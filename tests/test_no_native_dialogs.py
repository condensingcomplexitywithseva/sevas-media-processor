# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

NATIVE_DIALOG = re.compile(r"\b(alert|confirm|prompt)\s*\(")


def strip_comments(text):
    out = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("<!--", i):
            end = text.find("-->", i + 4)
            end = n if end == -1 else end + 3
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
        elif text.startswith("//", i) and not (i and text[i - 1] == ":"):
            end = text.find("\n", i)
            end = n if end == -1 else end
        else:
            out.append(text[i])
            i += 1
            continue
        out.append("".join(c if c == "\n" else " " for c in text[i:end]))
        i = end
    return "".join(out)


def frontend_sources():
    return sorted((SRC / "static").rglob("*.js")) + sorted(
        (SRC / "templates").rglob("*.html")
    )


def test_no_native_alert_or_confirm_in_the_frontend():
    files = frontend_sources()
    assert len(files) >= 10, f"source scan looks broken, found only: {files}"

    hits = []
    for path in files:
        raw = path.read_text(encoding="utf-8").splitlines()
        code = strip_comments("\n".join(raw)).splitlines()
        for lineno, line in enumerate(code, 1):
            if NATIVE_DIALOG.search(line):
                rel = path.relative_to(SRC.parent)
                hits.append(f"  {rel}:{lineno}: {raw[lineno - 1].strip()}")

    assert not hits, (
        "native browser dialogs found - use the in-app "
        "appConfirm / appAlert instead:\n" + "\n".join(hits)
    )


def test_the_scan_can_still_see_the_frontend_code():
    app_js = strip_comments((SRC / "static" / "app.js").read_text(encoding="utf-8"))
    assert "window.appConfirm = function" in app_js
    assert "window.appAlert = function" in app_js


@pytest.mark.parametrize("snippet", [
    "alert('boom');",
    "window.alert('boom');",
    "if (confirm('really?')) { go(); }",
    "window.confirm('really?')",
    "  confirm ( 'spaced out' )",
    "prompt('new name?');",
    "window.prompt('new name?')",
    "<script>alert(1)</script>",
    "const url = 'http://127.0.0.1/v1'; alert('boom');",
])
def test_the_scan_catches_a_native_dialog(snippet):
    assert NATIVE_DIALOG.search(strip_comments(snippet)), snippet


@pytest.mark.parametrize("snippet", [
    "await window.appConfirm(msg)",
    "appAlert(window.getT('alert_error', 'An error occurred: '))",
    "// Styled replacements for native confirm()/alert()",
    "/* In-app modal replacing native confirm()/alert() */",
    "<!-- never use alert() here -->",
    ".alert-banner.warning { color: red; }",
    "const confirmMsg = window.getT('confirm_clear_logs');",
    "// last confirmed on disk",
    "const text = buildPrompt(parts);",
    "sendPrompt(payload)",
    "appAlert(window.getT('err_missing_prompt'));",
])
def test_the_scan_allows_the_real_frontend_idioms(snippet):
    assert not NATIVE_DIALOG.search(strip_comments(snippet)), snippet
