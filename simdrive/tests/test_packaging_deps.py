"""
Regression test: every top-level third-party import in simdrive/src/simdrive/
must be declared in [project.dependencies] of pyproject.toml.

This test specifically catches the class of bug where a package is available
transitively (e.g. requests via boto3/mcp) in dev but missing on a clean install.

Only MODULE-TOP-LEVEL imports are checked — imports inside functions, try/except
blocks, or `if TYPE_CHECKING:` guards are excluded (they don't crash on import).
Imports that belong to optional extras ([cloud], [dev], [ssim]) are also excluded
since a bare `pip install simdrive` doesn't pull those.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SIMDRIVE_ROOT = Path(__file__).parent.parent
PYPROJECT_PATH = SIMDRIVE_ROOT / "pyproject.toml"
SRC_ROOT = SIMDRIVE_ROOT / "src" / "simdrive"

# ---------------------------------------------------------------------------
# Stdlib modules — imports from these are never third-party.
# ---------------------------------------------------------------------------
try:
    _STDLIB = sys.stdlib_module_names  # type: ignore[attr-defined]  # Python 3.10+
except AttributeError:
    _STDLIB = frozenset()  # pragma: no cover

_STDLIB_EXTRAS = frozenset({
    "os", "sys", "re", "json", "time", "base64", "datetime", "pathlib",
    "typing", "dataclasses", "subprocess", "hashlib", "tempfile", "uuid",
    "calendar", "abc", "io", "math", "struct", "contextlib", "functools",
    "itertools", "collections", "enum", "copy", "traceback", "inspect",
    "threading", "logging", "warnings", "platform", "shutil", "signal",
    "socket", "urllib", "http", "html", "xml", "csv", "random", "string",
    "secrets", "hmac", "binascii", "codecs", "textwrap", "unittest",
    "ast", "dis", "token", "tokenize", "importlib", "pkgutil", "zipfile",
    "tarfile", "glob", "fnmatch", "getpass", "argparse", "configparser",
    "email", "mimetypes", "pprint", "array", "queue", "heapq", "bisect",
    "weakref", "atexit", "gc", "operator", "decimal", "fractions",
    "statistics", "cmath", "numbers", "locale", "gettext", "plistlib",
    "asyncio", "concurrent", "multiprocessing", "select", "selectors",
    "ssl", "ipaddress", "__future__",
})


def _is_stdlib(name: str) -> bool:
    root = name.split(".")[0]
    return root in _STDLIB or root in _STDLIB_EXTRAS


# ---------------------------------------------------------------------------
# import_name → PyPI dist name for packages where they differ.
# ---------------------------------------------------------------------------
IMPORT_TO_DIST: dict[str, str] = {
    "PIL": "Pillow",
    "nacl": "pynacl",
    "yaml": "pyyaml",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "attr": "attrs",
    "pkg_resources": "setuptools",
    "prometheus_client": "prometheus-client",
    "email_validator": "email-validator",
    "boto3": "boto3",
    "botocore": "botocore",
    "requests": "requests",
    "httpx": "httpx",
    "fastapi": "fastapi",
    "pydantic": "pydantic",
    "anthropic": "anthropic",
    "mcp": "mcp",
    "sqlalchemy": "sqlalchemy",
    "uvicorn": "uvicorn",
}


def _import_to_dist(import_name: str) -> str:
    root = import_name.split(".")[0]
    return IMPORT_TO_DIST.get(root, root)


# ---------------------------------------------------------------------------
# Imports from optional extras or always-transitive — excluded from core check.
# ---------------------------------------------------------------------------
EXCLUDED_IMPORT_ROOTS: frozenset[str] = frozenset({
    # [cloud] extras
    "fastapi", "uvicorn", "sqlalchemy",
    # [dev] extras
    "anthropic", "httpx", "hypothesis", "pytest", "mypy", "ruff",
    # [ssim] extras
    "skimage",
    # botocore is always installed as a transitive dep of boto3 (declared)
    "botocore",
})


# ---------------------------------------------------------------------------
# Parse [project.dependencies] from pyproject.toml using simple regex.
# No external deps — stdlib only.
# ---------------------------------------------------------------------------
def _parse_declared_deps(pyproject: Path) -> set[str]:
    """
    Return the set of normalised dist names declared in [project.dependencies].
    Normalisation: lowercase, underscores→hyphens, version specifiers stripped.
    """
    text = pyproject.read_text()
    m = re.search(r"\[project\]\s*.*?dependencies\s*=\s*\[([^\]]*)\]", text, re.DOTALL)
    if not m:
        raise ValueError("Could not find [project] dependencies in pyproject.toml")

    block = m.group(1)
    deps: set[str] = set()
    for line in block.splitlines():
        line = line.strip().strip('",').strip("',")
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[><=!;\[]", line)[0].strip()
        if name:
            deps.add(name.lower().replace("_", "-"))
    return deps


# ---------------------------------------------------------------------------
# Extract top-level third-party imports from a .py source file.
#
# Strategy: tokenise lines manually to skip:
#   - Lines inside triple-quoted strings (docstrings / multiline strings)
#   - Lines with any leading whitespace (inside blocks: try, if, def, class…)
#   - import/from statements that are NOT at column 0
# ---------------------------------------------------------------------------

def _extract_toplevel_third_party(py_file: Path) -> list[str]:
    """Return list of unique third-party import roots at module top level."""
    text = py_file.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    results: set[str] = set()
    in_docstring = False
    docstring_char: str = ""

    for line in lines:
        stripped = line.rstrip()

        # Track triple-quoted string state
        if not in_docstring:
            # Check if this line opens a triple-quoted string
            for quote in ('"""', "'''"):
                count = stripped.count(quote)
                if count > 0:
                    # If odd number of occurrences, we enter/exit docstring
                    if count % 2 == 1:
                        # Enter docstring — but only if it closes on a later line
                        # Check: does it also close on same line?
                        # Single line: """foo""" → count == 2 (even) → no state change
                        # Opening: """foo → count == 1 → enter
                        in_docstring = True
                        docstring_char = quote
                        break
                    # Even count → opens and closes on same line, no state change
            if in_docstring:
                continue
        else:
            # We're inside a docstring — look for the closing quote
            if docstring_char in stripped:
                in_docstring = False
            continue

        # Only process lines at column 0 (no leading whitespace)
        if stripped and stripped[0] in (" ", "\t"):
            continue

        # Match import statements at column 0
        m = re.match(r"^(import|from)\s+([\w]+)", stripped)
        if not m:
            continue

        module_root = m.group(2)

        # Skip stdlib, simdrive-internal, and empty matches
        if not module_root:
            continue
        if _is_stdlib(module_root):
            continue
        if module_root == "simdrive":
            continue

        results.add(module_root)

    return list(results)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _load_project_dependencies() -> list[str]:
    """
    Return the raw dependency strings from [project.dependencies].
    Used by regression tests that need to inspect the raw spec (e.g. version pins).
    """
    text = PYPROJECT_PATH.read_text()
    m = re.search(r"\[project\]\s*.*?dependencies\s*=\s*\[([^\]]*)\]", text, re.DOTALL)
    if not m:
        raise ValueError("Could not find [project] dependencies in pyproject.toml")
    block = m.group(1)
    deps: list[str] = []
    for line in block.splitlines():
        line = line.strip().strip('",').strip("',")
        # Strip inline comments
        line = line.split("#")[0].strip()
        if not line:
            continue
        deps.append(line)
    return deps


def test_declared_deps_contains_requests() -> None:
    """
    Test 1: requests must appear in [project.dependencies].

    This is the specific regression test for the gap release pipeline caught:
    license/cli.py imports `requests` at module top, but it was absent from
    the declared deps. Clean install → ModuleNotFoundError.
    """
    declared = _parse_declared_deps(PYPROJECT_PATH)
    assert "requests" in declared, (
        "MISSING DEP: 'requests' is imported at module top in "
        "simdrive/license/cli.py but is not declared in [project.dependencies]. "
        "Add 'requests>=2.28' to pyproject.toml."
    )


def test_all_toplevel_imports_declared() -> None:
    """
    Test 2: every top-level third-party import across all src/simdrive/*.py files
    must be declared in [project.dependencies] (or be an optional/transitive module).

    If this test fails, it prints exactly which file and import is undeclared,
    so the bug is debuggable in 5 seconds.
    """
    declared = _parse_declared_deps(PYPROJECT_PATH)
    py_files = list(SRC_ROOT.rglob("*.py"))
    assert py_files, f"No .py files found under {SRC_ROOT}"

    undeclared: list[str] = []

    for py_file in sorted(py_files):
        imports = _extract_toplevel_third_party(py_file)
        for imp_root in imports:
            if imp_root in EXCLUDED_IMPORT_ROOTS:
                continue
            dist_name = _import_to_dist(imp_root).lower().replace("_", "-")
            if dist_name not in declared:
                rel = py_file.relative_to(SIMDRIVE_ROOT)
                undeclared.append(
                    f"  {rel}: import '{imp_root}' → dist '{dist_name}' NOT in [project.dependencies]"
                )

    assert not undeclared, (
        "Found top-level imports that are undeclared in pyproject.toml "
        "(these crash on a clean `pip install simdrive`):\n"
        + "\n".join(undeclared)
        + "\n\nAdd the missing dist names to [project.dependencies] in pyproject.toml."
    )


def test_pinned_sha_in_package_data() -> None:
    """wda/PINNED_SHA.txt must be declared in [tool.setuptools.package-data].

    Without this entry, `pip install simdrive` omits PINNED_SHA.txt from the
    wheel and `simdrive bootstrap-device` fails with FileNotFoundError when it
    tries to read the pinned WDA SHA.
    """
    text = PYPROJECT_PATH.read_text()
    m = re.search(r"\[tool\.setuptools\.package-data\](.*?)(?=\n\[|\Z)", text, re.DOTALL)
    assert m is not None, "Could not find [tool.setuptools.package-data] in pyproject.toml"
    block = m.group(1)
    assert "wda/PINNED_SHA.txt" in block, (
        "'wda/PINNED_SHA.txt' is missing from [tool.setuptools.package-data]. "
        "Without this declaration, the file is excluded from the wheel and "
        "`simdrive bootstrap-device` raises FileNotFoundError."
    )


def test_no_path_file_data_undeclared() -> None:
    """Every Path(__file__).parent / X reference in src/simdrive/ should have X declared in package-data.

    Scans source files for the pattern `Path(__file__).parent / "..."` (or similar)
    and checks the quoted filename appears in [tool.setuptools.package-data].
    This catches future additions of data files that are read via __file__ but
    not declared in pyproject.toml.
    """
    text = PYPROJECT_PATH.read_text()
    pkg_data_match = re.search(
        r"\[tool\.setuptools\.package-data\](.*?)(?=\n\[|\Z)", text, re.DOTALL
    )
    pkg_data_block = pkg_data_match.group(1) if pkg_data_match else ""

    # Collect all Path(__file__).parent / "..." references
    file_refs: list[tuple[Path, str]] = []
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        src = py_file.read_text(encoding="utf-8", errors="replace")
        # Match: Path(__file__).parent / "something" or / 'something'
        for m_ref in re.finditer(r'Path\(__file__\)\.parent\s*/\s*["\']([^"\']+)["\']', src):
            file_refs.append((py_file, m_ref.group(1)))

    undeclared: list[str] = []
    for py_file, ref in file_refs:
        # Check if this data file reference appears in package-data
        if ref not in pkg_data_block:
            rel = py_file.relative_to(SIMDRIVE_ROOT)
            undeclared.append(f"  {rel}: Path(__file__).parent / {ref!r} not in package-data")

    assert not undeclared, (
        "Found Path(__file__).parent / 'X' references where X is not declared in "
        "[tool.setuptools.package-data]:\n"
        + "\n".join(undeclared)
        + "\n\nAdd the missing entries to [tool.setuptools.package-data] in pyproject.toml."
    )


def test_httpx_pinned_below_1_0() -> None:
    """httpx must be pinned <1.0.

    REASON: mcp 1.27.0 declares `httpx>=0.27.1` with no upper bound. With
    `pip install --pre`, the resolver picks `httpx 1.0.dev3` (a real
    pre-release on PyPI), which breaks `httpx-sse` and the MCP transport
    layer. Until upstream mcp adds an upper bound, simdrive must defend
    its users with a top-level pin. Caught by release pipeline pre-publish
    smoke for 1.0.0a4.
    """
    deps = _load_project_dependencies()
    httpx_specs = [d for d in deps if d.startswith("httpx")]
    assert httpx_specs, "httpx not declared in [project.dependencies]"
    spec = httpx_specs[0]
    assert "<1.0" in spec or "<1," in spec or "<2" in spec, (
        f"httpx must be pinned below 1.0; got {spec!r}. "
        f"Reason: mcp 1.27.0 unbounded httpx>=0.27.1 + pip --pre = httpx 1.0.dev3 = breaks httpx-sse."
    )
