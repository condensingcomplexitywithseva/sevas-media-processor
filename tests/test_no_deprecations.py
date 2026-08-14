# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import logging
import re
import sys
import warnings
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import main as main_module


def _deprecation_messages(caplog_records, caught_warnings):
    offenders = [
        r.getMessage()
        for r in caplog_records
        if "deprecat" in r.getMessage().lower()
    ]
    for w in caught_warnings:
        if (
            issubclass(w.category, (DeprecationWarning, PendingDeprecationWarning))
            or "deprecat" in str(w.message).lower()
        ):
            offenders.append(str(w.message))
    return offenders


def test_the_capture_sees_a_real_deprecated_access(caplog):
    import webview

    with caplog.at_level(logging.WARNING, logger="pywebview"):
        assert webview.OPEN_DIALOG == webview.FileDialog.OPEN

    assert any(
        "deprecat" in r.getMessage().lower() for r in caplog.records
    ), (
        "touching webview.OPEN_DIALOG no longer emits a deprecation log "
        "record - pywebview changed its deprecation mechanism (or removed "
        "the constant), so re-verify how deprecations surface and point "
        "this net at the new mechanism"
    )


@pytest.mark.parametrize(
    "method, member",
    [("browse_file", "OPEN"), ("browse_folder", "FOLDER")],
)
def test_pickers_use_no_deprecated_pywebview_api(caplog, method, member):
    import webview

    api = main_module.Api()
    recorded = {}

    class FakeWindow:
        def create_file_dialog(self, dialog_type, **kwargs):
            recorded["dialog_type"] = dialog_type
            return None

    api._window = FakeWindow()

    with caplog.at_level(logging.WARNING):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = getattr(api, method)()

    assert result is None
    assert recorded["dialog_type"] is getattr(webview.FileDialog, member)
    offenders = _deprecation_messages(caplog.records, caught)
    assert not offenders, (
        f"Api.{method} touched a deprecated dependency API: {offenders}"
    )


DEPRECATED_PYWEBVIEW_NAMES = (
    "OPEN_DIALOG",
    "FOLDER_DIALOG",
    "SAVE_DIALOG",
    "DRAG_REGION_SELECTOR",
    "set_window_size",
    "mshtml",
)


def _sweep_for_deprecated_names(root):
    hits = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in DEPRECATED_PYWEBVIEW_NAMES:
            if re.search(rf"\b{name}\b", text, re.IGNORECASE):
                hits.append(f"{path.name}: {name}")
    return hits


def test_src_names_no_deprecated_pywebview_surface():
    assert _sweep_for_deprecated_names(SRC) == []


def test_the_sweep_actually_sweeps(tmp_path):
    (tmp_path / "planted.py").write_text(
        "dialog = webview.OPEN_DIALOG\n", encoding="utf-8"
    )
    assert _sweep_for_deprecated_names(tmp_path) == ["planted.py: OPEN_DIALOG"]
