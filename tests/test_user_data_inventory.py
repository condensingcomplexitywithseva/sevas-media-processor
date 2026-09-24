# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import user_data
from config_loader import ConfigManager
from config_validator import Settings
from user_data import Bucket, Site, moved_by_user, user_paths

from test_readme_step_lists import backticked, steps_under

INSTALL_SCRIPT = REPO_ROOT / "install.ps1"
GITIGNORE = REPO_ROOT / ".gitignore"

SCRIPT_FOLDERS = re.compile(r'foreach \(\$dir in @\(((?:"[^"]+"(?:,\s*)?)+)\)\)')
SCRIPT_SETTINGS_COPY = re.compile(
    r'\[System\.IO\.File\]::Copy\("\$InstallDir\\settings\.example\.json", "\$InstallDir\\([^"]+)"\)'
)
SCRIPT_VENV = re.compile(r'-m venv "\$InstallDir\\([^"]+)"')


def registry_names(bucket):
    return [p.name for p in user_paths() if p.bucket is bucket]



def test_every_entry_is_classified_with_a_reason():
    for entry in user_paths():
        assert isinstance(entry.bucket, Bucket) and isinstance(entry.site, Site)
        assert entry.reason.strip(), entry


def test_the_moved_bucket_is_settings_input_output_in_that_order():
    assert moved_by_user() == ("settings.json", "input", "output")


def test_the_runtime_spells_the_names_through_the_registry(tmp_path):
    manager = ConfigManager(tmp_path)
    assert manager.settings_path == tmp_path / user_data.SETTINGS_FILE_NAME
    assert manager.env_path.name == user_data.ENV_FILE_NAME
    defaults = Settings()
    assert str(defaults.INPUT_FOLDER_PATH) == user_data.DEFAULT_INPUT_FOLDER
    assert str(defaults.OUTPUT_FOLDER_PATH) == user_data.DEFAULT_OUTPUT_FOLDER
    for module, literal in (("central_logger.py", '"logs"'),
                            ("main.py", '"MEDIA_PROCESSOR_CRASH_LOG.txt"'),
                            ("main.py", '"settings.json"'),
                            ("config_loader.py", '"settings.json"'),
                            ("config_loader.py", '".env"')):
        source = (SRC / module).read_text(encoding="utf-8")
        assert literal not in source, (
            f"src/{module} spells {literal} by hand; import the name from user_data"
        )



def test_the_readme_moves_exactly_the_moved_bucket():
    for heading in ("Update with the install script", "Update by hand"):
        move_steps = [s for s in steps_under(heading) if s.startswith("Move `")]
        assert len(move_steps) == 1, (heading, move_steps)
        assert tuple(backticked(move_steps[0])) == moved_by_user(), (heading, move_steps[0])


def test_the_readme_names_the_rebuilt_folder_in_the_git_line():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert f"delete the `{user_data.VENV_DIR_NAME}` folder" in text



def test_the_installer_creates_exactly_the_moved_folders_and_the_settings_file():
    script = INSTALL_SCRIPT.read_text(encoding="utf-8")
    folders = SCRIPT_FOLDERS.search(script)
    assert folders, "install.ps1 no longer creates its first-run folders in a foreach"
    created = [name.strip().strip('"') for name in folders.group(1).split(",")]
    settings_copy = SCRIPT_SETTINGS_COPY.search(script)
    assert settings_copy, "install.ps1 no longer copies the settings template with a literal .NET copy"
    assert [*created, settings_copy.group(1)] == [
        user_data.DEFAULT_INPUT_FOLDER, user_data.DEFAULT_OUTPUT_FOLDER,
        user_data.SETTINGS_FILE_NAME,
    ]
    venv = SCRIPT_VENV.search(script)
    assert venv and venv.group(1) == user_data.VENV_DIR_NAME
    assert registry_names(Bucket.REBUILT_BY_INSTALLER) == [user_data.VENV_DIR_NAME]



def gitignore_entries():
    return [
        line.strip() for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_every_in_folder_user_path_is_ignored_by_git():
    entries = gitignore_entries()
    for entry in user_paths():
        if entry.site is not Site.APP_FOLDER:
            continue
        spelled = entry.name if "." in entry.name else entry.name + "/"
        assert spelled in entries, f".gitignore no longer lists {spelled}"


def test_no_user_owned_path_is_tracked():
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not on PATH")
    tracked = subprocess.run(
        [git, "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True,
    ).stdout.decode("utf-8").split("\0")
    for name in (*moved_by_user(), user_data.VENV_DIR_NAME):
        offenders = [p for p in tracked if p == name or p.startswith(name + "/")]
        assert not offenders, f"user-owned path tracked by git: {offenders[:5]}"


def test_the_panic_log_is_the_only_diagnostic_and_lives_at_three_sites():
    diagnostics = [p for p in user_paths() if p.bucket is Bucket.DIAGNOSTIC_NOT_CARRIED]
    assert {p.name for p in diagnostics} == {user_data.PANIC_LOG_NAME}
    assert [p.site for p in diagnostics] == [Site.OUTPUT_FOLDER, Site.OUTPUT_PARENT, Site.HOME]
