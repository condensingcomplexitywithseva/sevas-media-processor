# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import re

import pytest

from test_no_native_dialogs import SRC, frontend_sources, strip_comments

BANNED = [
    (re.compile(r"\.download\s*="), "anchor .download assignment"),
    (re.compile(r"\bcreateObjectURL\b"), "blob URL for a download"),
    (re.compile(r"<a\s[^>]*\bdownload\b"), "HTML download attribute"),
    (re.compile(r"\bwindow\.open\s*\("), "window.open"),
    (re.compile(r"""target\s*=\s*["']_blank"""), "target=_blank"),
]


def test_no_browser_download_idioms_in_the_frontend():
    files = frontend_sources()
    assert len(files) >= 10, f"source scan looks broken, found only: {files}"

    hits = []
    for path in files:
        raw = path.read_text(encoding="utf-8").splitlines()
        code = strip_comments("\n".join(raw)).splitlines()
        for lineno, line in enumerate(code, 1):
            for pattern, label in BANNED:
                if pattern.search(line):
                    rel = path.relative_to(SRC.parent)
                    hits.append(f"  {rel}:{lineno} ({label}): {raw[lineno - 1].strip()}")

    assert not hits, (
        "browser-download idioms found - the WebView cancels downloads "
        "silently; export to a folder via the backend instead "
        "(see /api/export/logs):\n" + "\n".join(hits)
    )


@pytest.mark.parametrize("snippet", [
    'a.download = "system_log.txt";',
    "anchor.download='x'",
    "const url = window.URL.createObjectURL(blob);",
    "URL.createObjectURL(blob)",
    '<a href="/api/export/logs" download>',
    '<a download="log.txt" href="x">get it</a>',
    "window.open('https://example.com')",
    '<a target="_blank" href="x">',
    "<a target = '_blank'>",
])
def test_the_scan_catches_a_download_idiom(snippet):
    assert any(p.search(strip_comments(snippet)) for p, _ in BANNED), snippet


@pytest.mark.parametrize("snippet", [
    "fetch('/api/export/logs', { method: 'POST' })",
    "window.exportLogs = function() {",
    "window.openExternalLink('github')",
    "openSettingsFile('active')",
    "const downloadCount = 3;",
    "el.dataset.downloadState = 'x';",
    "// the old blob-anchor download was removed - downloads die silently",
    "<!-- never add a download attribute here -->",
    "const target = computeTarget();",
])
def test_the_scan_allows_the_real_frontend_idioms(snippet):
    assert not any(p.search(strip_comments(snippet)) for p, _ in BANNED), snippet
