# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

COPYRIGHT = "Copyright 2026 Vsevolod Belonogov"
SPDX = "SPDX-License-Identifier: Apache-2.0"

HEADERS = {
    ".py": (f"# {COPYRIGHT}", f"# {SPDX}"),
    ".ps1": (f"# {COPYRIGHT}", f"# {SPDX}"),
    ".js": (f"/* {COPYRIGHT} */", f"/* {SPDX} */"),
}

NOTICE_TEXT = "Seva's Media Processor\n" + COPYRIGHT + "\n"


def _iter_files():
    for base in ("src", "tests"):
        for path in sorted((REPO / base).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path
    yield REPO / "src" / "static" / "app.js"
    yield REPO / "src" / "static" / "i18n.js"
    yield REPO / "install.ps1"


def _missing_header(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    want = HEADERS[path.suffix.lower()]
    return lines[:2] != list(want)


def test_positive_control_the_check_still_bites(tmp_path):
    bare = tmp_path / "bare.py"
    bare.write_text('"""No header here."""\n', encoding="utf-8")
    assert _missing_header(bare)
    good = tmp_path / "good.py"
    good.write_text(f"# {COPYRIGHT}\n# {SPDX}\n\ncode = 1\n", encoding="utf-8")
    assert not _missing_header(good)


def test_the_walk_sees_the_known_source_files():
    seen = {p.name for p in _iter_files()}
    for required in ("main.py", "conftest.py", "app.js", "i18n.js",
                     "install.ps1", "test_legal_headers.py"):
        assert required in seen, f"the walk no longer reaches {required}"


def test_every_source_file_opens_with_the_header():
    offenders = [
        str(p.relative_to(REPO)) for p in _iter_files() if _missing_header(p)
    ]
    assert not offenders, (
        "source files missing the two-line legal header "
        "(copyright + SPDX, comment style per language):\n"
        + "\n".join(offenders)
    )


def test_notice_is_exactly_the_approved_two_lines():
    notice = (REPO / "NOTICE").read_text(encoding="utf-8")
    assert notice.replace("\r\n", "\n") == NOTICE_TEXT, (
        "NOTICE must hold exactly the approved two lines - every line in "
        "NOTICE is a legal obligation on redistributors (Apache-2.0 4(d))"
    )


def test_license_appendix_line_is_filled_in():
    lines = (REPO / "LICENSE.txt").read_text(encoding="utf-8").splitlines()
    assert lines[189].strip() == COPYRIGHT
