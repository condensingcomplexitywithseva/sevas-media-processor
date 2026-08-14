# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import windows_shell

on_windows = pytest.mark.skipif(
    not windows_shell.IS_WINDOWS, reason="the shell rename is a Windows API"
)


def test_availability_tracks_the_platform():
    assert windows_shell.is_available() is (sys.platform == "win32")


@on_windows
def test_it_renames_a_folder_with_its_contents(tmp_path):
    source = tmp_path / "current_run"
    (source / "TECH").mkdir(parents=True)
    (source / "page_1.jpg").write_bytes(b"a jpeg")
    (source / "TECH" / "application_state.db").write_bytes(b"a database")
    target = tmp_path / "old_current_run_2026-08-08_01-54-41"

    windows_shell.rename_folder_like_explorer(source, target)

    assert not source.exists()
    assert (target / "page_1.jpg").read_bytes() == b"a jpeg"
    assert (target / "TECH" / "application_state.db").read_bytes() == b"a database"


@on_windows
def test_a_missing_source_raises_oserror_carrying_both_paths(tmp_path):
    source = tmp_path / "never_existed"
    target = tmp_path / "old_never_existed"

    with pytest.raises(OSError) as raised:
        windows_shell.rename_folder_like_explorer(source, target)

    assert raised.value.filename == str(source)
    assert raised.value.filename2 == str(target)
    assert raised.value.strerror
    assert not target.exists()


@on_windows
def test_an_existing_target_is_never_silently_overwritten(tmp_path):
    source = tmp_path / "current_run"
    source.mkdir()
    (source / "new.jpg").write_bytes(b"new")
    target = tmp_path / "old_current_run"
    target.mkdir()
    (target / "precious.jpg").write_bytes(b"an earlier archive")

    try:
        windows_shell.rename_folder_like_explorer(source, target)
    except OSError:
        pass
    assert (target / "precious.jpg").read_bytes() == b"an earlier archive"


def test_it_refuses_rather_than_crashes_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_shell, "IS_WINDOWS", False)

    with pytest.raises(OSError):
        windows_shell.rename_folder_like_explorer(tmp_path / "a", tmp_path / "b")
