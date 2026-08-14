# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
REQUIREMENTS = REPO_ROOT / "requirements.txt"
INSTALL_SCRIPT = REPO_ROOT / "install.ps1"

README_ENTRY = re.compile(r"^\*   ([A-Za-z0-9_.-]+) \(([0-9][^),]*)", re.M)


def normalize(name):
    return name.lower().replace("_", "-").replace(".", "-")


def build_unique(pairs, source):
    result = {}
    dupes = []
    for name, version in pairs:
        if name in result:
            dupes.append(name)
        result[name] = version
    assert not dupes, (
        f"{source} lists the same package more than once: {sorted(set(dupes))}. "
        "Only the last occurrence would be checked; remove the stale copies."
    )
    return result


def parse_readme_versions(text):
    return build_unique(
        ((normalize(name), version) for name, version in README_ENTRY.findall(text)),
        "README.md",
    )


def readme_versions():
    return parse_readme_versions(README.read_text(encoding="utf-8"))


def parse_requirement_versions(text):
    pairs = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, version = line.split(";")[0].strip().partition("==")
        pairs.append((normalize(name.strip()), version.strip()))
    return build_unique(pairs, "requirements.txt")


def requirement_versions():
    return parse_requirement_versions(REQUIREMENTS.read_text(encoding="utf-8"))


def test_the_readme_parser_actually_finds_the_entries():
    found = readme_versions()
    assert len(found) >= 10, (
        f"the README dependency list parser found only {len(found)} entries "
        f"({sorted(found)}). The list format has changed - fix README_ENTRY, "
        "because until it matches, every check in this module is vacuous."
    )


def test_a_duplicated_entry_is_refused_not_last_wins():
    duplicated_readme = (
        "*   pillow (12.1.1): the stale line a reader meets first.\n"
        "*   pillow (12.3.0): the fresh line the dict would keep.\n"
    )
    with pytest.raises(AssertionError, match="pillow"):
        parse_readme_versions(duplicated_readme)
    with pytest.raises(AssertionError, match="pillow"):
        parse_requirement_versions("pillow==12.1.1\npillow==12.3.0\n")


def test_readme_lists_every_pinned_dependency():
    documented = set(readme_versions())
    pinned = set(requirement_versions())
    assert pinned == documented, (
        f"undocumented in README.md: {sorted(pinned - documented)}; "
        f"listed in README.md but not a dependency: {sorted(documented - pinned)}"
    )


def test_readme_versions_match_requirements():
    documented = readme_versions()
    pinned = requirement_versions()
    wrong = {
        name: (documented[name], pinned[name])
        for name in documented.keys() & pinned.keys()
        if documented[name] != pinned[name]
    }
    assert not wrong, (
        "README.md states versions that requirements.txt does not pin "
        f"(name: README vs requirements.txt): {wrong}. requirements.txt is "
        "the source of the README's explained list, so "
        "the README follows the pin, never the other way round."
    )


def required_python_from_install_script():
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    major = re.search(r"^\$RequiredMajor\s*=\s*(\d+)", text, re.M)
    minor = re.search(r"^\$RequiredMinor\s*=\s*(\d+)", text, re.M)
    assert major and minor, "install.ps1 no longer declares RequiredMajor/RequiredMinor"
    return int(major.group(1)), int(minor.group(1))


def test_readme_and_install_script_agree_on_the_python_version():
    major, minor = required_python_from_install_script()
    expected = f"{major}.{minor}"
    text = README.read_text(encoding="utf-8")
    assert f"Python {expected}" in text, (
        f"install.ps1 requires Python {expected}, and README.md never "
        f"mentions 'Python {expected}'. The two must name the same version - "
        "a reader installs from the README and the script enforces the gate."
    )
    assert f"Python.Python.{expected}" in text, (
        f"README.md's winget command does not install Python.Python.{expected}"
    )


def parse_python_version(version_output):
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version_output)
    assert match, f"could not parse a Python version from {version_output!r}"
    return tuple(int(part) for part in match.groups())


def test_the_version_parser_actually_parses():
    assert parse_python_version("Python 3.14.7") == (3, 14, 7)
    with pytest.raises(AssertionError):
        parse_python_version("Python three point one four")


def test_the_suite_runs_on_the_interpreter_the_installer_would_use():
    exe = shutil.which("python.exe") or shutil.which("python")
    if exe is None:
        pytest.skip("no python on PATH - install.ps1 could not run here either")
    output = subprocess.run(
        [exe, "--version"], capture_output=True, text=True, check=True
    )
    installer_version = parse_python_version(output.stdout + output.stderr)
    running = sys.version_info[:3]
    assert running == installer_version, (
        f"this suite runs on Python {'.'.join(map(str, running))}, but the "
        f"python.exe install.ps1 would use ({exe}) is "
        f"{'.'.join(map(str, installer_version))}. Two interpreters have "
        "diverged on this machine: rebuild the venv from the installer's "
        "interpreter (or retire the stale install) so the tested "
        "configuration is the one users get."
    )


def test_the_running_interpreter_satisfies_the_documented_minimum():
    required = required_python_from_install_script()
    running = sys.version_info[:2]
    assert running >= required, (
        f"running Python {running[0]}.{running[1]} but README.md and "
        f"install.ps1 require {required[0]}.{required[1]} or newer"
    )
