#!/usr/bin/env python3
"""Static typing-import audit (Python-version compatibility guard).

Docker runs Python 3.11, where function annotations are evaluated at import time.
Python 3.14 (the local/Calibre test interpreter) defers annotation evaluation, so a
missing ``from typing import List`` only crashes in production:

    NameError: name 'List' is not defined   at ``def _delete_failure_rows(rows: List[...])``

This suite parses every backend module and fails when an annotation uses a typing
construct that the module never imported. It is version-independent and therefore
catches exactly the class of error that only appears on the Docker interpreter.
"""

import ast
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_APP = os.path.join(BASE_DIR, "backend", "app")

TYPING_CONSTRUCTS = {
    "List", "Set", "Tuple", "Dict", "Any", "Optional", "Union", "Callable",
    "Iterable", "Sequence", "Mapping", "Awaitable", "Iterator", "Generator",
    "Type", "FrozenSet", "AbstractSet", "MutableMapping", "MutableSequence",
    "Coroutine", "AsyncIterator", "Literal", "TypedDict", "Protocol", "NamedTuple",
}


def _imported_typing_names(tree):
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "typing":
            for alias in node.names:
                if alias.name == "*":
                    return {"*"}
                imported.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "typing":
                    imported.add("typing")
    return imported


def _annotation_typing_names(tree):
    used = set()

    def collect(annotation):
        if annotation is None:
            return
        for child in ast.walk(annotation):
            if isinstance(child, ast.Name):
                used.add(child.id)
            # typing.List[...] / list[...] style: subscripted attribute is fine
            if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) and child.value.id == "typing":
                used.add("typing")

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = (
                list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
                + [node.args.vararg, node.args.kwarg]
            )
            for arg in args:
                if arg is not None:
                    collect(arg.annotation)
            collect(node.returns)
        elif isinstance(node, ast.AnnAssign):
            collect(node.annotation)
        elif isinstance(node, ast.Lambda):
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                collect(arg.annotation)
    return used


def _modules():
    for root, _dirs, files in os.walk(BACKEND_APP):
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(root, name)


def test_no_missing_typing_imports():
    problems = []
    for path in _modules():
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            problems.append(f"{os.path.relpath(path, BASE_DIR)}: SyntaxError: {exc}")
            continue
        imported = _imported_typing_names(tree)
        if "*" in imported:
            continue
        used = _annotation_typing_names(tree)
        missing = sorted((used & TYPING_CONSTRUCTS) - imported)
        if missing:
            problems.append(f"{os.path.relpath(path, BASE_DIR)}: missing typing imports {missing}")
    assert not problems, (
        "annotations use typing names that are not imported (crashes on Python 3.11 Docker):\n"
        + "\n".join(problems)
    )
    print("    [PASSED] every annotated typing construct is imported in its module.")


def test_main_imports_list_regression():
    path = os.path.join(BACKEND_APP, "main.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert "from typing import Dict, Any, Optional, List" in source, (
        "main.py must import List: Docker (Python 3.11) evaluates annotations at import time"
    )
    assert "def _delete_failure_rows(rows: List[Dict[str, Any]]" in source
    print("    [PASSED] main.py imports List (the production NameError regression).")


def run():
    test_no_missing_typing_imports()
    test_main_imports_list_regression()
    print("ALL TYPING-IMPORT TESTS PASSED")


if __name__ == "__main__":
    run()
