# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "install.ps1"
STEP_5_MARKER = 'Write-Host "5/5'
DESKTOP_LOOKUP = "[Environment]::GetFolderPath('Desktop')"
SHORTCUT_NAME = "Seva's Media Processor.lnk"

FAKE_PIP_MAIN = '''
import os
import sys
with open(os.environ["FAKE_PIP_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("FAKE_PIP_EXIT", "0")))
'''

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="install.ps1 is Windows-only")

POWERSHELL = shutil.which("powershell.exe")


def required_python():
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    major = re.search(r"^\$RequiredMajor\s*=\s*(\d+)", text, re.M)
    minor = re.search(r"^\$RequiredMinor\s*=\s*(\d+)", text, re.M)
    assert major and minor
    return int(major.group(1)), int(minor.group(1))


def script_text():
    return INSTALL_SCRIPT.read_text(encoding="utf-8")


def split_at_step_5():
    lines = script_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith(STEP_5_MARKER))
    return "\n".join(lines[:index]) + "\n", "\n".join(lines[index:]) + "\n"


def run_powershell(script_path, cwd, env, timeout=420):
    assert POWERSHELL is not None
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
        cwd=cwd, env=env, input="\n\n\n", capture_output=True, text=True, timeout=timeout,
    )


class Toolbox:

    def __init__(self, root):
        self.root = root
        base_python = Path(sys.base_prefix) / "python.exe"
        assert base_python.exists(), base_python
        fake_pip = root / "fakepip" / "pip"
        fake_pip.mkdir(parents=True)
        (fake_pip / "__init__.py").write_text("", encoding="utf-8")
        (fake_pip / "__main__.py").write_text(FAKE_PIP_MAIN, encoding="utf-8")
        home = root / "home"
        home.mkdir()
        windows = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        self.base_env = {
            key: value for key, value in os.environ.items()
            if key.upper() not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONSAFEPATH"}
        }
        self.base_env.update({
            "PATH": os.pathsep.join([str(base_python.parent), str(windows / "System32"), str(windows)]),
            "USERPROFILE": str(home),
            "PYTHONPATH": str(fake_pip.parent),
        })
        self.steps_1_to_4, self.step_5 = split_at_step_5()
        self.fresh_folder = self.app_folder("fresh")
        self.fresh_log = self.root / "fresh_pip_calls.log"
        self.fresh_result = self.run(self.fresh_folder, self.fresh_log)

    def app_folder(self, name, with_venv=False):
        folder = self.root / name
        folder.mkdir()
        for filename in ("settings.example.json", "requirements.lock", "requirements_no_version.txt"):
            shutil.copy2(REPO_ROOT / filename, folder / filename)
        (folder / "install_steps_1_to_4.ps1").write_text(self.steps_1_to_4, encoding="utf-8")
        if with_venv:
            shutil.copytree(self.fresh_folder / "venv", folder / "venv")
        return folder

    def run(self, folder, pip_log, pip_exit=0):
        env = {**self.base_env, "FAKE_PIP_LOG": str(pip_log), "FAKE_PIP_EXIT": str(pip_exit)}
        return run_powershell(folder / "install_steps_1_to_4.ps1", folder, env)


@pytest.fixture(scope="module")
def toolbox(tmp_path_factory):
    if POWERSHELL is None:
        pytest.skip("powershell.exe not found")
    if sys.version_info[:2] != required_python():
        pytest.skip(
            f"install.ps1 requires Python {required_python()}; this interpreter is "
            f"{sys.version_info[:2]} and the script would prompt"
        )
    return Toolbox(tmp_path_factory.mktemp("install_sandbox"))


def pip_calls(log):
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []



def test_a_fresh_folder_gets_venv_folders_settings_and_the_lock_installed(toolbox):
    folder, result = toolbox.fresh_folder, toolbox.fresh_result
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAILED" not in result.stdout
    assert "Virtual environment created" in result.stdout
    assert (folder / "venv" / "Scripts" / "python.exe").exists()
    assert (folder / "input").is_dir() and (folder / "output").is_dir()
    assert (folder / "settings.json").read_bytes() == (folder / "settings.example.json").read_bytes()
    calls = pip_calls(toolbox.fresh_log)
    assert calls == ["install --upgrade pip", f"install -r {folder}\\requirements.lock"], calls


def test_an_updated_folder_keeps_the_users_files_untouched(toolbox):
    folder = toolbox.app_folder("app [1]", with_venv=True)
    user_settings = b'{"JPEG_QUALITY": 77}'
    (folder / "settings.json").write_bytes(user_settings)
    (folder / "input" / "holiday").mkdir(parents=True)
    (folder / "input" / "holiday" / "beach.png").write_bytes(b"png-bytes")
    (folder / "output" / "current_run" / "TECH").mkdir(parents=True)
    (folder / "output" / "current_run" / "TECH" / "application_state.db").write_bytes(b"db-bytes")
    stamp = (folder / "venv" / "pyvenv.cfg").stat().st_mtime_ns

    result = toolbox.run(folder, toolbox.root / "updated_pip_calls.log")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (folder / "settings.json").read_bytes() == user_settings
    assert (folder / "input" / "holiday" / "beach.png").read_bytes() == b"png-bytes"
    assert (folder / "output" / "current_run" / "TECH" / "application_state.db").read_bytes() == b"db-bytes"
    assert "Virtual environment already exists" in result.stdout
    assert "Created settings.json" not in result.stdout
    assert "Created empty" not in result.stdout
    assert (folder / "venv" / "pyvenv.cfg").stat().st_mtime_ns == stamp
    assert pip_calls(toolbox.root / "updated_pip_calls.log") == [
        "install --upgrade pip", f"install -r {folder}\\requirements.lock",
    ]


def test_a_venv_without_the_users_files_gets_the_first_run_files(toolbox):
    folder = toolbox.app_folder("cloned", with_venv=True)
    result = toolbox.run(folder, toolbox.root / "cloned_pip_calls.log")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Virtual environment already exists" in result.stdout
    assert (folder / "settings.json").read_bytes() == (folder / "settings.example.json").read_bytes()
    assert (folder / "input").is_dir() and (folder / "output").is_dir()


def test_a_failed_pip_stops_the_script_honestly(toolbox):
    folder = toolbox.app_folder("broken_pip", with_venv=True)
    result = toolbox.run(folder, toolbox.root / "broken_pip_calls.log", pip_exit=1)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAILED: upgrading pip (exit code 1)" in result.stdout
    assert "INSTALLATION COMPLETE" not in result.stdout
    assert not (folder / "input").exists() and not (folder / "settings.json").exists()


def test_the_shortcut_step_writes_the_lnk_into_the_desktop_folder_windows_reports(toolbox, tmp_path):
    folder = toolbox.app_folder("shortcut")
    env = toolbox.base_env
    desktop = tmp_path / "onedrive" / "Desktop"
    desktop.mkdir(parents=True)
    step_5 = toolbox.step_5
    assert step_5.count(DESKTOP_LOOKUP) == 1, (
        "install.ps1 must ask Windows where the Desktop is, exactly once, with "
        f"{DESKTOP_LOOKUP}"
    )
    step_5 = step_5.replace(DESKTOP_LOOKUP, f'"{desktop}"')
    preamble = (
        '$ErrorActionPreference = "Stop"\n'
        f'$InstallDir = "{folder}"\n'
        f'$venvPythonw = "{folder}\\venv\\Scripts\\pythonw.exe"\n'
    )
    script = folder / "install_step_5.ps1"
    script.write_text(preamble + step_5, encoding="utf-8")
    result = run_powershell(script, folder, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "INSTALLATION COMPLETE!" in result.stdout
    link = desktop / SHORTCUT_NAME
    assert link.exists()

    read_back = folder / "read_shortcut.ps1"
    read_back.write_text(
        "$shell = New-Object -comObject WScript.Shell\n"
        f'$link = $shell.CreateShortcut("{link}")\n'
        "$link.TargetPath\n$link.WorkingDirectory\n$link.Arguments\n",
        encoding="utf-8",
    )
    fields = run_powershell(read_back, folder, env).stdout.splitlines()
    assert fields[:3] == [f"{folder}\\venv\\Scripts\\pythonw.exe", str(folder), ".\\src\\main.py"]



def test_the_script_parses_under_windows_powershell_5():
    if POWERSHELL is None:
        pytest.skip("powershell.exe not found")
    probe = (
        "$tokens = $null; $errors = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{INSTALL_SCRIPT}', "
        "[ref]$tokens, [ref]$errors) | Out-Null; "
        "$PSVersionTable.PSVersion.Major; $errors.Count; $errors | ForEach-Object { $_.Message }"
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", probe],
        capture_output=True, text=True, timeout=60,
    )
    lines = result.stdout.splitlines()
    assert lines[:2] == ["5", "0"], result.stdout + result.stderr


def test_every_native_call_is_followed_by_the_exit_code_check():
    lines = script_text().splitlines()
    native = re.compile(r"^\s*(&\s+\$\w+|winget)\b")
    offenders = []
    for index, line in enumerate(lines):
        if not native.match(line):
            continue
        following = [
            candidate.strip() for candidate in lines[index + 1:]
            if candidate.strip() and not candidate.strip().startswith("#")
        ]
        if not following or not following[0].startswith("Stop-OnFailure"):
            offenders.append(f"line {index + 1}: {line.strip()}")
    assert not offenders, "native calls without Stop-OnFailure:\n  " + "\n  ".join(offenders)
    assert sum(1 for line in lines if native.match(line)) >= 4, "the sweep saw too few native calls"


def test_every_path_test_is_literal_and_no_profile_desktop_remains():
    text = script_text()
    assert "$Home" not in text and "$HOME" not in text
    for match in re.finditer(r"\bTest-Path\b[^\n]*", text):
        assert "-LiteralPath" in match.group(), match.group()
    assert not re.search(r"\b(Copy-Item|New-Item)\b", text), (
        "use the literal .NET calls; the cmdlets read [ ] in a folder name as a wildcard"
    )
