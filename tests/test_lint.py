# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LINT_TARGETS = [d for d in ("src", "tests", "tools") if (REPO_ROOT / d).is_dir()]

PYRIGHT_VERSION = "1.1.403"


def test_ruff_finds_nothing():
    if importlib.util.find_spec("ruff") is None:
        pytest.skip("ruff not installed (dev tier: pip install -r requirements-dev.txt)")
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", *LINT_TARGETS],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, (
        "ruff has findings (ruff.toml is the rule set; prefer a real fix, "
        "and give any suppression a stated reason):\n"
        + result.stdout + result.stderr
    )


def test_pyright_finds_nothing():
    npx = shutil.which("npx")
    if npx is None:
        pytest.skip("Node/npx not installed (dev tier; the CI runners have it)")
    result = subprocess.run(
        [npx, "--yes", f"pyright@{PYRIGHT_VERSION}", "--outputjson",
         "--pythonpath", sys.executable, *LINT_TARGETS],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
    )
    try:
        report = json.loads(result.stdout)
    except ValueError:
        pytest.skip(
            "npx could not launch pyright (npm registry unreachable?); "
            "the CI runners execute this gate:\n" + result.stdout + result.stderr
        )
    findings = [
        "{file}:{line} [{rule}] {message}".format(
            file=Path(d.get("file", "?")).name,
            line=d.get("range", {}).get("start", {}).get("line", -1) + 1,
            rule=d.get("rule", d.get("severity", "?")),
            message=str(d.get("message", "")).splitlines()[0],
        )
        for d in report.get("generalDiagnostics", [])
        if d.get("severity") in ("error", "warning")
    ]
    assert not findings, (
        "pyright has findings (pyrightconfig.json is the rule set; prefer "
        "a real fix, and give any suppression a stated reason):\n"
        + "\n".join(findings)
    )


INVISIBLE_CODE_POINTS = "\ufeff\u200b\u200c\u200d\u2060"


def _python_sources():
    for target in LINT_TARGETS:
        yield from sorted((REPO_ROOT / target).rglob("*.py"))


def test_no_source_line_carries_an_invisible_code_point():
    offenders = []
    for path in _python_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(ch in line for ch in INVISIBLE_CODE_POINTS):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}")
    assert not offenders, (
        "invisible code points written literally (spell them as \\uXXXX escapes):\n"
        + "\n".join(offenders)
    )


def test_the_invisible_sweep_can_see():
    assert any(ch in "a\u200bb" for ch in INVISIBLE_CODE_POINTS)
    assert not any(ch in "a\\u200bb" for ch in INVISIBLE_CODE_POINTS)
