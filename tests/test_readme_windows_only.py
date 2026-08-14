# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"


def test_windows_is_named_in_the_opening_pitch():
    text = README.read_text(encoding="utf-8")
    first_heading = text.find("\n## ")
    assert first_heading != -1, (
        "README.md has no '## ' section headings; the opening-pitch slice "
        "below would cover the whole file and this check would be vacuous"
    )
    pitch = text[:first_heading]
    assert "Windows" in pitch, (
        "the README's opening pitch (everything above the first '## ' "
        "heading) no longer says the app runs on Windows - a macOS or Linux "
        "reader learns it only by failing to install"
    )


def test_no_cross_platform_claim_anywhere():
    text = README.read_text(encoding="utf-8").lower()
    for claim in ("cross-platform", "cross platform"):
        assert claim not in text, (
            f"README.md says {claim!r} again - the app is Windows-only, and "
            "describing any part of it as portable misleads the reader "
            "deciding whether it will run for them"
        )
