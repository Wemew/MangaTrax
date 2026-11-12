#!/usr/bin/env python3
"""Multi-user identity, roles and legacy-data claim.

Covers:
  * IdP groups parsing (list / delimited / custom claim name) and session round-trip,
  * Admin resolution: none/basic are operator-admin, OIDC needs an admin group,
  * ``users`` role + policy flags persistence and the Admin panel helpers,
  * first-admin legacy claim (idempotent, copies series/blacklist/groups once).
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import auth as auth_module  # noqa: E402
from app.database import MangaDatabase  # noqa: E402
from app.auth import AuthConfig, AuthManager, Identity, SignedToken, SESSION_COOKIE_NAME  # noqa: E402


def _db(tmp_path: str) -> MangaDatabase:
    db = MangaDatabase(os.path.join(tmp_path, "identity.db"))
    db.init_db()
    return db


def test_groups_scope_requested():
    assert "groups" in auth_module.SCOPE.split(), auth_module.SCOPE


def test_stale_session_requires_relogin():
    config = AuthConfig(mode="oidc", session_secret="x" * 32, admin_groups=("admin",))
    manager = AuthManager(config)
    signer = auth_module.SignedToken(config.session_secret, 3600, "session")
    current = signer.sign({
        "sub": "oidc|red", "username": "red", "src": "oidc",
        "groups": ["admin"], "gv": auth_module.SESSION_GROUPS_VERSION,
    })
    stale = signer.sign({"sub": "oidc|red", "username": "red", "src": "oidc"})
    cookie = auth_module.SESSION_COOKIE_NAME
    assert manager.resolve_identity({}, {cookie: current}) is not None
    assert manager.resolve_identity({}, {cookie: stale}) is None, "pre-groups sessions must re-authenticate"


def test_group_normalisation():
    assert auth_module.normalize_groups(None) == ()
    assert auth_module.normalize_groups([]) == ()
    assert auth_module.normalize_groups(["admins", "admins", " Readers "]) == ("admins", "Readers")
    assert auth_module.normalize_groups("admins, readers  editors") == ("admins", "readers", "editors")
    assert auth_module.normalize_groups(["a", "A"]) == ("a",)


def test_identity_claim_and_session():
    claims = {
        "sub": "oidc|abc",
        "email": "red@example.com",
        "name": "Red",
        "groups": ["mangatrax-admins", "readers"],
    }
    identity = Identity.from_oidc_claims(claims)
    assert identity.groups == ("mangatrax-admins", "readers"), identity
    assert identity.actor == "Red"

    custom = Identity.from_oidc_claims(
        {"sub": "s", "roles": ["ops"]}, groups_claim="roles",
    )
    assert custom.groups == ("ops",), custom.groups

    # Session round-trip keeps the groups used for role decisions.
    token = SignedToken("x" * 32, 3600, "session").sign(identity.to_session())
    payload = SignedToken("x" * 32, 3600, "session").verify(token)
    session_identity = Identity.from_session(payload)
    assert session_identity is not None
    assert session_identity.groups == ("mangatrax-admins", "readers")


def test_admin_resolution():
    oidc = AuthConfig(
        mode="oidc", session_secret="x" * 32,
        admin_groups=("mangatrax-admins",),
    )
    manager = AuthManager(oidc)
    admin = Identity(subject="a", username="a", auth_source="oidc", groups=("MANGATRAX-ADMINS",))
    reader = Identity(subject="b", username="b", auth_source="oidc", groups=("readers",))
    nobody = Identity(subject="c", username="c", auth_source="oidc")
    assert manager.is_admin(admin) is True
    assert manager.is_admin(reader) is False
    assert manager.is_admin(nobody) is False
    assert manager.is_admin(None) is False

    # Group loss on a new login demotes (roles are recomputed from the IdP groups).
    demoted = Identity(subject="a", username="a", auth_source="oidc", groups=("readers",))
    assert manager.is_admin(demoted) is False

    # Single-operator deployments stay admin.
    assert AuthManager(AuthConfig(mode="none")).is_admin(None) is True
    basic = AuthManager(AuthConfig(mode="basic", basic_pass="pw"))
    assert basic.is_admin(Identity(subject="basic:admin", username="admin", auth_source="basic")) is True


def test_env_configuration():
    config = AuthConfig.from_env({
        "MANGATRAX_AUTH_MODE": "oidc",
        "MANGATRAX_SESSION_SECRET": "x" * 32,
        "MANGATRAX_ADMIN_GROUPS": "ops, manga-admins",
        "MANGATRAX_OIDC_GROUPS_CLAIM": "roles",
    })
    assert config.admin_groups == ("ops", "manga-admins"), config.admin_groups
    assert config.oidc_groups_claim == "roles"
    default = AuthConfig.from_env({})
    assert default.admin_groups == ("mangatrax-admins",)


def test_user_flags_and_legacy_claim(tmp_path):
    db = _db(tmp_path)

    admin = db.upsert_user(
        subject="oidc|red", username="red", email="red@example.com", display_name="Red",
        auth_source="oidc", groups=["mangatrax-admins"], is_admin=True,
    )
    reader = db.upsert_user(
        subject="oidc|blue", username="blue", auth_source="oidc", groups=[], is_admin=False,
    )
    assert admin["is_admin"] == 1 and reader["is_admin"] == 0
    assert "mangatrax-admins" in admin["groups_json"]

    updated = db.set_user_flags(reader["id"], auto_download=True, bypass_approval=True)
    assert updated["auto_download"] == 1 and updated["bypass_approval"] == 1
    untouched = db.set_user_flags(reader["id"], auto_download=False)
    assert untouched["auto_download"] == 0 and untouched["bypass_approval"] == 1

    listed = db.list_users()
    assert listed[0]["id"] == admin["id"], "admins sort first"

    # Legacy single-user data exists before multi-user is enabled.
    for name in ("Legacy A", "Legacy B"):
        db.track_discovered_series(name)
    db.add_to_blacklist("Legacy A", "not interested")
    db.add_prioritized_group("LegacyGroup", "notes", 2)
    legacy_groups = len(db.get_prioritized_groups())

    first = db.claim_legacy_data_for_admin(admin["id"])
    assert first["series"] == 2, first
    assert first["blacklist"] == 1, first
    assert first["groups"] == legacy_groups, (first, legacy_groups)
    assert len(db.get_user_series(admin["id"])) == 2
    assert len(db.get_user_blacklist(admin["id"])) == 1
    assert len(db.get_user_prioritized_groups(admin["id"])) == legacy_groups
    # New accounts start empty.
    assert db.get_user_series(reader["id"]) == []
    assert db.get_user_blacklist(reader["id"]) == []

    second = db.claim_legacy_data_for_admin(admin["id"])
    assert second["claimed"] == 0, "claim must be idempotent"


def main():
    test_groups_scope_requested()
    test_stale_session_requires_relogin()
    test_group_normalisation()
    test_identity_claim_and_session()
    test_admin_resolution()
    test_env_configuration()
    import tempfile
    tmp = tempfile.mkdtemp(prefix="mangatrax_multiuser_identity_")
    test_user_flags_and_legacy_claim(tmp)
    print("multi-user identity/roles suite PASSED")


if __name__ == "__main__":
    main()
