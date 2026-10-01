"""
scripts/run_tests.py
---------------------
Runs every test suite in the repository and reports progress live, in colour.

`TESTING.md` is the reference: what each suite covers, why two of them skip, and the
hazards. This is the thing you actually run. They are kept consistent on purpose -- the
expected counts below are the ones TESTING.md documents, so a drift between them shows up
as a WARN rather than passing silently.

    python scripts/run_tests.py                 # Python + frontend suites
    python scripts/run_tests.py --python-only   # skip the frontend (no node needed)
    python scripts/run_tests.py --containers    # also check the live stack's LLM fallback
    python scripts/run_tests.py -v              # stream each suite's full output
    python scripts/run_tests.py --no-color      # plain text (also automatic when piped)

Exit code is 0 only if every suite that ran passed, so this is usable as a gate.

**`tests/integration` is deliberately NOT run here.** Its conftest defaults DATABASE_URL
to a live database name and has destroyed this project's demo data once; a convenience
runner is the worst possible place to hide that. See TESTING.md section 7 for the explicit
invocation if you want it.

Two portability details that are not incidental:

  * every subprocess is read with encoding="utf-8", errors="replace". Without it, Python
    decodes child output using the host's preferred encoding -- cp1252 on this machine --
    and `node --test` emits U+2139 INFORMATION SOURCE, which is not representable there.
    That raises UnicodeDecodeError inside subprocess's reader *thread*, where it does not
    propagate: the run appears to succeed while the output is silently lost.
  * the status glyphs degrade to ASCII when stdout cannot encode them, for the same
    reason in the other direction -- printing a check mark to a cp1252 console raises
    UnicodeEncodeError and would crash the runner rather than the tests.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------- terminal capabilities

def _supports(text: str) -> bool:
    """Can stdout actually encode this?"""
    enc = sys.stdout.encoding or "ascii"
    try:
        text.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        fancy = _supports("✓✗─▶")
        self.ok_mark = "✓" if fancy else "OK"
        self.bad_mark = "✗" if fancy else "FAIL"
        self.run_mark = "▶" if fancy else ">"
        self.skip_mark = "·" if _supports("·") else "-"
        self.rule_char = "─" if fancy else "-"

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def green(self, t): return self._wrap("32", t)
    def red(self, t): return self._wrap("31", t)
    def yellow(self, t): return self._wrap("33", t)
    def dim(self, t): return self._wrap("2", t)
    def bold(self, t): return self._wrap("1", t)
    def cyan(self, t): return self._wrap("36", t)


def enable_ansi_on_windows() -> bool:
    """Turns on virtual-terminal processing so escape codes render instead of printing
    literally. Modern Windows Terminal does this already; older conhost does not."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # -11 = STD_OUTPUT_HANDLE, 0x4 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x4))
    except Exception:
        return False


# ------------------------------------------------------------------------- suite results

@dataclass
class Suite:
    name: str
    cmd: list[str] | str
    cwd: Path
    expected: str                 # what TESTING.md says passing looks like
    shell: bool = False
    optional_reason: str | None = None   # set -> a missing prerequisite is a skip, not a fail
    # Several commands whose counts are summed, for a runner that must be invoked once per
    # file. `node --test` is the case: it does not reliably pick up a directory here, and a
    # shell `for` loop is not portable -- shell=True runs cmd.exe on Windows, which rejects
    # bash syntax outright ("f était inattendu"). Looping in Python works everywhere.
    commands: list[list[str]] | None = None


@dataclass
class Result:
    suite: Suite
    status: str                   # "pass" | "fail" | "skip"
    summary: str = ""
    seconds: float = 0.0
    passed: int = 0
    skipped: int = 0
    failed: int = 0
    output: str = ""
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------- output parsing

_PYTEST = re.compile(
    r"(?:(?P<failed>\d+) failed[^\n]*?)?(?P<passed>\d+) passed"
    r"(?:, (?P<skipped>\d+) skipped)?", re.I
)
_PYTEST_ONLY_FAIL = re.compile(r"(?P<failed>\d+) failed", re.I)
_VITEST = re.compile(r"Tests\s+(?P<passed>\d+) passed", re.I)
_NODE_PASS = re.compile(r"^.?\s*pass (?P<passed>\d+)$", re.M)
_NODE_FAIL = re.compile(r"^.?\s*fail (?P<failed>\d+)$", re.M)


def safe(text: str) -> str:
    """Makes text printable on this console.

    Child output is read with errors="replace", which inserts U+FFFD for bytes it could
    not decode -- and U+FFFD is itself not encodable in cp1252, so echoing a failing
    suite's output crashed this runner on the one code path that only ever runs when
    something is already broken. Reporting a failure must not itself fail.
    """
    enc = sys.stdout.encoding or "ascii"
    return text.encode(enc, errors="replace").decode(enc, errors="replace")


def parse_counts(text: str) -> tuple[int, int, int, str]:
    """Returns (passed, skipped, failed, human summary) for any runner we invoke."""
    # Strip ANSI, then normalise line endings. On Windows a child's "pass 13\r\n" leaves
    # a \r before the line end, so an anchored (\d+)$ silently never matches and a
    # perfectly good suite reports "no counts found".
    plain = re.sub(r"\033\[[0-9;]*m", "", text).replace("\r\n", "\n").replace("\r", "\n")

    # vitest first, and only for vitest: its summary contains BOTH "Test Files  2 passed"
    # and "Tests  15 passed", and the generic pytest pattern below matches whichever comes
    # first in the text -- reporting 2 tests instead of 15.
    if "Test Files" in plain:
        m = _VITEST.search(plain)
        if m:
            p = int(m.group("passed"))
            return p, 0, 0, f"{p} passed"

    m = _PYTEST.search(plain)
    if m and m.group("passed"):
        p = int(m.group("passed"))
        s = int(m.group("skipped") or 0)
        f = int(m.group("failed") or 0)
        bits = [f"{p} passed"] + ([f"{s} skipped"] if s else []) + ([f"{f} failed"] if f else [])
        return p, s, f, ", ".join(bits)

    # A run where everything failed prints no "passed" at all.
    m = _PYTEST_ONLY_FAIL.search(plain)
    if m:
        f = int(m.group("failed"))
        return 0, 0, f, f"{f} failed"

    m = _VITEST.search(plain)
    if m:
        p = int(m.group("passed"))
        return p, 0, 0, f"{p} passed"

    passed = sum(int(x) for x in _NODE_PASS.findall(plain)) if _NODE_PASS.search(plain) else 0
    failed = sum(int(x) for x in _NODE_FAIL.findall(plain)) if _NODE_FAIL.search(plain) else 0
    if passed or failed:
        bits = [f"{passed} passed"] + ([f"{failed} failed"] if failed else [])
        return passed, 0, failed, ", ".join(bits)

    return 0, 0, 0, "no counts found"


# ------------------------------------------------------------------------------- the run

def run_many(suite: Suite, verbose: bool) -> Result:
    """Runs each of `suite.commands` and sums the counts. Any non-zero exit fails the
    suite, and the first failure's output is what gets reported."""
    started = time.perf_counter()
    passed = skipped = failed = 0
    output_parts: list[str] = []
    status = "pass"
    for cmd in suite.commands or []:
        proc = subprocess.run(cmd, cwd=str(suite.cwd), capture_output=not verbose,
                              text=True, encoding="utf-8", errors="replace", timeout=1800)
        chunk = "" if verbose else ((proc.stdout or "") + (proc.stderr or ""))
        output_parts.append(chunk)
        p, s, f, _ = parse_counts(chunk)
        passed += p
        skipped += s
        failed += f
        if proc.returncode != 0:
            status = "fail"
    elapsed = time.perf_counter() - started
    bits = [f"{passed} passed"] + ([f"{skipped} skipped"] if skipped else []) \
        + ([f"{failed} failed"] if failed else [])
    summary = ", ".join(bits) if (passed or failed) else "no counts found"
    if status == "pass" and passed == 0 and not verbose:
        status, summary = "fail", "exit 0 but no tests reported"
    result = Result(suite, status, summary, elapsed, passed, skipped, failed,
                    "\n".join(output_parts))
    if status == "pass" and suite.expected and summary != suite.expected:
        result.notes.append(f"TESTING.md expects '{suite.expected}'")
    return result


def run_suite(suite: Suite, style: Style, verbose: bool) -> Result:
    if suite.commands:
        return run_many(suite, verbose)
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            suite.cmd,
            cwd=str(suite.cwd),
            shell=suite.shell,
            capture_output=not verbose,
            text=True,
            # See the module docstring: this is required, not cosmetic.
            encoding="utf-8",
            errors="replace",
            timeout=3600,
        )
    except FileNotFoundError as exc:
        return Result(suite, "skip", f"not available ({exc.filename})",
                      time.perf_counter() - started)
    except subprocess.TimeoutExpired:
        return Result(suite, "fail", "timed out after 1h", time.perf_counter() - started)

    elapsed = time.perf_counter() - started
    output = "" if verbose else ((proc.stdout or "") + (proc.stderr or ""))
    passed, skipped, failed, summary = parse_counts(output)

    status = "pass" if proc.returncode == 0 else "fail"
    # A zero exit with no recognisable counts means the runner did not actually run
    # anything -- "0 tests, exit 0" must never read as success.
    if status == "pass" and passed == 0 and not verbose:
        status, summary = "fail", f"exit 0 but no tests reported ({summary})"

    result = Result(suite, status, summary, elapsed, passed, skipped, failed, output)
    if status == "pass" and suite.expected and summary != suite.expected:
        result.notes.append(f"TESTING.md expects '{suite.expected}'")
    return result


def build_suites(python_only: bool) -> list[Suite]:
    rws_fe = ROOT / "services" / "recruitment-workflow" / "frontend"
    py = [sys.executable, "-m", "pytest", "tests", "-q"]

    suites = [
        Suite("Shared unit", [sys.executable, "-m", "pytest", "tests/unit", "-q"],
              ROOT, "174 passed"),
        Suite("Recruitment Workflow", py, ROOT / "services" / "recruitment-workflow",
              "224 passed"),
        Suite("Resume Intelligence", py, ROOT / "services" / "resume-intelligence",
              "89 passed, 6 skipped"),
        Suite("Second Brain", py, ROOT / "services" / "second-brain", "8 passed"),
        Suite("VoiceHR", py, ROOT / "services" / "voicehr", "288 passed, 1 skipped"),
    ]
    if python_only:
        return suites

    # node --test is run per file, not per directory: see TESTING.md section 6.
    test_files = sorted(p.name for p in (rws_fe / "src" / "constants").glob("*.test.js"))
    if test_files and (rws_fe / "node_modules").is_dir():
        node = shutil.which("node") or "node"
        suites.append(Suite("Frontend logic (node --test)", [], rws_fe, "34 passed",
                            commands=[[node, "--test", f"src/constants/{f}"]
                                      for f in test_files]))
        suites.append(Suite("Frontend components (vitest)",
                            "npm test", rws_fe, "15 passed", shell=True))
    else:
        reason = "node_modules missing -- run `npm ci`" if test_files else "no test files"
        for name in ("Frontend logic (node --test)", "Frontend components (vitest)"):
            suites.append(Suite(name, ["true"], rws_fe, "", optional_reason=reason))
    return suites


def container_check(style: Style, verbose: bool) -> Result:
    suite = Suite("LLM fallback (in-container)",
                  [sys.executable, "scripts/check_llm_fallback.py", "--generate"],
                  ROOT, "")
    if not shutil.which("docker"):
        return Result(suite, "skip", "docker not on PATH")
    started = time.perf_counter()
    proc = subprocess.run(suite.cmd, cwd=str(ROOT), capture_output=not verbose, text=True,
                          encoding="utf-8", errors="replace", timeout=1800)
    out = "" if verbose else ((proc.stdout or "") + (proc.stderr or ""))
    ok = out.count("[ OK ]")
    bad = out.count("[FAIL]")
    off = out.count("[OFF ]")
    summary = f"{ok} ok" + (f", {off} disabled" if off else "") + (f", {bad} FAILING" if bad else "")
    status = "pass" if proc.returncode == 0 else "fail"
    return Result(suite, status, summary, time.perf_counter() - started,
                  passed=ok, failed=bad, output=out)


def _clear(interactive: bool, verbose: bool, colour: bool, label_w: int) -> None:
    """Erases the transient progress line -- but only when one was actually drawn."""
    if verbose or not interactive:
        return
    if colour:
        print("\033[2K", end="\r")
    else:
        print(" " * (label_w + 24), end="\r")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run every test suite with live progress.")
    ap.add_argument("--python-only", action="store_true", help="skip the frontend suites")
    ap.add_argument("--containers", action="store_true",
                    help="also verify the running stack's LLM fallback (needs docker)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="stream each suite's output instead of summarising")
    ap.add_argument("--no-color", action="store_true",
                    help="plain text; also automatic when piped, or with NO_COLOR set")
    ap.add_argument("--color", action="store_true",
                    help="force colour on (for a terminal this cannot detect)")
    args = ap.parse_args()

    # Best-effort: helps old conhost, and is simply unavailable when stdout is not a real
    # console. Its result is deliberately NOT required for colour -- Windows Terminal, the
    # VS Code terminal and every non-Windows terminal render ANSI without it, and gating on
    # it meant no colour at all on a host where the call merely fails rather than
    # indicating a lack of support.
    enable_ansi_on_windows()

    # Whether a human is watching a real terminal. Piped into a file or into `tail`,
    # carriage returns are literal characters, so the overwrite-in-place progress line
    # would be left sitting in the output instead of being replaced.
    interactive = sys.stdout.isatty()

    # NO_COLOR is the cross-tool convention (https://no-color.org); --color forces it on
    # for the case of a terminal this cannot detect.
    if args.color:
        colour = True
    elif args.no_color or os.environ.get("NO_COLOR"):
        colour = False
    else:
        colour = interactive
    st = Style(colour)
    width = 64

    print()
    print(st.bold("  Company Second Brain -- test run"))
    print(st.dim("  " + st.rule_char * width))

    suites = build_suites(args.python_only)
    results: list[Result] = []
    label_w = max(len(s.name) for s in suites) + 2
    overall = time.perf_counter()

    for suite in suites:
        if suite.optional_reason:
            results.append(Result(suite, "skip", suite.optional_reason))
            print(f"  {st.yellow(st.skip_mark)} {suite.name:<{label_w}}"
                  f"{st.yellow('skipped')} {st.dim('(' + suite.optional_reason + ')')}")
            continue

        # The in-progress line is rewritten in place once the suite finishes, so a long
        # run shows what it is currently doing rather than sitting silent.
        if args.verbose:
            print(f"\n  {st.cyan(st.run_mark)} {suite.name}")
        elif interactive:
            print(f"  {st.cyan(st.run_mark)} {suite.name:<{label_w}}{st.dim('running...')}",
                  end="\r", flush=True)

        result = run_suite(suite, st, args.verbose)
        results.append(result)

        mark = st.green(st.ok_mark) if result.status == "pass" else st.red(st.bad_mark)
        body = st.green(result.summary) if result.status == "pass" else st.red(result.summary)
        line = (f"  {mark} {suite.name:<{label_w}}{body}"
                f"  {st.dim(f'{result.seconds:.1f}s')}")
        _clear(interactive, args.verbose, colour, label_w)
        print(line)
        for note in result.notes:
            print(f"    {st.yellow('warn')} {st.dim(note)}")

    if args.containers:
        if interactive and not args.verbose:
            print(f"  {st.cyan(st.run_mark)} container checks{st.dim(' running...')}",
                  end="\r", flush=True)
        result = container_check(st, args.verbose)
        results.append(result)
        mark = {"pass": st.green(st.ok_mark), "fail": st.red(st.bad_mark),
                "skip": st.yellow(st.skip_mark)}[result.status]
        body = {"pass": st.green, "fail": st.red, "skip": st.yellow}[result.status](result.summary)
        _clear(interactive, args.verbose, colour, label_w)
        print(f"  {mark} {result.suite.name:<{label_w}}{body}"
              f"  {st.dim(f'{result.seconds:.1f}s')}")

    # ------------------------------------------------------------------------- summary
    total = time.perf_counter() - overall
    passed = sum(r.passed for r in results)
    skipped_tests = sum(r.skipped for r in results)
    failed_tests = sum(r.failed for r in results)
    bad = [r for r in results if r.status == "fail"]
    skipped_suites = [r for r in results if r.status == "skip"]

    print(st.dim("  " + st.rule_char * width))
    counts = f"{passed} passed"
    if skipped_tests:
        counts += f", {skipped_tests} skipped"
    if failed_tests:
        counts += f", {failed_tests} failed"
    tint = st.red if bad else st.green
    print(f"  {tint(st.bold(counts))}"
          f"  {st.dim(f'across {len(results) - len(skipped_suites)} suites in {total:.1f}s')}")

    if skipped_suites:
        print(f"  {st.yellow(str(len(skipped_suites)) + ' suite(s) skipped')}: "
              + st.dim(", ".join(r.suite.name for r in skipped_suites)))

    print(st.dim(f"  tests/integration is not run here -- see TESTING.md section 7"))
    if not args.containers:
        print(st.dim("  container checks not run -- add --containers (needs the stack up)"))

    if bad:
        print()
        print(st.red(st.bold(f"  {len(bad)} SUITE(S) FAILED")))
        for r in bad:
            print(f"  {st.red(st.bad_mark)} {r.suite.name}: {r.summary}")
            if r.output:
                tail = [ln for ln in r.output.strip().splitlines() if ln.strip()][-12:]
                for ln in tail:
                    print(st.dim(safe(f"      {ln[:140]}")))
        print()
        return 1

    print()
    print(st.green(st.bold("  All suites passed.")))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
