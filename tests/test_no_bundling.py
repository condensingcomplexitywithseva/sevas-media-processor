# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SCANNED_DIRS = ["src", "tests", "tools"]

BUNDLERS = {"pyinstaller", "cx-freeze", "nuitka", "py2exe", "briefcase"}

NAME_END = re.compile(r"[\[=<>!~;@ \t]")


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_names(text):
    names = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = NAME_END.search(line)
        names.append(normalize(line[: match.start()] if match else line))
    return names


def is_bundler(name):
    return any(name == b or name.startswith(b + "-") for b in BUNDLERS)


def requirements_files():
    files = []
    for pattern in ("requirements*.txt", "requirements*.lock"):
        files += sorted(REPO_ROOT.glob(pattern))
        for folder in SCANNED_DIRS:
            files += sorted((REPO_ROOT / folder).rglob(pattern))
    return sorted(set(files))


def test_no_bundler_in_any_requirements_file():
    hits = []
    for path in requirements_files():
        for name in requirement_names(path.read_text(encoding="utf-8")):
            if is_bundler(name):
                hits.append(f"  {path.relative_to(REPO_ROOT)}: {name}")

    assert not hits, (
        "packaging/bundler dependency found - this project is source-only: "
        "bundling GPL-2.0 encoder code into a "
        "distributed artifact conflicts with Apache-2.0:\n" + "\n".join(hits)
    )


def test_no_bundler_spec_file_in_the_repo():
    specs = sorted(REPO_ROOT.glob("*.spec"))
    for folder in SCANNED_DIRS:
        specs += sorted((REPO_ROOT / folder).rglob("*.spec"))
    names = [str(p.relative_to(REPO_ROOT)) for p in specs]
    assert not names, (
        "PyInstaller-style .spec file(s) in the repo - this project is "
        "source-only: " + ", ".join(names)
    )


def test_the_scan_can_still_see_the_requirements():
    files = requirements_files()
    assert {
        "requirements.txt", "requirements_no_version.txt",
        "requirements.lock", "requirements-dev.txt",
    } <= {
        p.name for p in files
    }, f"requirements files not found, scan looks broken: {files}"

    app_trio = {"pillow", "flask", "pywebview"}
    for path in files:
        names = set(requirement_names(path.read_text(encoding="utf-8")))
        expected = {"pytest"} if path.name == "requirements-dev.txt" else app_trio
        assert expected <= names, f"{path.name} parsed into {sorted(names)}"


@pytest.mark.parametrize("line", [
    "pyinstaller",
    "PyInstaller==6.3.0",
    "pyinstaller >= 6.0",
    "pyinstaller[encryption]==6.3.0",
    "pyinstaller-hooks-contrib",
    "cx_Freeze==7.2.0",
    "cx-freeze",
    "Nuitka~=2.4",
    "py2exe; sys_platform == 'win32'",
    "briefcase",
    'pyinstaller==6.3.0; sys_platform == "win32"',
])
def test_the_scan_catches_a_bundler(line):
    assert any(is_bundler(n) for n in requirement_names(line)), line


@pytest.mark.parametrize("line", [
    "pillow==12.1.1",
    "pywebview==5.3.2",
    "pypdfium2==5.4.0",
    "installer==0.7.0",
    'pythonnet==3.0.5; sys_platform == "win32"',
    "# pyinstaller is banned here - this project is source-only",
    "requests==2.33.1  # pyinstaller would break this",
    "-r requirements.txt",
    "",
    "   ",
])
def test_the_scan_allows_the_real_requirement_lines(line):
    assert not any(is_bundler(n) for n in requirement_names(line)), line
