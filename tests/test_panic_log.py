# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import main as main_module

LOG_NAME = "MEDIA_PROCESSOR_CRASH_LOG.txt"
CRASH_DETAILS = "Traceback (most recent call last): boom at boot"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    app_root.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return app_root, fake_home


def write_settings(app_root, output_folder):
    (app_root / "settings.json").write_text(
        json.dumps({"OUTPUT_FOLDER_PATH": str(output_folder)}), encoding="utf-8"
    )


def test_crash_log_lands_in_the_configured_output_folder(sandbox, tmp_path):
    app_root, _ = sandbox
    output_folder = tmp_path / "run_output"
    write_settings(app_root, output_folder)

    main_module.write_fatal_panic_log(CRASH_DETAILS, app_root=app_root)

    content = (output_folder / LOG_NAME).read_text(encoding="utf-8")
    assert CRASH_DETAILS in content
    assert "CRITICAL STARTUP CRASH" in content
    assert "Trapped Pre-Boot System Logs" in content


def test_unwritable_output_folder_falls_back_beside_it(sandbox, tmp_path):
    app_root, _ = sandbox
    blocked = tmp_path / "blocked_output"
    blocked.write_text("a file where the output folder should be")
    write_settings(app_root, blocked)

    main_module.write_fatal_panic_log(CRASH_DETAILS, app_root=app_root)

    assert CRASH_DETAILS in (blocked.parent / LOG_NAME).read_text(encoding="utf-8")


def test_missing_settings_falls_back_to_the_desktop(sandbox):
    app_root, fake_home = sandbox
    (fake_home / "Desktop").mkdir()

    main_module.write_fatal_panic_log(CRASH_DETAILS, app_root=app_root)

    assert CRASH_DETAILS in (fake_home / "Desktop" / LOG_NAME).read_text(encoding="utf-8")


def test_no_desktop_falls_back_to_the_home_folder(sandbox):
    app_root, fake_home = sandbox

    main_module.write_fatal_panic_log(CRASH_DETAILS, app_root=app_root)

    assert CRASH_DETAILS in (fake_home / LOG_NAME).read_text(encoding="utf-8")


def test_corrupt_settings_json_still_writes_a_log(sandbox):
    app_root, fake_home = sandbox
    (app_root / "settings.json").write_text("{not valid json", encoding="utf-8")

    main_module.write_fatal_panic_log(CRASH_DETAILS, app_root=app_root)

    assert CRASH_DETAILS in (fake_home / LOG_NAME).read_text(encoding="utf-8")


def test_never_raises_even_when_every_location_is_unwritable(sandbox):
    app_root, fake_home = sandbox
    (fake_home / LOG_NAME).mkdir()

    main_module.write_fatal_panic_log(CRASH_DETAILS, app_root=app_root)
