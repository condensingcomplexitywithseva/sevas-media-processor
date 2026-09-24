# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import shutil
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import central_logger
import config_loader
from app_context import ProcessorCore
from config_loader import ConfigManager, TokenManager
from user_data import Bucket, SETTINGS_FILE_NAME, moved_by_user, user_paths

from test_readme_step_lists import backticked, readme_slug, steps_under

UPDATE_LIST = "Update with the install script"
EDITED_QUALITY = 77
EARLIER_ARCHIVE = "old_current_run_2026-01-01_00-00-00"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    app_data = tmp_path / "appdata"
    app_data.mkdir()
    monkeypatch.setattr(central_logger, "get_app_data_dir", lambda: app_data)
    monkeypatch.setattr(central_logger, "_configured", False)
    for key in list(__import__("os").environ):
        if key.endswith("_TOKEN"):
            monkeypatch.delenv(key)
    return tmp_path, app_data


def manager_for(root, app_data):
    manager = ConfigManager(root)
    manager.app_data_dir = app_data
    manager.env_path = app_data / ".env"
    manager.token_manager = TokenManager(manager.env_path)
    return manager


def boot_and_run(root, app_data, monkeypatch):
    monkeypatch.chdir(root)
    manager = manager_for(root, app_data)
    monkeypatch.setattr(config_loader, "_manager", manager)
    settings = manager.load_strict()
    events = []
    core = ProcessorCore(settings, threading.Event(), on_progress=events.append)
    core.run()
    return settings, events


def tree_hashes(folder):
    return {
        str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*")) if path.is_file()
    }


def build_old_install(root, folder_name, app_data, monkeypatch):
    old = root / folder_name
    (old / "input" / "holiday").mkdir(parents=True)
    Image.new("RGB", (24, 24), (10, 200, 30)).save(old / "input" / "photo.png")
    Image.new("RGB", (24, 24), (200, 30, 10)).save(old / "input" / "holiday" / "beach.png")
    (old / SETTINGS_FILE_NAME).write_text(json.dumps({
        "INPUT_FOLDER_PATH": "input",
        "OUTPUT_FOLDER_PATH": "output",
        "JPEG_QUALITY": EDITED_QUALITY,
        "START_OVER": True,
    }), encoding="utf-8")
    (old / "venv" / "Scripts").mkdir(parents=True)
    (old / "venv" / "Scripts" / "python.exe").write_bytes(b"not really")
    (old / "notes.txt").write_text("the user's own note", encoding="utf-8")
    _, events = boot_and_run(old, app_data, monkeypatch)
    assert events[-1] == {"type": "done"}, events[-3:]
    monkeypatch.chdir(root)
    (old / "output" / EARLIER_ARCHIVE).mkdir()
    (old / "output" / EARLIER_ARCHIVE / "marker.txt").write_text("earlier", encoding="utf-8")
    return old


def build_new_version(root):
    new = root / "downloads" / f"{readme_slug()}-main"
    new.mkdir(parents=True)
    for name in ("README.md", "install.ps1", "settings.example.json",
                 "requirements.lock", "requirements_no_version.txt"):
        shutil.copy2(REPO_ROOT / name, new / name)
    for entry in user_paths():
        assert not (new / entry.name).exists()
    return new


def follow_the_readme(old, new, steps, move_names):
    assert steps[0].startswith("Rename your current application folder by adding `-old`")
    renamed_old = old.with_name(old.name + "-old")
    old.rename(renamed_old)
    assert steps[3].startswith("Move the extracted")
    placed = old.parent / new.name
    shutil.move(str(new), str(placed))
    assert steps[4].startswith("Rename the new folder to the old folder's name without the `-old`")
    final = old.parent / renamed_old.name.removesuffix("-old")
    placed.rename(final)
    assert steps[5].startswith("Move `")
    for name in move_names:
        shutil.move(str(renamed_old / name), str(final / name))
    return renamed_old, final


@pytest.mark.parametrize("folder_name", ["sevas-media-processor-main", "my_app"])
def test_following_the_update_list_keeps_every_user_owned_byte(sandbox, monkeypatch, folder_name):
    root, app_data = sandbox
    old = build_old_install(root, folder_name, app_data, monkeypatch)
    before_input = tree_hashes(old / "input")
    before_output = tree_hashes(old / "output")
    before_settings = (old / SETTINGS_FILE_NAME).read_bytes()
    new = build_new_version(root)
    steps = steps_under(UPDATE_LIST)
    move_step = next(s for s in steps if s.startswith("Move `"))

    renamed_old, final = follow_the_readme(old, new, steps, backticked(move_step))

    assert final == root / folder_name
    assert (final / SETTINGS_FILE_NAME).read_bytes() == before_settings
    assert tree_hashes(final / "input") == before_input
    assert tree_hashes(final / "output") == before_output
    for entry in user_paths():
        if entry.bucket is Bucket.MOVED_BY_USER:
            assert (final / entry.name).exists() and not (renamed_old / entry.name).exists()
        elif entry.bucket is Bucket.REBUILT_BY_INSTALLER:
            assert (renamed_old / entry.name).exists() and not (final / entry.name).exists()
    assert (renamed_old / "notes.txt").exists() and not (final / "notes.txt").exists()

    settings, events = boot_and_run(final, app_data, monkeypatch)
    assert settings.JPEG_QUALITY == EDITED_QUALITY, "settings.json was not the user's own"
    assert events[-1] == {"type": "done"}, events[-3:]
    archives = sorted(p.name for p in (final / "output").iterdir() if p.name.startswith("old_"))
    assert EARLIER_ARCHIVE in archives and len(archives) == 2, archives
    assert (final / "output" / EARLIER_ARCHIVE / "marker.txt").read_text(encoding="utf-8") == "earlier"
    assert (final / "output" / "current_run" / "TECH" / "application_state.db").exists()
    assert tree_hashes(final / "input") == before_input, "a run must never touch the media"


def test_resuming_after_the_swap_works_too(sandbox, monkeypatch):
    root, app_data = sandbox
    old = build_old_install(root, "sevas-media-processor-main", app_data, monkeypatch)
    settings_file = old / SETTINGS_FILE_NAME
    data = json.loads(settings_file.read_text(encoding="utf-8"))
    data["START_OVER"] = False
    settings_file.write_text(json.dumps(data), encoding="utf-8")
    new = build_new_version(root)
    steps = steps_under(UPDATE_LIST)
    move_step = next(s for s in steps if s.startswith("Move `"))
    _, final = follow_the_readme(old, new, steps, backticked(move_step))

    settings, events = boot_and_run(final, app_data, monkeypatch)
    assert settings.JPEG_QUALITY == EDITED_QUALITY
    assert events[-1] == {"type": "done"}, events[-3:]
    archives = [p.name for p in (final / "output").iterdir() if p.name.startswith("old_")]
    assert archives == [EARLIER_ARCHIVE], "a resume must not archive the carried run"


def test_positive_control_leaving_a_path_behind_is_caught(sandbox, monkeypatch):
    root, app_data = sandbox
    old = build_old_install(root, "my_app", app_data, monkeypatch)
    new = build_new_version(root)
    steps = steps_under(UPDATE_LIST)
    incomplete = [name for name in moved_by_user() if name != SETTINGS_FILE_NAME]
    _, final = follow_the_readme(old, new, steps, incomplete)
    settings, _ = boot_and_run(final, app_data, monkeypatch)
    with pytest.raises(AssertionError):
        assert settings.JPEG_QUALITY == EDITED_QUALITY
