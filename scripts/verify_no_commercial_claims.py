#!/usr/bin/env python3
"""INIT-2026-605 — audit every public SimDrive surface for commercial claims.

SimDrive was a paid MCP tool (Pro $29/mo, Team $99/seat/mo, Polar checkout,
14-day trial/paywall). INIT-2026-605 removes those commercial surfaces.
SimDrive stays free to use.

This script is the TDD contract for that removal: it FAILS today (the claims
are still live in source and on the served sites) and must PASS once the
takedown PRs land — in source, on simdrive.dev, on the docs site, on PyPI,
and on GitHub. It also asserts the thing removal must NOT break: the MCP
server still starts and still registers all 36 tools.

Scope
-----
1. Repo-local marketing surfaces (this repo's root README/llms.txt, the
   nested `simdrive/` package's README/pyproject/docs, GTM docs).
2. Sibling repos on disk, if present: simdrive-site, simdrive-docs,
   simdrive-docs-starlight. Missing sibling repos are skipped with a
   warning, not a hard failure — this script must still be useful when run
   from CI where those repos aren't checked out.
3. What's actually served right now: simdrive.dev (home, /pricing,
   /llms.txt, /llms-full.txt), docs.simdrive.dev, the PyPI JSON API, and
   GitHub raw content on the default branch. Source and served state can
   diverge — that divergence is the whole reason this initiative exists, so
   both must be checked independently. Live checks can be skipped with
   --skip-live for offline/sandboxed runs (a skip is reported, not treated
   as a pass).
4. The MCP tool registry: `simdrive.server.list_tools()` must still return
   exactly 36 tools after the takedown. A takedown that breaks internal
   usage is a failed takedown.

Design choice — dated release-note pages are EXCLUDED from the hard-fail
scan. That means CHANGELOG.md, but also every equivalent per-repo page
under a `changelog/` path: `changelog/index.mdx`,
`content/changelog/<version>.md`, etc. They are a historical record ("we
shipped $29/mo pricing on this date"), not a live claim; scrubbing them
would be a no-theater violation (rewriting history), not a "removal" — a
lesson learned the hard way when an early takedown PR reworded published
release notes to pass this exact check instead of leaving them alone.
Changelog hits are reported as informational only under --show-changelog.
Test/source code (e.g. the `LicenseError` exception class, its docstrings,
or its test files) is likewise never scanned — banned patterns are checked
only against surfaces a customer/visitor actually reads.

Exit code: 0 if every hard-fail check passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent          # simdrive repo root
_PKG = _REPO / "simdrive"                                 # nested package dir
_SIMDRIVE_SRC = _PKG / "src"

EXPECTED_TOOL_COUNT = 36

# ---------------------------------------------------------------------------
# Banned patterns. Each is (label, compiled regex). A file/URL fails the
# audit the moment ANY of these matches, once STALE_TOOL_COUNT_RE (handled
# separately, below) is folded in.
# ---------------------------------------------------------------------------
BANNED_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("polar_checkout_link", re.compile(r"[a-zA-Z0-9.-]*polar\.sh", re.I)),
    ("price_29", re.compile(r"\$29\b")),
    ("price_99", re.compile(r"\$99\b")),
    ("price_50k", re.compile(r"\$50K\b", re.I)),
    ("pro_pipe_dollar", re.compile(r"Pro\s*\|\s*\$")),
    ("team_pipe_dollar", re.compile(r"Team\s*\|\s*\$")),
    ("seat_per_month", re.compile(r"/\s*seat\s*/?\s*mo\b", re.I)),
    ("enterprise_from", re.compile(r"Enterprise\s+from", re.I)),
    ("ci_replay_free_claim", re.compile(r"free\s+in\s+CI", re.I)),
    ("unlimited_ci_replays", re.compile(r"unlimited\s+(CI\s+)?replays(\s+in\s+CI)?", re.I)),
    ("zero_ai_cost", re.compile(r"Zero\s+AI\s+cost", re.I)),
    ("no_ai_cost", re.compile(r"no\s+AI\s+cost", re.I)),
    ("trial_14_day", re.compile(r"14[-\s]day\s+trial", re.I)),
    ("after_14_days", re.compile(r"after\s+14\s+days", re.I)),
    ("license_error_in_marketing_copy", re.compile(r"LicenseError")),
    ("trial_start_cta", re.compile(r"\btrial\s+start\b", re.I)),
]

# Any "<N> tools" / "<N> MCP tools" / "<N> vision-first tools" claim where
# N != 36 is stale — catches "32 tools" AND "33 tools" (both observed live)
# without hardcoding every wrong number anyone has typed.
STALE_TOOL_COUNT_RE = re.compile(
    r"\b(\d{1,3})\s+(vision-first\s+)?(MCP\s+)?tools\b", re.I
)

# Historical-record exemption. Originally this matched only a literal
# `CHANGELOG.md` filename -- but every repo in this fleet also carries
# equivalent dated release-note pages (a per-repo `changelog/` section
# rendered on the docs/marketing site: `changelog/index.mdx`,
# `content/changelog/<version>.md`, etc.) that are exactly the same kind of
# historical record, just not named CHANGELOG.md. QualityAtlas caught a PR
# that "fixed" the audit by rewording published release notes instead of
# leaving history alone -- that's the failure mode this whole module exists
# to prevent, applied to itself. Any path with a `changelog` (case-
# insensitive) path segment, or literally named CHANGELOG.md, is exempt.
CHANGELOG_NAME_RE = re.compile(r"(^|[/\\])CHANGELOG\.md$", re.I)
CHANGELOG_DIR_RE = re.compile(r"(^|[/\\])changelog([/\\]|$)", re.I)


def _is_changelog_surface(path_str: str) -> bool:
    return bool(CHANGELOG_NAME_RE.search(path_str) or CHANGELOG_DIR_RE.search(path_str))


@dataclass
class Finding:
    surface: str          # file path or URL
    pattern: str           # label of the banned pattern, or "stale_tool_count"
    line_no: int | None
    excerpt: str
    advisory: bool = False  # True => reported, not counted toward hard fail


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)

    @property
    def hard_failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.advisory]


def _scan_text(surface: str, text: str, *, advisory: bool, report: Report) -> None:
    for line_no, line in enumerate(text.splitlines(), start=1):
        for label, pattern in BANNED_PATTERNS:
            if pattern.search(line):
                report.findings.append(
                    Finding(surface, label, line_no, line.strip()[:220], advisory)
                )
        for m in STALE_TOOL_COUNT_RE.finditer(line):
            n = int(m.group(1))
            if n != EXPECTED_TOOL_COUNT:
                report.findings.append(
                    Finding(
                        surface,
                        f"stale_tool_count({n}!={EXPECTED_TOOL_COUNT})",
                        line_no,
                        line.strip()[:220],
                        advisory,
                    )
                )


def _scan_file(path: Path, report: Report) -> None:
    advisory = _is_changelog_surface(str(path))
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        report.warnings.append(f"could not read {path}: {exc}")
        return
    _scan_text(str(path), text, advisory=advisory, report=report)


_SKIP_DIR_NAMES = {
    ".git", "node_modules", "dist", "build", ".astro", "__pycache__",
    ".venv", "venv", ".ruff_cache", ".next", ".cache",
}


def _iter_files(root: Path, patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pattern in patterns:
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIR_NAMES for part in p.parts):
                continue
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Marketing/public-surface file sets, per repo.
# ---------------------------------------------------------------------------

def repo_root_marketing_files() -> list[Path]:
    """Top-level surfaces of THIS repo — what GitHub renders by default,
    and llms.txt / docs consumed by AI agents / humans landing on the repo.
    Distinct from simdrive/README.md (the PyPI long_description) — the repo
    has two READMEs and both are public.
    """
    candidates = [
        _REPO / "README.md",
        _REPO / "llms.txt",
        _REPO / "docs" / "landing-page.md",
        _REPO / "docs" / "MCP_TOOL_SURFACE.md",
        _REPO / "docs" / "troubleshooting.md",
    ]
    return [p for p in candidates if p.is_file()]


def package_marketing_files() -> list[Path]:
    """Public surfaces of the published `simdrive` PyPI package."""
    candidates = [
        _PKG / "README.md",           # readme = "README.md" in pyproject.toml
        _PKG / "pyproject.toml",       # PyPI summary/description
        _PKG / "llms.txt",
        _PKG / "server.json",          # MCP registry manifest, if populated
        _PKG / "docs" / "RECOVERY.md",
        _PKG / "docs" / "MIGRATION.md",
        _PKG / "CHANGELOG.md",         # advisory-only, see module docstring
    ]
    files = [p for p in candidates if p.is_file()]
    files += _iter_files(_PKG / "docs" / "gtm", ["*.md"])
    return files


_SIBLING_ENV_VAR = {
    "simdrive-site": "SIMDRIVE_SITE_REPO",
    "simdrive-docs": "SIMDRIVE_DOCS_REPO",
    "simdrive-docs-starlight": "SIMDRIVE_DOCS_STARLIGHT_REPO",
}


def sibling_repo_files(name: str, patterns: list[str], report: Report) -> list[Path]:
    """Locate a sibling repo by (1) an explicit env var override, since this
    script may run from a worktree that isn't checked out next to its
    siblings, or (2) `_REPO.parent / name`, the conventional layout when
    all SimDrive repos are cloned side by side. Missing sibling repos are
    reported as a warning and skipped — not a hard failure — so this script
    stays useful in a CI runner that only clones `simdrive`.
    """
    import os

    override = os.environ.get(_SIBLING_ENV_VAR[name])
    root = Path(override) if override else (_REPO.parent / name)
    if not root.is_dir():
        report.warnings.append(
            f"sibling repo '{name}' not found at {root} "
            f"(set {_SIBLING_ENV_VAR[name]} to override) — skipped, not scanned"
        )
        return []
    return _iter_files(root, patterns)


# ---------------------------------------------------------------------------
# Live surfaces.
# ---------------------------------------------------------------------------

LIVE_URLS = [
    "https://simdrive.dev/",
    "https://simdrive.dev/pricing",
    "https://simdrive.dev/llms.txt",
    "https://simdrive.dev/llms-full.txt",
    "https://docs.simdrive.dev/",
    "https://docs.simdrive.dev/license/trial",
    "https://docs.simdrive.dev/license/paid",
    "https://docs.simdrive.dev/concepts/license",
    "https://docs.simdrive.dev/troubleshooting",
    "https://raw.githubusercontent.com/SyncTek-LLC/simdrive/main/README.md",
    "https://raw.githubusercontent.com/SyncTek-LLC/simdrive/main/simdrive/README.md",
]

PYPI_JSON_URL = "https://pypi.org/pypi/simdrive/json"


def _fetch(url: str, timeout: int = 20) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": "simdrive-verify/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return None if _record_fetch_warning(url, exc) else None


_report_for_warnings: Report | None = None


def _record_fetch_warning(url: str, exc: Exception) -> bool:
    if _report_for_warnings is not None:
        _report_for_warnings.warnings.append(f"live fetch failed for {url}: {exc}")
    return True


def check_live_surfaces(report: Report) -> None:
    global _report_for_warnings
    _report_for_warnings = report
    report.checks_run.append("live_surfaces")

    for url in LIVE_URLS:
        text = _fetch(url)
        if text is None:
            continue
        _scan_text(url, text, advisory=False, report=report)

    # PyPI JSON API — far more reliable than scraping the HTML page (which
    # sits behind a CSP/bot-check shell that doesn't hydrate under curl).
    raw = _fetch(PYPI_JSON_URL)
    if raw is None:
        return
    try:
        data = json.loads(raw)
        summary = data["info"].get("summary") or ""
        version = data["info"].get("version") or "?"
    except (json.JSONDecodeError, KeyError) as exc:
        report.warnings.append(f"could not parse PyPI JSON: {exc}")
        return
    _scan_text(f"{PYPI_JSON_URL} (summary, version={version})", summary, advisory=False, report=report)


# ---------------------------------------------------------------------------
# Required-present checks.
# ---------------------------------------------------------------------------

def check_tool_count_stated_as_36(report: Report) -> None:
    report.checks_run.append("tool_count_stated_as_36")
    targets = {
        "pyproject_description": _PKG / "pyproject.toml",
        "package_readme": _PKG / "README.md",
    }
    for label, path in targets.items():
        if not path.is_file():
            report.warnings.append(f"{label}: {path} not found")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not re.search(r"\b36\s+(vision-first\s+)?(MCP\s+)?tools\b", text, re.I):
            report.findings.append(
                Finding(str(path), "missing_required_36_tools_claim", None,
                        f"{label} does not state '36 tools' anywhere", advisory=False)
            )


def check_no_replay_cli_entry_point(report: Report) -> None:
    """Independently confirm: [project.scripts] has exactly two entries,
    both `simdrive.server:serve` — i.e. no replay CLI exists, so nothing may
    claim a standalone replay command with no entry point behind it.
    """
    report.checks_run.append("no_replay_cli_entry_point")
    pyproject = _PKG / "pyproject.toml"
    if not pyproject.is_file():
        report.warnings.append(f"{pyproject} not found; cannot verify [project.scripts]")
        return
    text = pyproject.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"\[project\.scripts\](.*?)(\n\[|\Z)", text, re.S)
    if not m:
        report.findings.append(
            Finding(str(pyproject), "missing_project_scripts_table", None,
                    "[project.scripts] table not found", advisory=False)
        )
        return
    body = m.group(1)
    entries = dict(re.findall(r'^\s*([\w-]+)\s*=\s*"([^"]+)"\s*$', body, re.M))
    if len(entries) != 2 or not all(v == "simdrive.server:serve" for v in entries.values()):
        report.findings.append(
            Finding(str(pyproject), "unexpected_project_scripts_entries", None,
                    f"expected exactly 2 entries -> simdrive.server:serve, got: {entries}",
                    advisory=False)
        )
    # Independently confirm no separate 'replay'/'simdrive-replay' console script exists.
    if re.search(r'^\s*(replay|simdrive-replay)\s*=', body, re.M | re.I):
        report.findings.append(
            Finding(str(pyproject), "replay_cli_entry_point_exists", None,
                    "a replay-named console_script entry was found — "
                    "this contradicts the 'no replay CLI exists' ground truth",
                    advisory=False)
        )


def _resolve_python() -> str:
    """Find an interpreter with simdrive's runtime deps (mcp, pydantic, ...)
    installed. Bare `sys.executable` usually only has the stdlib, so prefer
    a project virtualenv if one exists; env var wins if set.
    """
    import os

    env_override = os.environ.get("SIMDRIVE_VERIFY_PYTHON")
    if env_override:
        return env_override
    for candidate in (_REPO / ".venv" / "bin" / "python", _PKG / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def check_mcp_tool_registry_intact(report: Report) -> None:
    """SimDrive must remain fully usable internally: the MCP server must
    still start and register exactly 36 tools. Run out-of-process so an
    import-time crash is caught as a failure, not a hard stop of this
    script.
    """
    report.checks_run.append("mcp_tool_registry_intact")
    python = _resolve_python()
    code = (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "from simdrive import server\n"
        "tools = server.list_tools()\n"
        "print(len(tools))\n"
    ) % str(_SIMDRIVE_SRC)
    try:
        proc = subprocess.run(
            [python, "-c", code],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.findings.append(
            Finding("mcp_tool_registry", "registry_check_crashed", None, str(exc), advisory=False)
        )
        return
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1] if (proc.stderr or proc.stdout).strip() else "(no output)"
        report.findings.append(
            Finding("mcp_tool_registry", "server_import_or_list_tools_failed", None,
                    f"using python={python} :: {tail}", advisory=False)
        )
        return
    try:
        count = int(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        report.findings.append(
            Finding("mcp_tool_registry", "unparseable_output", None, proc.stdout, advisory=False)
        )
        return
    if count != EXPECTED_TOOL_COUNT:
        report.findings.append(
            Finding("mcp_tool_registry", "wrong_tool_count", None,
                    f"server.list_tools() returned {count}, expected {EXPECTED_TOOL_COUNT}",
                    advisory=False)
        )
    else:
        report.checks_run.append(f"mcp_tool_registry_intact:PASS ({count} tools)")


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> Report:
    report = Report()

    files = repo_root_marketing_files() + package_marketing_files()
    files += sibling_repo_files(
        "simdrive-site",
        ["src/**/*.astro", "src/**/*.md", "src/**/*.mdx", "src/**/*.ts"],
        report,
    )
    files += sibling_repo_files("simdrive-docs", ["**/*.mdx", "**/*.md"], report)
    files += sibling_repo_files(
        "simdrive-docs-starlight",
        ["src/content/**/*.mdx", "src/content/**/*.md", "README.md"],
        report,
    )

    if not files:
        report.warnings.append("no marketing files found at all — check _REPO paths")
    report.checks_run.append(f"source_scan ({len(files)} files)")
    for path in files:
        _scan_file(path, report)

    check_tool_count_stated_as_36(report)
    check_no_replay_cli_entry_point(report)

    if not args.skip_live:
        check_live_surfaces(report)
    else:
        report.warnings.append("--skip-live set: live simdrive.dev/PyPI/GitHub checks NOT run")

    if not args.skip_registry:
        check_mcp_tool_registry_intact(report)
    else:
        report.warnings.append("--skip-registry set: MCP tool registry NOT verified")

    return report


def print_report(report: Report, *, show_changelog: bool) -> None:
    hard = report.hard_failures
    advisory = [f for f in report.findings if f.advisory]

    print("=" * 78)
    print("SimDrive commercial-surface audit — INIT-2026-605")
    print("=" * 78)
    print(f"checks run: {', '.join(report.checks_run)}")
    if report.warnings:
        print("\nWARNINGS:")
        for w in report.warnings:
            print(f"  - {w}")

    print(f"\n{'FAIL' if hard else 'PASS'} — {len(hard)} hard failure(s), "
          f"{len(advisory)} advisory (CHANGELOG) finding(s)")

    if hard:
        print("\n--- HARD FAILURES (must be zero to pass) ---")
        for f in hard:
            loc = f"{f.surface}:{f.line_no}" if f.line_no else f.surface
            print(f"  [{f.pattern}] {loc}\n      {f.excerpt}")

    if advisory and show_changelog:
        print("\n--- ADVISORY (dated changelog/release-note pages — historical record, not scanned for pass/fail) ---")
        for f in advisory:
            loc = f"{f.surface}:{f.line_no}" if f.line_no else f.surface
            print(f"  [{f.pattern}] {loc}\n      {f.excerpt}")

    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-live", action="store_true",
                         help="skip HTTP checks against simdrive.dev / docs / PyPI / GitHub")
    parser.add_argument("--skip-registry", action="store_true",
                         help="skip the MCP tool-registry-intact check")
    parser.add_argument("--show-changelog", action="store_true",
                         help="also print CHANGELOG.md advisory findings")
    args = parser.parse_args()

    report = run(args)
    print_report(report, show_changelog=args.show_changelog)
    return 1 if report.hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
