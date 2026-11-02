#!/usr/bin/env python3
"""
MangaTrax assert-aware test runner (the ONLY sanctioned entry point for the
script-style suites in tests/).

WHY THIS EXISTS
---------------
``& "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e tests\\test_foo.py``
runs Python with ``optimize=2`` (``sys.flags.optimize == 2``, ``__debug__ is False``).
CPython removes every bare ``assert`` statement at compile time when optimize >= 1,
so running a suite directly under calibre-debug turns all of its assertions into
no-ops while the script still prints "ALL TESTS PASSED" and exits 0.  That is exactly
how the earlier "100% verified" figures in TEST_READY.md were produced; they were void.

This runner reads each suite's source and recompiles it with ``optimize=0``, then
executes it in a fresh module namespace with ``__name__ == "__main__"`` (so the
suite's own ``if __name__ == "__main__":`` block runs).  Assertions therefore really
execute.  Any AssertionError / exception marks the suite FAIL, is printed in full,
and forces a non-zero process exit code.

USAGE
-----
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
        -> run every tests/test_*.py script-style suite

    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py" tests\\test_mangatrax_workflow.py
        -> run one suite

    ... tests\\run_tests.py --list
        -> print the discovered suite list

The unittest-based end-to-end suite is NOT run here (it genuinely executes its own
assertions); run it separately:
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\e2e\\run_all_e2e.py"

Stdlib only.  No test framework dependency.
"""

import ast
import builtins
import glob
import os
import sys
import time
import traceback

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)

EXECUTED_SUITE_FILENAME_FILTER = None  # set per suite (kept for clarity in counter)

_MONITORING_TOOL_NAME = "mangatrax-assert-runner"
_MONITORING_TOOL_IDS = (4, 3, 2, 5, 0, 1)


# ---------------------------------------------------------------------------
# Assertion instrumentation
# ---------------------------------------------------------------------------
def collect_assert_lines(source, filename):
    """Return {line_number: number_of_assert_statements_on_that_line}."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        # Let the real compile() below produce the canonical error message.
        return {}
    lines = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            lines[node.lineno] = lines.get(node.lineno, 0) + 1
    return lines


def iter_code_objects(code):
    """Yield ``code`` and every nested code object (functions, comprehensions...)."""
    yield code
    for const in code.co_consts:
        if isinstance(const, type(code)):
            yield from iter_code_objects(const)


_SOURCE_LINE_CACHE = {}


def read_source_line(filename, lineno):
    """Best-effort one-line source read, used to make failures self-explanatory.

    (This interpreter build does not render source lines in tracebacks, so the
    runner prints the failing statement itself.)
    """
    key = (filename, lineno)
    if key in _SOURCE_LINE_CACHE:
        return _SOURCE_LINE_CACHE[key]
    line = None
    try:
        with open(filename, "r", encoding="utf-8") as handle:
            for index, text in enumerate(handle, start=1):
                if index == lineno:
                    line = text.strip()
                    break
    except OSError:
        line = None
    _SOURCE_LINE_CACHE[key] = line
    return line


def format_failure(exc):
    """Render a failure with an explicit location, statement and message.

    Tracebacks in this embedded interpreter omit the source line, so we print the
    deepest frame's file:line and its source statement ourselves.  The full
    traceback is still appended (nothing is swallowed).
    """
    lines = []
    deepest = None
    tb = exc.__traceback__
    while tb is not None:
        deepest = (tb.tb_frame.f_code.co_filename, tb.tb_lineno)
        tb = tb.tb_next
    if deepest:
        filename, lineno = deepest
        try:
            shown = os.path.relpath(filename, REPO_ROOT)
        except ValueError:
            shown = filename
        lines.append(f"  failed at : {shown}:{lineno}")
        statement = read_source_line(filename, lineno)
        if statement:
            lines.append(f"  statement : {statement}")
    if isinstance(exc, AssertionError):
        lines.append(f"  message   : {str(exc) or '(no message on the assert)'}")
    else:
        lines.append(f"  exception : {type(exc).__name__}: {exc}")
    lines.append("")
    lines.append(traceback.format_exc())
    return "\n".join(lines)


class AssertCounter:
    """Counts *executed* ``assert`` statements using sys.monitoring line events.

    sys.monitoring (PEP 669) is available on Python >= 3.12, which is what calibre
    ships.  Line events are enabled only for the suite's own code objects (local
    events), so backend modules are not slowed down and timing assertions are not
    distorted.  If the interpreter lacks sys.monitoring, ``available`` is False and
    only the static (compiled-live) assert count is reported.
    """

    def __init__(self):
        self.available = hasattr(sys, "monitoring")
        self.tool_id = None
        self.executed = 0
        self._assert_lines = {}
        self._instrumented = []
        if not self.available:
            return
        monitoring = sys.monitoring
        for candidate in _MONITORING_TOOL_IDS:
            try:
                monitoring.use_tool_id(candidate, _MONITORING_TOOL_NAME)
            except ValueError:
                continue
            self.tool_id = candidate
            break
        if self.tool_id is None:
            self.available = False

    def _on_line(self, code, line_number):
        multiplicity = self._assert_lines.get(line_number)
        if multiplicity:
            self.executed += multiplicity

    def start(self, code, assert_lines):
        self._assert_lines = assert_lines
        self.executed = 0
        self._instrumented = []
        if not self.available:
            return
        monitoring = sys.monitoring
        monitoring.register_callback(self.tool_id, monitoring.events.LINE, self._on_line)
        filename = code.co_filename
        for nested in iter_code_objects(code):
            if nested.co_filename != filename:
                continue
            try:
                monitoring.set_local_events(self.tool_id, nested, monitoring.events.LINE)
                self._instrumented.append(nested)
            except Exception:
                # Counting is diagnostic; never let it break a suite run.
                pass

    def stop(self):
        if not self.available:
            return
        monitoring = sys.monitoring
        for nested in self._instrumented:
            try:
                monitoring.set_local_events(self.tool_id, nested, 0)
            except Exception:
                pass
        self._instrumented = []
        try:
            monitoring.register_callback(self.tool_id, monitoring.events.LINE, None)
        except Exception:
            pass

    def release(self):
        if not self.available or self.tool_id is None:
            return
        try:
            sys.monitoring.free_tool_id(self.tool_id)
        except Exception:
            pass
        self.tool_id = None


# ---------------------------------------------------------------------------
# Suite execution
# ---------------------------------------------------------------------------
class SuiteResult:
    def __init__(self, path):
        self.path = path
        self.ok = False
        self.live_asserts = 0
        self.executed_asserts = 0
        self.executed_known = True
        self.duration = 0.0
        self.error = None           # formatted traceback, if any
        self.failure_kind = None    # "assertion" | "exception" | "exit"
        self.exit_code = None


def run_suite(path, counter):
    """Compile ``path`` with optimize=0 and exec it as a ``__main__`` script."""
    result = SuiteResult(path)
    normalized = os.path.normcase(os.path.abspath(path))

    try:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
    except OSError as exc:
        result.error = f"could not read suite {path}: {exc!r}"
        result.failure_kind = "exception"
        return result

    assert_lines = collect_assert_lines(source, path)
    result.live_asserts = sum(assert_lines.values())

    # The key step: compile the *source* ourselves with optimize=0 so the assert
    # statements the interpreter would otherwise strip are kept in the bytecode.
    try:
        code = compile(source, path, "exec", optimize=0)
    except SyntaxError:
        result.error = traceback.format_exc()
        result.failure_kind = "exception"
        return result

    namespace = {
        "__name__": "__main__",       # make the suite's own runner block execute
        "__file__": os.path.abspath(path),
        "__package__": None,
        "__spec__": None,
        "__loader__": None,
        "__cached__": None,
        "__doc__": None,
        "__builtins__": builtins,
    }
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    if TESTS_DIR not in sys.path:
        sys.path.insert(0, TESTS_DIR)

    saved_argv = sys.argv
    sys.argv = [os.path.abspath(path)]
    started = time.time()
    try:
        if not counter.available:
            result.executed_known = False
        counter.start(code, assert_lines)
        exec(code, namespace)
        result.ok = True
    except SystemExit as exc:
        code_value = exc.code
        result.exit_code = code_value
        if code_value in (0, None):
            result.ok = True
        else:
            result.ok = False
            result.failure_kind = "exit"
            result.error = (
                f"suite called sys.exit({code_value!r}) -- treated as FAILURE so a suite "
                "cannot fake success by exiting\n"
            )
    except AssertionError as exc:
        result.ok = False
        result.failure_kind = "assertion"
        result.error = format_failure(exc)
    except BaseException as exc:
        result.ok = False
        result.failure_kind = "exception"
        result.error = format_failure(exc)
    finally:
        counter.stop()
        sys.argv = saved_argv
        result.duration = time.time() - started
        result.executed_asserts = counter.executed

    # Guard against silently losing the instrumentation: if the file has live
    # asserts but none were observed and monitoring is supposed to be active,
    # report it loudly (it means the count is not trustworthy).
    if result.ok and counter.available and result.live_asserts and result.executed_asserts == 0:
        print(
            "  [runner warning] {0} assert statement(s) compiled live but none were "
            "observed executing; the executed-count is unreliable for this suite.".format(
                result.live_asserts
            )
        )
    return result


# ---------------------------------------------------------------------------
# Discovery / CLI
# ---------------------------------------------------------------------------
def discover_suites():
    pattern = os.path.join(TESTS_DIR, "test_*.py")
    return [p for p in sorted(glob.glob(pattern)) if os.path.isfile(p)]


def resolve_suite_argument(argument):
    """Resolve a CLI suite argument relative to cwd, repo root, or tests dir."""
    candidates = []
    if os.path.isabs(argument):
        candidates.append(argument)
    else:
        candidates.append(os.path.abspath(argument))
        candidates.append(os.path.join(REPO_ROOT, argument))
        candidates.append(os.path.join(TESTS_DIR, argument))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def main(argv):
    arguments = list(argv[1:])
    show_list = False
    suites = []
    unknown_options = []

    for argument in arguments:
        if argument in ("--list", "-l"):
            show_list = True
        elif argument in ("--help", "-h"):
            print(__doc__)
            return 0
        elif argument.startswith("-"):
            unknown_options.append(argument)
        else:
            resolved = resolve_suite_argument(argument)
            if resolved is None:
                print(f"run_tests: no such suite file: {argument}", file=sys.stderr)
                return 2
            if resolved not in suites:
                suites.append(resolved)

    if unknown_options:
        print(f"run_tests: unknown option(s): {' '.join(unknown_options)}", file=sys.stderr)
        return 2

    if not suites:
        suites = discover_suites()

    if show_list:
        for suite in suites:
            print(os.path.relpath(suite, REPO_ROOT))
        return 0

    if not suites:
        print("run_tests: no suites found (looked for tests/test_*.py)", file=sys.stderr)
        return 2

    print("=" * 78)
    print("MangaTrax assert-aware test runner")
    print("=" * 78)
    print(f"  interpreter      : Python {sys.version.split()[0]}")
    print(f"  sys.flags.optimize: {sys.flags.optimize}  (calibre-debug -e)")
    print(f"  __debug__        : {__debug__}")
    print(
        "  native asserts   : "
        + ("DISABLED" if not __debug__ else "enabled")
        + " -> every suite below is recompiled with optimize=0"
    )
    print(f"  suites           : {len(suites)}")
    print(f"  repo root        : {REPO_ROOT}")
    print("=" * 78)

    counter = AssertCounter()
    if counter.available:
        print(f"  executed-assert counting: sys.monitoring tool id {counter.tool_id}")
    else:
        print("  executed-assert counting: UNAVAILABLE (static live count only)")
    print("=" * 78)

    results = []
    started_all = time.time()
    try:
        for index, suite in enumerate(suites, start=1):
            relative = os.path.relpath(suite, REPO_ROOT)
            print()
            print("-" * 78)
            print(f"[{index}/{len(suites)}] RUN  {relative}")
            print("-" * 78)
            result = run_suite(suite, counter)
            results.append(result)

            status = "PASS" if result.ok else "FAIL"
            counts = f"assert statements live: {result.live_asserts}"
            if counter.available:
                counts += f", executions: {result.executed_asserts}"
            print("-" * 78)
            print(f"[{index}/{len(suites)}] {status}  {relative}  ({result.duration:.2f}s)  {counts}")
            if result.live_asserts == 0:
                print(
                    "  note: no bare `assert` statements in this suite "
                    "(it must use explicit raise-based checks, e.g. a _check() helper); "
                    "failures still surface as exceptions and fail the run."
                )
            if not result.ok:
                if result.failure_kind == "assertion":
                    print("  ^ an assertion was executed and FAILED (asserts are live)", file=sys.stderr)
                print("-" * 78, file=sys.stderr)
                print(f"FAILURE DETAIL: {relative}", file=sys.stderr)
                print("-" * 78, file=sys.stderr)
                print(result.error, file=sys.stderr)
                print("-" * 78, file=sys.stderr)
            print("-" * 78)
    finally:
        counter.release()

    total_duration = time.time() - started_all
    passed = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    total_live = sum(r.live_asserts for r in results)
    total_executed = sum(r.executed_asserts for r in results)

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for result in results:
        relative = os.path.relpath(result.path, REPO_ROOT)
        status = "PASS" if result.ok else "FAIL"
        counts = f"{result.live_asserts} live"
        if counter.available:
            counts += f", {result.executed_asserts} executions"
        print(f"  {status}  {relative:<58} {counts}")
    print("-" * 78)
    print(f"  suites run      : {len(results)}")
    print(f"  suites passed   : {len(passed)}")
    print(f"  suites failed   : {len(failed)}")
    print(f"  assert statements compiled live (optimize=0): {total_live}")
    if counter.available:
        print(f"  assert executions observed (sys.monitoring): {total_executed}")
    print(f"  wall time       : {total_duration:.2f}s")
    if failed:
        print("  FAILED SUITES   : " + ", ".join(os.path.relpath(r.path, REPO_ROOT) for r in failed))
        print("=" * 78)
        print("RESULT: FAIL -- assertions ran with optimize=0 and at least one suite failed.")
        print("=" * 78)
        return 1
    print("=" * 78)
    print("RESULT: PASS -- every suite ran with assertions compiled live (optimize=0).")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        print("run_tests: interrupted", file=sys.stderr)
        sys.exit(130)
