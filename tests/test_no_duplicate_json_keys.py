# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCALES_DIR = REPO_ROOT / "src" / "locales"

LOCALE_FILES = sorted(LOCALES_DIR.glob("*.json"))
HAND_EDITED = [*LOCALE_FILES, REPO_ROOT / "settings.example.json"]


def duplicate_keys(text):
    dupes = []

    def refuse_duplicates(pairs):
        seen = {}
        for key, value in pairs:
            if key in seen:
                dupes.append(key)
            seen[key] = value
        return seen

    json.loads(text, object_pairs_hook=refuse_duplicates)
    return dupes


def test_the_locale_folder_was_actually_scanned():
    assert len(LOCALE_FILES) >= 2, (
        f"only {len(LOCALE_FILES)} locale files found in {LOCALES_DIR} - "
        "either the folder moved (fix the path here) or locales were lost"
    )


def test_the_detector_notices_duplicates_at_every_depth():
    assert duplicate_keys('{"a": 1, "b": 2, "a": 3}') == ["a"]
    assert duplicate_keys('{"outer": {"x": 1, "x": 2}}') == ["x"]
    assert duplicate_keys('[{"k": 1, "k": 2}]') == ["k"]
    assert duplicate_keys('{"a": 1, "b": {"a": 2}}') == []


@pytest.mark.parametrize("path", HAND_EDITED, ids=lambda p: p.name)
def test_hand_edited_json_has_no_duplicate_keys(path):
    dupes = duplicate_keys(path.read_text(encoding="utf-8"))
    assert not dupes, (
        f"{path.name} defines these keys more than once: {sorted(set(dupes))}. "
        "json.load keeps only the last one, so the earlier definitions are "
        "dead text that misleads the next editor - delete the stale copies."
    )
