"""Pydantic v2 request-validation models for every mutating MangaTrax endpoint.

M3 feature 19.  All models use ``extra="forbid"`` so unknown keys are rejected
instead of being silently persisted (previously ``POST /api/settings`` stored
whatever JSON the caller sent, and most endpoints trusted ``payload.get(...)``).

Embedded-interpreter compatibility
----------------------------------
The application itself ships with Pydantic v2 (``backend/requirements.txt``).
The repository's test suites, however, execute inside Calibre's embedded
Python interpreter (``calibre-debug.exe -e``) where *pydantic, fastapi, httpx
and jinja2 are not installed* - the E2E harness already shims FastAPI for the
same reason.  To keep ``app.main`` importable in that interpreter, this module
falls back to a small, deliberately limited validation layer when Pydantic is
unavailable.  The fallback implements exactly the subset used below:
required/default fields, ``str``/``int``/``float``/``bool``/``list``/``dict``/
``Optional``/``Literal`` handling, ``ge``/``le``/``min_length``/``max_length``/
``pattern`` constraints, per-field validators, unknown-key rejection and
``model_dump(exclude_unset=True)``.  It performs no coercion beyond the usual
numeric/bool strings, which keeps its behaviour close to Pydantic's lax mode.
Production deployments always use real Pydantic v2.
"""

from __future__ import annotations

import re
import typing
from typing import Any, Dict, Literal, Optional, Tuple, Union

from app import security

__all__ = [
    "PYDANTIC_AVAILABLE",
    "ValidationError",
    "FeedCreateRequest",
    "FeedTestRequest",
    "ReleaseStatusUpdateRequest",
    "SeriesLinkUpdateRequest",
    "SeriesBackfillRequest",
    "SeriesBackfillByNameRequest",
    "BlacklistCreateRequest",
    "DiscoveryTrackRequest",
    "DiscoveryBlacklistRequest",
    "PrioritizedGroupCreateRequest",
    "SettingsUpdateRequest",
    "DownloaderConfigureRequest",
    "DownloaderTestRequest",
    "WebhookTestRequest",
    "BackfillStartRequest",
    "DebugActionRequest",
    "validate",
    "validation_error_messages",
]

try:  # pragma: no cover - branch depends on the interpreter
    from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

    PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover - Calibre embedded interpreter
    PYDANTIC_AVAILABLE = False

    _UNSET = object()

    class ValidationError(ValueError):
        """Minimal stand-in for ``pydantic.ValidationError``."""

        def __init__(self, errors):
            self._errors = list(errors)
            summary = "; ".join(f"{'.'.join(str(p) for p in err.get('loc', ()))}: {err.get('msg', '')}" for err in self._errors)
            super().__init__(summary or "validation failed")

        def errors(self):
            return list(self._errors)

    class ConfigDict(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    class _FieldInfo:
        def __init__(self, default=_UNSET, default_factory=None, ge=None, le=None,
                     min_length=None, max_length=None, pattern=None, **kwargs):
            self.default = default
            self.default_factory = default_factory
            self.ge = ge
            self.le = le
            self.min_length = min_length
            self.max_length = max_length
            self.pattern = pattern

    def Field(default=_UNSET, *, default_factory=None, ge=None, le=None, min_length=None,
              max_length=None, pattern=None, **kwargs):
        return _FieldInfo(default, default_factory, ge=ge, le=le, min_length=min_length,
                          max_length=max_length, pattern=pattern)

    def field_validator(*field_names):
        def decorator(func):
            target = func.__func__ if isinstance(func, classmethod) else func
            target.__validator_fields__ = tuple(field_names)
            return func
        return decorator

    class _ModelMeta(type):
        def __new__(mcls, name, bases, namespace):
            cls = super().__new__(mcls, name, bases, namespace)
            if name == "BaseModel":
                return cls
            specs = {}
            for base in reversed(cls.__mro__[1:]):
                specs.update(getattr(base, "_field_specs", {}) or {})
            annotations = namespace.get("__annotations__", {})
            for field_name, annotation in annotations.items():
                if field_name.startswith("_"):
                    continue
                raw = namespace.get(field_name, _UNSET)
                info = raw if isinstance(raw, _FieldInfo) else None
                default = info.default if info is not None else raw
                specs[field_name] = (annotation, default, info)
            cls._field_specs = specs
            # ``from __future__ import annotations`` makes annotations strings;
            # resolve them against the module namespace before validation.
            try:
                hints = typing.get_type_hints(cls)
            except Exception:  # pragma: no cover - unresolvable forward refs
                hints = {}
            for hint_name, hint_value in hints.items():
                if hint_name in specs and hint_value is not None:
                    specs[hint_name] = (hint_value, specs[hint_name][1], specs[hint_name][2])
            validators = []
            for base in reversed(cls.__mro__[1:]):
                validators.extend(getattr(base, "_validator_specs", []) or [])
            for attr_name, attr in namespace.items():
                target = attr.__func__ if isinstance(attr, classmethod) else attr
                targets = getattr(target, "__validator_fields__", None)
                if targets:
                    for target_name in targets:
                        validators.append((target_name, attr_name))
            cls._validator_specs = validators
            return cls

    class BaseModel(metaclass=_ModelMeta):
        model_config: Dict[str, Any] = {}

        @classmethod
        def model_validate(cls, data):
            if isinstance(data, cls):
                return data
            return cls._validate_payload(data if data is not None else {})

        @classmethod
        def _validate_payload(cls, data):
            errors = []
            if not isinstance(data, dict):
                raise ValidationError([{"loc": (), "msg": "Input should be an object", "type": "model_type"}])
            config = cls.model_config or {}
            extra_forbid = config.get("extra") == "forbid"
            known = set(cls._field_specs.keys())
            if extra_forbid:
                for key in data:
                    if key not in known:
                        errors.append({"loc": (key,), "msg": "Extra inputs are not permitted", "type": "extra_forbidden"})

            values = {}
            provided = set()
            for field_name, (annotation, default, info) in cls._field_specs.items():
                if field_name in data:
                    raw = data[field_name]
                    provided.add(field_name)
                elif info is not None and info.default_factory is not None:
                    raw = info.default_factory()
                elif default is not _UNSET:
                    raw = default
                else:
                    errors.append({"loc": (field_name,), "msg": "Field required", "type": "missing"})
                    continue
                try:
                    value = _check_value(annotation, raw, info, field_name)
                except ValidationError as exc:
                    errors.extend(exc.errors())
                    continue
                values[field_name] = value

            for target_name, validator_name in cls._validator_specs:
                if target_name not in values:
                    continue
                validator = getattr(cls, validator_name)
                try:
                    values[target_name] = validator(values[target_name])
                except ValidationError as exc:
                    errors.extend(exc.errors())
                except (ValueError, TypeError) as exc:
                    errors.append({"loc": (target_name,), "msg": str(exc), "type": "value_error"})

            if errors:
                raise ValidationError(errors)

            instance = cls.__new__(cls)
            instance.__dict__.update(values)
            instance._provided_fields = provided
            return instance

        def model_dump(self, *, exclude_unset=False, exclude_none=False, **kwargs):
            result = {}
            provided = getattr(self, "_provided_fields", set(self.__dict__.keys()))
            for key, value in self.__dict__.items():
                if key.startswith("_"):
                    continue
                if exclude_unset and key not in provided:
                    continue
                if exclude_none and value is None:
                    continue
                result[key] = value
            return result

    import types as _types

    _UNION_TYPE = getattr(_types, "UnionType", None)

    def _is_optional(annotation):
        origin = typing.get_origin(annotation)
        if origin is Union or (_UNION_TYPE is not None and origin is _UNION_TYPE):
            args = typing.get_args(annotation)
            if type(None) in args:
                remaining = [arg for arg in args if arg is not type(None)]
                if len(remaining) == 1:
                    return True, remaining[0]
        return False, annotation

    def _check_value(annotation, value, info, field_name):
        optional, inner = _is_optional(annotation)
        if optional and value is None:
            return None

        origin = typing.get_origin(inner)
        if origin is Union or (_UNION_TYPE is not None and origin is _UNION_TYPE):
            # Union[...] (e.g. Optional[Union[bool, str]]): first matching member wins.
            for member in typing.get_args(inner):
                try:
                    return _check_value(member, value, info, field_name)
                except ValidationError:
                    continue
            raise ValidationError([{
                "loc": (field_name,),
                "msg": "Input does not match any allowed type",
                "type": "union_error",
            }])
        if origin is Literal:
            allowed = typing.get_args(inner)
            if value not in allowed:
                raise ValidationError([{
                    "loc": (field_name,),
                    "msg": f"Input should be one of {list(allowed)}",
                    "type": "literal_error",
                }])
            return value
        if origin in (list, typing.List):
            if not isinstance(value, list):
                raise ValidationError([{"loc": (field_name,), "msg": "Input should be a list", "type": "list_type"}])
            item_args = typing.get_args(inner)
            if item_args:
                return [_check_value(item_args[0], item, None, field_name) for item in value]
            return value
        if origin in (dict, typing.Dict):
            if not isinstance(value, dict):
                raise ValidationError([{"loc": (field_name,), "msg": "Input should be an object", "type": "dict_type"}])
            return dict(value)
        if inner is str:
            if not isinstance(value, str):
                raise ValidationError([{"loc": (field_name,), "msg": "Input should be a string", "type": "string_type"}])
        elif inner is int:
            if isinstance(value, bool):
                raise ValidationError([{"loc": (field_name,), "msg": "Input should be an integer", "type": "int_type"}])
            if isinstance(value, int):
                pass
            elif isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
                value = int(value.strip())
            else:
                raise ValidationError([{"loc": (field_name,), "msg": "Input should be an integer", "type": "int_type"}])
        elif inner is float:
            if isinstance(value, bool):
                raise ValidationError([{"loc": (field_name,), "msg": "Input should be a number", "type": "float_type"}])
            if isinstance(value, (int, float)):
                value = float(value)
            elif isinstance(value, str):
                try:
                    value = float(value.strip())
                except ValueError:
                    raise ValidationError([{"loc": (field_name,), "msg": "Input should be a number", "type": "float_type"}])
            else:
                raise ValidationError([{"loc": (field_name,), "msg": "Input should be a number", "type": "float_type"}])
        elif inner is bool:
            if isinstance(value, bool):
                pass
            elif isinstance(value, str) and value.strip().lower() in ("true", "1", "yes", "on"):
                value = True
            elif isinstance(value, str) and value.strip().lower() in ("false", "0", "no", "off"):
                value = False
            elif isinstance(value, int) and value in (0, 1):
                value = bool(value)
            else:
                raise ValidationError([{"loc": (field_name,), "msg": "Input should be a boolean", "type": "bool_type"}])
        elif inner is Any or inner is object:
            pass
        else:
            if not isinstance(value, inner):
                raise ValidationError([{"loc": (field_name,), "msg": f"Input should be a {inner}", "type": "type_error"}])

        if info is not None:
            if info.ge is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and value < info.ge:
                raise ValidationError([{"loc": (field_name,), "msg": f"Input should be greater than or equal to {info.ge}", "type": "greater_than_equal"}])
            if info.le is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and value > info.le:
                raise ValidationError([{"loc": (field_name,), "msg": f"Input should be less than or equal to {info.le}", "type": "less_than_equal"}])
            if info.min_length is not None and hasattr(value, "__len__") and len(value) < info.min_length:
                raise ValidationError([{"loc": (field_name,), "msg": f"Input should have at least {info.min_length} characters", "type": "string_too_short"}])
            if info.max_length is not None and hasattr(value, "__len__") and len(value) > info.max_length:
                raise ValidationError([{"loc": (field_name,), "msg": f"Input should have at most {info.max_length} characters", "type": "string_too_long"}])
            if info.pattern is not None and isinstance(value, str) and not re.search(info.pattern, value):
                raise ValidationError([{"loc": (field_name,), "msg": "Input should match the required pattern", "type": "string_pattern_mismatch"}])
        return value


# ---------------------------------------------------------------------------
# Shared constants / helpers
# ---------------------------------------------------------------------------
FEED_TYPES = ("rss", "html", "html_scraper", "nyaa", "nyaa_scraper", "nyaa_manga")
RELEASE_STATUSES = ("REQUESTED", "IN_CALIBRE", "IGNORED", "BLACKLISTED", "DISCOVERY")
DEBUG_ACTIONS = (
    "clear-discovery",
    "clear-descriptions",
    "clear-thumbnails",
    "normalize-english-titles",
    "reset-all-data",
)
SETTINGS_KEYS = (
    "calibre_path",
    "qbit_enabled",
    "qbit_url",
    "qbit_username",
    "qbit_password",
    "qbit_category",
    "qbit_tags",
    "staging_dir",
    "auto_download",
    "auto_tag",
    "calibre_sync_interval_minutes",
    "feed_check_interval_minutes",
    "backfill_max_pages",
    "backfill_delay_seconds",
    "app_timezone",
    "discord_webhook_url",
    "apprise_url",
)


def _clean_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


def _validate_http_url(value: str) -> str:
    """Structural http(s) URL validation (no DNS) for persisted feed URLs."""
    if not isinstance(value, str):
        raise ValueError("must be a string")
    if not value.strip():
        raise ValueError("URL is required")
    try:
        return security.validate_outbound_url(value)
    except security.UnsafeURLError as exc:
        raise ValueError(str(exc)) from exc


def _validate_strict_http_url(value: str) -> str:
    """Strict (DNS re-checked) http(s) URL validation for user-probed URLs."""
    if not isinstance(value, str):
        raise ValueError("must be a string")
    if not value.strip():
        raise ValueError("URL is required")
    try:
        return security.validate_public_url(value)
    except security.UnsafeURLError as exc:
        raise ValueError(str(exc)) from exc


def _validate_directory_value(setting_name: str):
    def _validator(value: str) -> str:
        if value is None:
            return value
        try:
            security.validate_directory_setting(value, setting_name=setting_name)
        except security.UnsafePathError as exc:
            raise ValueError(str(exc)) from exc
        return value
    return _validator


def validate(model_cls, payload: Any):
    """Validate *payload* against *model_cls* using whichever backend is available."""
    data = {} if payload is None else payload
    return model_cls.model_validate(data)


def validation_error_messages(exc: "ValidationError") -> str:
    """Flatten a validation error into a single human-readable message."""
    try:
        errors = exc.errors()
    except Exception:  # pragma: no cover - defensive
        return str(exc)
    parts = []
    for error in errors:
        location = ".".join(str(part) for part in error.get("loc", ()) or ())
        message = error.get("msg", "invalid value")
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or "validation failed"


# ---------------------------------------------------------------------------
# Feed models
# ---------------------------------------------------------------------------
class FeedCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=security.MAX_URL_LENGTH)
    feed_type: Literal["rss", "html", "html_scraper", "nyaa", "nyaa_scraper", "nyaa_manga"] = "rss"
    interval: int = Field(default=60, ge=5, le=1440)
    auto_download: bool = True
    config: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value):
        return _clean_text(value)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value):
        return _validate_http_url(value)


class FeedTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=security.MAX_URL_LENGTH)
    feed_type: str = Field(default="rss", max_length=40)
    config: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value):
        # User-triggered live probe: strict SSRF validation (DNS re-checked).
        return _validate_strict_http_url(value)


# ---------------------------------------------------------------------------
# Releases / series models
# ---------------------------------------------------------------------------
class ReleaseStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["REQUESTED", "IN_CALIBRE", "IGNORED", "BLACKLISTED", "DISCOVERY"]


class SeriesLinkUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    website_url: str = Field(default="", max_length=security.MAX_URL_LENGTH)

    @field_validator("website_url")
    @classmethod
    def _validate_website_url(cls, value):
        try:
            return security.validate_stored_website_url(value)
        except security.UnsafeURLError as exc:
            raise ValueError(str(exc)) from exc


class SeriesBackfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_pages: int = Field(default=3, ge=1, le=500)


class SeriesBackfillByNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    series_name: str = Field(min_length=1, max_length=500)
    max_pages: int = Field(default=3, ge=1, le=500)

    @field_validator("series_name")
    @classmethod
    def _clean_series_name(cls, value):
        return _clean_text(value)


class BlacklistCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=500)
    reason: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value):
        return _clean_text(value)


class DiscoveryTrackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    series_name: str = Field(min_length=1, max_length=500)
    release_id: Optional[int] = Field(default=None, ge=1)

    @field_validator("series_name")
    @classmethod
    def _clean_series_name(cls, value):
        return _clean_text(value)


class DiscoveryBlacklistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    series_name: str = Field(min_length=1, max_length=500)
    reason: str = Field(default="Blacklisted from Discovery panel", max_length=2000)

    @field_validator("series_name")
    @classmethod
    def _clean_series_name(cls, value):
        return _clean_text(value)


class PrioritizedGroupCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=2000)
    priority: int = Field(default=1, ge=0, le=100)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value):
        return _clean_text(value)


# ---------------------------------------------------------------------------
# Settings / downloader / webhook models
# ---------------------------------------------------------------------------
class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calibre_path: Optional[str] = Field(default=None, max_length=security.MAX_PATH_LENGTH)
    qbit_enabled: Optional[str] = Field(default=None, max_length=20)
    qbit_url: Optional[str] = Field(default=None, max_length=security.MAX_URL_LENGTH)
    qbit_username: Optional[str] = Field(default=None, max_length=200)
    qbit_password: Optional[str] = Field(default=None, max_length=500)
    qbit_category: Optional[str] = Field(default=None, max_length=100)
    qbit_tags: Optional[str] = Field(default=None, max_length=200)
    staging_dir: Optional[str] = Field(default=None, max_length=security.MAX_PATH_LENGTH)
    auto_download: Optional[str] = Field(default=None, max_length=20)
    auto_tag: Optional[str] = Field(default=None, max_length=20)
    calibre_sync_interval_minutes: Optional[str] = Field(default=None, max_length=20)
    feed_check_interval_minutes: Optional[str] = Field(default=None, max_length=20)
    backfill_max_pages: Optional[str] = Field(default=None, max_length=20)
    backfill_delay_seconds: Optional[str] = Field(default=None, max_length=20)
    app_timezone: Optional[str] = Field(default=None, max_length=100)
    discord_webhook_url: Optional[str] = Field(default=None, max_length=security.MAX_URL_LENGTH)
    apprise_url: Optional[str] = Field(default=None, max_length=security.MAX_URL_LENGTH)

    @field_validator("calibre_path")
    @classmethod
    def _validate_calibre_path(cls, value):
        return _validate_directory_value("calibre_path")(value)

    @field_validator("staging_dir")
    @classmethod
    def _validate_staging_dir(cls, value):
        return _validate_directory_value("staging_dir")(value)

    @field_validator("discord_webhook_url", "apprise_url")
    @classmethod
    def _validate_optional_webhook(cls, value):
        if value is None or not str(value).strip():
            return "" if value is not None else None
        return _validate_http_url(str(value).strip())

    @field_validator("qbit_url")
    @classmethod
    def _validate_qbit_url(cls, value):
        if value is None or not str(value).strip():
            return value
        candidate = str(value).strip()
        # The downloader is intentionally local/internal (qBittorrent on the LAN),
        # so only the scheme is enforced here - no public-IP requirement.
        parsed = security.urllib.parse.urlsplit(candidate)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            raise ValueError("qbit_url must be an absolute http:// or https:// URL")
        return candidate


class DownloaderConfigureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qbit_url: Optional[str] = Field(default=None, max_length=security.MAX_URL_LENGTH)
    qbit_username: Optional[str] = Field(default=None, max_length=200)
    qbit_password: Optional[str] = Field(default=None, max_length=500)
    qbit_enabled: Optional[Union[bool, str]] = None
    qbit_category: Optional[str] = Field(default=None, max_length=100)
    qbit_tags: Optional[str] = Field(default=None, max_length=200)


class DownloaderTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qbit_url: Optional[str] = Field(default=None, max_length=security.MAX_URL_LENGTH)
    qbit_username: Optional[str] = Field(default=None, max_length=200)
    qbit_password: Optional[str] = Field(default=None, max_length=500)
    qbit_enabled: Optional[Union[bool, str]] = None


class WebhookTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discord_webhook_url: Optional[str] = Field(default=None, max_length=security.MAX_URL_LENGTH)
    generic_webhook_url: Optional[str] = Field(default=None, max_length=security.MAX_URL_LENGTH)
    apprise_url: Optional[str] = Field(default=None, max_length=security.MAX_URL_LENGTH)

    @field_validator("discord_webhook_url", "generic_webhook_url", "apprise_url")
    @classmethod
    def _validate_webhook_url(cls, value):
        if value is None or not str(value).strip():
            return None
        # Structural SSRF guard: blocks loopback/metadata literals and non-http(s)
        # schemes while still permitting self-hosted (LAN) Apprise endpoints.
        return _validate_http_url(str(value).strip())


# ---------------------------------------------------------------------------
# Backfill / debug models
# ---------------------------------------------------------------------------
class BackfillStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_pages: Optional[int] = Field(default=None, ge=0, le=5000)
    delay: Optional[float] = Field(default=None, ge=0, le=300)
    start_page: Optional[int] = Field(default=None, ge=1)


class DebugActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "clear-discovery",
        "clear-descriptions",
        "clear-thumbnails",
        "normalize-english-titles",
        "reset-all-data",
    ]
