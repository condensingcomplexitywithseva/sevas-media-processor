# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import difflib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

FENCE_OPEN = "```powershell"
FENCE_CLOSE = "```"
FIRST_SCRIPT_LINE = "$ErrorActionPreference"

FILE_ASSIGNMENT = "$InstallDir = $PSScriptRoot"
README_ASSIGNMENT = "$InstallDir = $PWD"


def strip_blank_edges(lines):
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return lines


def extract_script_body(text):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(FIRST_SCRIPT_LINE):
            for skipped in lines[:i]:
                stripped = skipped.strip()
                assert not stripped or stripped.startswith("#"), (
                    "install.ps1 has a non-comment line above the "
                    f"{FIRST_SCRIPT_LINE} anchor, invisible to the sync "
                    f"check: {skipped!r}"
                )
            return strip_blank_edges(lines[i:])
    raise AssertionError(
        f"install.ps1 has no line starting with {FIRST_SCRIPT_LINE}"
    )


def extract_readme_block(text):
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if l.strip() == FENCE_OPEN]
    assert len(starts) == 1, (
        f"expected exactly one {FENCE_OPEN} fence in the README, "
        f"found {len(starts)}"
    )
    end = next(
        (
            i
            for i in range(starts[0] + 1, len(lines))
            if lines[i].strip() == FENCE_CLOSE
        ),
        None,
    )
    assert end is not None, "the README's powershell fence is never closed"

    block = lines[starts[0] + 1 : end]
    for i, line in enumerate(block):
        if line.startswith(FIRST_SCRIPT_LINE):
            for skipped in block[:i]:
                assert not skipped.strip(), (
                    "README Option 1 block has a non-blank line above the "
                    f"{FIRST_SCRIPT_LINE} anchor, invisible to the sync "
                    f"check: {skipped!r}"
                )
            return strip_blank_edges(block[i:])
    raise AssertionError(
        f"README Option 1 block has no line starting with {FIRST_SCRIPT_LINE}"
    )


def normalize_readme_block(block):
    assignment_hits = [l for l in block if l.strip() == README_ASSIGNMENT]
    assert len(assignment_hits) == 1, (
        f"expected exactly one '{README_ASSIGNMENT}' line in the README "
        f"block, found {len(assignment_hits)}"
    )
    normalized = []
    for line in block:
        if line.strip() == README_ASSIGNMENT:
            line = line.replace(README_ASSIGNMENT, FILE_ASSIGNMENT)
        else:
            assert "$PWD" not in line and "$PSScriptRoot" not in line, (
                f"undocumented $PWD/$PSScriptRoot mention in README block: {line!r}"
            )
        normalized.append(line)
    return normalized


def test_readme_block_matches_install_ps1():
    script = extract_script_body(
        (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    )
    readme = normalize_readme_block(
        extract_readme_block((REPO_ROOT / "README.md").read_text(encoding="utf-8"))
    )

    script_hits = [l for l in script if l.strip() == FILE_ASSIGNMENT]
    assert len(script_hits) == 1, (
        f"expected exactly one '{FILE_ASSIGNMENT}' line in install.ps1, "
        f"found {len(script_hits)}"
    )

    diff = list(
        difflib.unified_diff(
            script,
            readme,
            fromfile="install.ps1",
            tofile="README.md (Option 1 block, $PWD normalized)",
            lineterm="",
        )
    )
    assert not diff, (
        "install.ps1 and the README Option 1 block have drifted "
        "(whitespace counts - the README copy must be byte-identical apart "
        "from the documented $PWD substitution):\n" + "\n".join(diff)
    )
