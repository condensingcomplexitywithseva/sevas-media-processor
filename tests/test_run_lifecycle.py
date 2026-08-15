# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
import threading
from pathlib import Path

from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import app_context
import central_logger
from app_context import ProcessorCore
from config_validator import Settings
from schemas import ConfigurationError


@pytest.fixture
def make_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(central_logger, "setup_logging", lambda *a, **k: None)

    def build(**overrides) -> Settings:
        (tmp_path / "input").mkdir(exist_ok=True)
        values: dict[str, Any] = {
            "INPUT_FOLDER_PATH": str(tmp_path / "input"),
            "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
            "ENABLE_LLM_INFERENCE": False,
        }
        values.update(overrides)
        return Settings(**values)

    return build


def without_the_shell_fallback(monkeypatch):
    monkeypatch.setattr(app_context.windows_shell, "is_available", lambda: False)


def deny_shell_rename(monkeypatch, calls):
    def refuse(source, target):
        calls.append(Path(target))
        raise OSError(
            0, "The process cannot access the file", str(source), 32, str(target)
        )

    monkeypatch.setattr(
        app_context.windows_shell, "rename_folder_like_explorer", refuse
    )


def assert_shutdown_released_the_db(settings):
    db_path = settings.TECH_FOLDER_PATH / "application_state.db"
    moved = db_path.with_name("proves_handle_released.db")
    db_path.rename(moved)
    moved.rename(db_path)



def test_successful_run_ends_with_100_then_done_and_shuts_down(make_settings, monkeypatch):
    settings = make_settings()
    events = []
    core = ProcessorCore(settings, threading.Event(), on_progress=events.append)
    monkeypatch.setattr(core.orchestrator, "execute_batch_processing_loop", lambda **kw: None)

    core.run()

    assert events[-2:] == [{"type": "progress", "value": 100}, {"type": "done"}]
    assert_shutdown_released_the_db(settings)


def test_crashed_run_ends_with_failed_never_done(make_settings, monkeypatch):
    settings = make_settings()
    events = []
    core = ProcessorCore(settings, threading.Event(), on_progress=events.append)

    def explode(**kwargs):
        raise RuntimeError("mid-run disaster")

    monkeypatch.setattr(core.orchestrator, "execute_batch_processing_loop", explode)

    core.run()

    event_types = [event["type"] for event in events]
    assert event_types[-1] == "failed"
    assert "done" not in event_types
    assert {"type": "progress", "value": 100} not in events
    assert_shutdown_released_the_db(settings)



def test_start_over_archives_the_previous_run_with_a_suffix_on_collision(
    make_settings, monkeypatch, tmp_path
):
    import datetime as datetime_module

    real_datetime = datetime_module.datetime

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 7, 5, 12, 0, 0)

    monkeypatch.setattr(datetime_module, "datetime", FrozenDatetime)

    settings = make_settings(START_OVER=True)
    output_folder = Path(str(settings.OUTPUT_FOLDER_PATH))

    settings.CURRENT_RUN_FOLDER.mkdir(parents=True)
    (settings.CURRENT_RUN_FOLDER / "first_run.jpg").write_bytes(b"precious")
    core = ProcessorCore(settings, threading.Event())
    core.shutdown()

    archives = sorted(p.name for p in output_folder.glob("old_current_run_*"))
    assert archives == ["old_current_run_2026-07-05_12-00-00"]
    assert (output_folder / archives[0] / "first_run.jpg").read_bytes() == b"precious"
    assert settings.CURRENT_RUN_FOLDER.exists()
    assert not (settings.CURRENT_RUN_FOLDER / "first_run.jpg").exists()

    (settings.CURRENT_RUN_FOLDER / "second_run.jpg").write_bytes(b"also precious")
    core = ProcessorCore(settings, threading.Event())
    core.shutdown()

    archives = sorted(p.name for p in output_folder.glob("old_current_run_*"))
    assert archives == [
        "old_current_run_2026-07-05_12-00-00",
        "old_current_run_2026-07-05_12-00-00-2",
    ]
    assert (output_folder / archives[0] / "first_run.jpg").exists()
    assert (output_folder / archives[1] / "second_run.jpg").exists()


def test_start_over_with_an_empty_previous_folder_archives_nothing(make_settings):
    settings = make_settings(START_OVER=True)
    settings.CURRENT_RUN_FOLDER.mkdir(parents=True)

    core = ProcessorCore(settings, threading.Event())
    core.shutdown()

    output_folder = Path(str(settings.OUTPUT_FOLDER_PATH))
    assert list(output_folder.glob("old_current_run_*")) == []



def test_locked_previous_run_folder_refuses_with_localizable_error(
    make_settings, monkeypatch
):
    settings = make_settings(START_OVER=True)
    settings.CURRENT_RUN_FOLDER.mkdir(parents=True)
    (settings.CURRENT_RUN_FOLDER / "locked.xlsx").write_bytes(b"open in excel")

    def refuse(self, target):
        raise OSError("[WinError 32] The process cannot access the file")

    monkeypatch.setattr(Path, "rename", refuse)
    monkeypatch.setattr(app_context.time, "sleep", lambda seconds: None)
    without_the_shell_fallback(monkeypatch)

    with pytest.raises(ConfigurationError) as raised:
        ProcessorCore(settings, threading.Event())
    assert str(raised.value).startswith("i18n:err_archive_locked|")


def test_locked_folder_error_shows_real_paths_not_python_escapes(
    make_settings, monkeypatch
):
    settings = make_settings(START_OVER=True)
    settings.CURRENT_RUN_FOLDER.mkdir(parents=True)
    (settings.CURRENT_RUN_FOLDER / "locked.xlsx").write_bytes(b"open in excel")

    source = Path(str(settings.CURRENT_RUN_FOLDER))
    target = source.with_name("old_current_run_2026-08-05_04-15-37")

    def refuse(self, _target):
        raise OSError(13, "Access is denied", str(source), 5, str(target))

    monkeypatch.setattr(Path, "rename", refuse)
    monkeypatch.setattr(app_context.time, "sleep", lambda seconds: None)
    without_the_shell_fallback(monkeypatch)

    with pytest.raises(ConfigurationError) as raised:
        ProcessorCore(settings, threading.Event())

    detail = str(raised.value)
    assert detail.startswith("i18n:err_archive_locked|")
    assert str(source) in detail
    assert str(target) in detail
    assert "\\\\" not in detail
    assert "'" not in detail


def test_locked_folder_error_survives_an_exception_with_no_details(
    make_settings, monkeypatch
):
    settings = make_settings(START_OVER=True)
    settings.CURRENT_RUN_FOLDER.mkdir(parents=True)
    (settings.CURRENT_RUN_FOLDER / "locked.xlsx").write_bytes(b"open in excel")

    def refuse(self, target):
        raise OSError("the disk went to lunch")

    monkeypatch.setattr(Path, "rename", refuse)
    monkeypatch.setattr(app_context.time, "sleep", lambda seconds: None)
    without_the_shell_fallback(monkeypatch)

    with pytest.raises(ConfigurationError) as raised:
        ProcessorCore(settings, threading.Event())

    detail = str(raised.value).split("|", 1)[1]
    assert detail == "the disk went to lunch"



def patch_current_run_rename(settings, monkeypatch, failures, calls):
    real_rename = Path.rename
    current_run = Path(str(settings.CURRENT_RUN_FOLDER)).resolve()

    def is_the_archive_rename(path):
        text = str(path)
        if text.startswith("\\\\?\\"):
            text = text[4:]
        return Path(text) == current_run

    def fake_rename(self, target):
        if not is_the_archive_rename(self):
            return real_rename(self, target)
        calls.append(Path(target))
        if len(calls) <= failures:
            raise OSError(13, "Access is denied", str(self), 5, str(target))
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", fake_rename)
    return real_rename


def test_transient_lock_is_absorbed_by_the_retry_loop(make_settings, monkeypatch):
    settings = make_settings(START_OVER=True)
    settings.CURRENT_RUN_FOLDER.mkdir(parents=True)
    (settings.CURRENT_RUN_FOLDER / "first_run.jpg").write_bytes(b"precious")

    calls, sleeps = [], []
    patch_current_run_rename(settings, monkeypatch, failures=2, calls=calls)
    monkeypatch.setattr(app_context.time, "sleep", sleeps.append)
    without_the_shell_fallback(monkeypatch)

    core = ProcessorCore(settings, threading.Event())
    core.shutdown()

    output_folder = Path(str(settings.OUTPUT_FOLDER_PATH))
    archives = list(output_folder.glob("old_current_run_*"))
    assert len(archives) == 1
    assert (archives[0] / "first_run.jpg").read_bytes() == b"precious"
    assert settings.CURRENT_RUN_FOLDER.exists()
    assert not (settings.CURRENT_RUN_FOLDER / "first_run.jpg").exists()

    assert len(calls) == 3
    assert sleeps == [app_context.ARCHIVE_RENAME_RETRY_DELAY_SECONDS] * 2


def test_persistent_lock_refuses_after_exactly_the_budgeted_attempts(
    make_settings, monkeypatch
):
    settings = make_settings(START_OVER=True)
    settings.CURRENT_RUN_FOLDER.mkdir(parents=True)
    (settings.CURRENT_RUN_FOLDER / "locked.xlsx").write_bytes(b"open in excel")

    calls, sleeps = [], []
    patch_current_run_rename(
        settings, monkeypatch, failures=app_context.ARCHIVE_RENAME_ATTEMPTS, calls=calls
    )
    monkeypatch.setattr(app_context.time, "sleep", sleeps.append)
    without_the_shell_fallback(monkeypatch)

    with pytest.raises(ConfigurationError) as raised:
        ProcessorCore(settings, threading.Event())

    assert str(raised.value).startswith("i18n:err_archive_locked|")
    assert len(calls) == app_context.ARCHIVE_RENAME_ATTEMPTS
    assert len(sleeps) == app_context.ARCHIVE_RENAME_ATTEMPTS - 1
    assert isinstance(raised.value.__cause__, OSError)
    assert raised.value.__cause__.filename2 == str(calls[-1])


def test_first_try_success_renames_once_and_never_sleeps(make_settings, monkeypatch):
    settings = make_settings(START_OVER=True)
    settings.CURRENT_RUN_FOLDER.mkdir(parents=True)
    (settings.CURRENT_RUN_FOLDER / "first_run.jpg").write_bytes(b"precious")

    calls, sleeps = [], []
    patch_current_run_rename(settings, monkeypatch, failures=0, calls=calls)
    monkeypatch.setattr(app_context.time, "sleep", sleeps.append)

    core = ProcessorCore(settings, threading.Event())
    core.shutdown()

    assert len(calls) == 1
    assert sleeps == []


def test_stop_pressed_mid_retry_refuses_without_waiting_out_the_budget(
    make_settings, monkeypatch
):
    settings = make_settings(START_OVER=True)
    settings.CURRENT_RUN_FOLDER.mkdir(parents=True)
    (settings.CURRENT_RUN_FOLDER / "locked.xlsx").write_bytes(b"open in excel")

    calls, sleeps = [], []
    patch_current_run_rename(
        settings, monkeypatch, failures=app_context.ARCHIVE_RENAME_ATTEMPTS, calls=calls
    )
    monkeypatch.setattr(app_context.time, "sleep", sleeps.append)
    without_the_shell_fallback(monkeypatch)

    abort_flag = threading.Event()
    abort_flag.set()

    with pytest.raises(ConfigurationError) as raised:
        ProcessorCore(settings, abort_flag)

    assert str(raised.value).startswith("i18n:err_archive_locked|")
    assert len(calls) == 1
    assert sleeps == []



def test_a_folder_held_by_explorer_is_archived_through_the_shell(
    make_settings, monkeypatch
):
    settings = make_settings(START_OVER=True)
    settings.CURRENT_RUN_FOLDER.mkdir(parents=True)
    (settings.CURRENT_RUN_FOLDER / "first_run.jpg").write_bytes(b"precious")

    calls, sleeps, shell_calls = [], [], []
    real_rename = patch_current_run_rename(
        settings, monkeypatch, failures=app_context.ARCHIVE_RENAME_ATTEMPTS, calls=calls
    )
    monkeypatch.setattr(app_context.time, "sleep", sleeps.append)

    def shell_rename(source, target):
        shell_calls.append(Path(target))
        real_rename(Path(source), target)

    monkeypatch.setattr(
        app_context.windows_shell, "rename_folder_like_explorer", shell_rename
    )

    core = ProcessorCore(settings, threading.Event())
    core.shutdown()

    output_folder = Path(str(settings.OUTPUT_FOLDER_PATH))
    archives = list(output_folder.glob("old_current_run_*"))
    assert len(archives) == 1
    assert (archives[0] / "first_run.jpg").read_bytes() == b"precious"
    assert settings.CURRENT_RUN_FOLDER.exists()
    assert not (settings.CURRENT_RUN_FOLDER / "first_run.jpg").exists()

    assert len(calls) == 1
    assert len(shell_calls) == 1
    assert sleeps == []


def test_the_shell_rename_is_attempted_once_not_on_every_retry(
    make_settings, monkeypatch
):
    settings = make_settings(START_OVER=True)
    settings.CURRENT_RUN_FOLDER.mkdir(parents=True)
    (settings.CURRENT_RUN_FOLDER / "locked.xlsx").write_bytes(b"open in excel")

    calls, sleeps, shell_calls = [], [], []
    patch_current_run_rename(
        settings, monkeypatch, failures=app_context.ARCHIVE_RENAME_ATTEMPTS, calls=calls
    )
    monkeypatch.setattr(app_context.time, "sleep", sleeps.append)
    deny_shell_rename(monkeypatch, shell_calls)

    with pytest.raises(ConfigurationError) as raised:
        ProcessorCore(settings, threading.Event())

    assert str(raised.value).startswith("i18n:err_archive_locked|")
    assert len(calls) == app_context.ARCHIVE_RENAME_ATTEMPTS
    assert len(shell_calls) == 1, "the shell route is a one-shot, not a per-attempt retry"
    assert len(sleeps) == app_context.ARCHIVE_RENAME_ATTEMPTS - 1


def test_the_refusal_blames_the_shell_failure_when_both_routes_fail(
    make_settings, monkeypatch
):
    settings = make_settings(START_OVER=True)
    settings.CURRENT_RUN_FOLDER.mkdir(parents=True)
    (settings.CURRENT_RUN_FOLDER / "locked.xlsx").write_bytes(b"open in excel")

    calls, shell_calls = [], []
    patch_current_run_rename(
        settings, monkeypatch, failures=app_context.ARCHIVE_RENAME_ATTEMPTS, calls=calls
    )
    monkeypatch.setattr(app_context.time, "sleep", lambda seconds: None)
    deny_shell_rename(monkeypatch, shell_calls)

    abort_flag = threading.Event()
    abort_flag.set()

    with pytest.raises(ConfigurationError) as raised:
        ProcessorCore(settings, abort_flag)

    cause = raised.value.__cause__
    assert isinstance(cause, OSError)
    assert cause.strerror == "The process cannot access the file"
    detail = str(raised.value)
    assert str(Path(str(settings.CURRENT_RUN_FOLDER))) in detail
    assert "\\\\" not in detail and "'" not in detail
