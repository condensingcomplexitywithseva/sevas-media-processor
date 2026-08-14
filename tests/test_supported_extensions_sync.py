# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import re
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import to_jpeg_converter
from media_classifier import (
    ANIMATED_IMAGE_EXTENSIONS,
    IMAGE_EXTENSIONS,
    PDF_EXTENSIONS,
    VIDEO_EXTENSIONS,
)

EXTENSION_TOKEN = re.compile(r"\.[a-z0-9]+")

TABS = {
    "images": IMAGE_EXTENSIONS,
    "animations": ANIMATED_IMAGE_EXTENSIONS,
    "videos": VIDEO_EXTENSIONS,
    "docs": PDF_EXTENSIONS,
}

README_CATEGORIES = {
    "Photos and images": IMAGE_EXTENSIONS,
    "Animations": ANIMATED_IMAGE_EXTENSIONS,
    "Documents": PDF_EXTENSIONS,
    "Video": VIDEO_EXTENSIONS,
}

README_HEADING = "## What file types it accepts"


def tab_extensions(stem: str) -> set:
    html = (SRC / "templates" / "tabs" / f"{stem}_content.html").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r'data-i18n="lbl_supported_img".*?</span>\s*<span[^>]*>(?P<list>[^<]*)</span>',
        html,
        re.DOTALL,
    )
    assert match, f"no 'Supported Extensions' block found in {stem}_content.html"
    return set(EXTENSION_TOKEN.findall(match.group("list")))


def readme_extensions() -> dict:
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()

    start = next(
        (i for i, line in enumerate(lines) if line.strip() == README_HEADING), None
    )
    assert start is not None, f"README.md no longer has a '{README_HEADING}' heading"

    found = {label: set() for label in README_CATEGORIES}
    for line in lines[start + 1 :]:
        text = line.strip()
        if text.startswith("#"):
            break
        if not text.startswith("|"):
            continue
        cells = [cell.strip() for cell in text.strip("|").split("|")]
        if cells and cells[0] in README_CATEGORIES:
            found[cells[0]].update(EXTENSION_TOKEN.findall(cells[1]))
    return found


@pytest.mark.parametrize("stem", sorted(TABS))
def test_each_media_tab_lists_exactly_what_it_routes(stem):
    assert tab_extensions(stem) == set(TABS[stem]), (
        f"the {stem} tab and media_classifier disagree: the GUI promises what "
        "the router does not accept, or hides what it does"
    )


@pytest.mark.parametrize("label", sorted(README_CATEGORIES))
def test_readme_lists_exactly_what_the_app_routes(label):
    assert readme_extensions()[label] == set(README_CATEGORIES[label]), (
        f"README.md's '{label}' row and media_classifier disagree - a user "
        "reads that list before installing, so it has to be honest"
    )


def test_no_extension_is_claimed_by_two_categories():
    seen = {}
    for name, extensions in README_CATEGORIES.items():
        for extension in extensions:
            assert extension not in seen, (
                f"{extension} is claimed by both {seen[extension]} and {name}"
            )
            seen[extension] = name



PILLOW_ROUTED = IMAGE_EXTENSIONS | ANIMATED_IMAGE_EXTENSIONS


def registered_format_for(extension: str):
    to_jpeg_converter._lazy_load_image_plugins()
    Image.init()
    return Image.registered_extensions().get(extension)


@pytest.mark.parametrize("extension", sorted(PILLOW_ROUTED))
def test_every_routed_image_extension_opens_through_the_allowlist(extension):
    decoded_as = registered_format_for(extension)
    assert decoded_as is not None, (
        f"no loaded Pillow plugin registers {extension} - the classifier "
        "routes it to a Pillow pipeline that cannot possibly open it"
    )
    assert decoded_as in to_jpeg_converter.SUPPORTED_OPEN_FORMATS, (
        f"{extension} decodes as {decoded_as}, which open_supported_image "
        "refuses - every such file would fail; add the format to "
        "SUPPORTED_OPEN_FORMATS or drop the extension from the router"
    )


def test_the_allowlist_carries_no_format_no_routed_extension_needs():
    needed = {registered_format_for(extension) for extension in PILLOW_ROUTED}
    stale = set(to_jpeg_converter.SUPPORTED_OPEN_FORMATS) - needed
    assert stale == set(), (
        "no routed extension needs these formats any more, and an allowlist "
        f"entry nothing needs is attack surface: {sorted(stale)}"
    )
