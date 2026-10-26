# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import os
import sys

import pytest

import test_install_script_behaviour as behaviour
from test_install_script_behaviour import auto_install_minor, fake_winget, pip_calls, script_text

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="install.txt is Windows-only")

FROM_PYPI = "1\n\n\n"


@pytest.fixture(scope="module")
def toolbox(tmp_path_factory):
    return behaviour.make_toolbox(tmp_path_factory)


def test_pypi_org_as_the_source_passes_no_package_address(toolbox):
    folder = toolbox.app_folder("from pypi", with_venv=True)
    log = toolbox.root / "pypi_pip_calls.log"
    result = toolbox.run(folder, log, stdin=FROM_PYPI)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 - The internet: pypi.org" in result.stdout
    assert pip_calls(log) == ["install --upgrade pip", f"install -r {folder}\\requirements.lock"]


def test_a_computer_without_python_gets_it_from_winget_when_asked_and_continues(toolbox, tmp_path):
    minor = auto_install_minor()
    folder = toolbox.app_folder("no python")
    local = tmp_path / "localappdata"
    target = local / "Programs" / "Python" / f"Python3{minor}"
    winget_log = tmp_path / "winget_calls.log"
    fakebin = fake_winget(tmp_path, winget_log, target)
    pip_log = tmp_path / "no_python_pip_calls.log"
    env = {**toolbox.no_python_env(fakebin), "LOCALAPPDATA": str(local)}
    if sys.version_info[:2] != (3, minor):
        pytest.skip(f"the fake winget links this interpreter, which is not 3.{minor}")
    try:
        result = toolbox.run(folder, pip_log, stdin="1\n" + FROM_PYPI, env=env)
    finally:
        if target.exists():
            os.rmdir(target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PYTHON 3.13 OR 3.14 IS NEEDED" in result.stdout
    assert "No Python was found on this computer." in result.stdout
    assert "3 - Continue" not in result.stdout, "the own-risk choice is only for a newer Python"
    assert f"Installing Python 3.{minor} with winget" in result.stdout
    assert winget_log.read_text(encoding="utf-8").startswith(
        f"install --id Python.Python.3.{minor} --source winget --silent")
    assert f"at {target}\\python.exe. Continuing." in result.stdout
    assert "paste the text again" not in result.stdout
    assert "Virtual environment created" in result.stdout
    assert (folder / "venv" / "pyvenv.cfg").exists()
    assert pip_calls(pip_log) == ["install --upgrade pip", f"install -r {folder}\\requirements.lock"]


@pytest.mark.parametrize("answer, shown", [
    ("2\n", "INSTALL PYTHON YOURSELF"),
    ("\n", "Nothing was chosen, so nothing was installed."),
])
def test_a_computer_without_python_can_install_it_by_hand_instead(toolbox, tmp_path, answer, shown):
    folder = toolbox.app_folder(f"no python by hand {len(answer)}")
    winget_log = tmp_path / "winget_calls.log"
    fakebin = fake_winget(tmp_path, winget_log, tmp_path / "unused" / "Python")
    pip_log = tmp_path / "pip_calls.log"
    result = toolbox.run(folder, pip_log, stdin=answer, env=toolbox.no_python_env(fakebin))
    assert result.returncode == 1, result.stdout + result.stderr
    assert shown in result.stdout
    if answer == "2\n":
        assert "https://www.python.org/downloads/windows/" in result.stdout
        assert "paste the text of" in result.stdout and "install.txt again" in result.stdout
    assert not winget_log.exists()
    assert not (folder / "venv").exists() and not (folder / "input").exists()
    assert pip_calls(pip_log) == []


def test_an_older_python_gets_no_own_risk_choice(toolbox, tmp_path):
    folder = toolbox.app_folder("python 3.12", with_venv=True, reported_version="3.12.9")
    winget_log = tmp_path / "winget_calls.log"
    fakebin = fake_winget(tmp_path, winget_log, tmp_path / "unused" / "Python")
    env = {"PATH": toolbox.base_env["PATH"] + os.pathsep + str(fakebin)}
    result = toolbox.run(folder, toolbox.root / "python_3.12_pip_calls.log", stdin="3\n2\n", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Found Python 3.12.9, which this application is not tested with." in result.stdout
    assert "3 - Continue" not in result.stdout
    assert "Please type 1 or 2." in result.stdout
    assert "INSTALL PYTHON YOURSELF" in result.stdout
    assert not winget_log.exists()


def test_a_missing_library_box_names_the_mirror_only_in_mirror_mode(toolbox):
    _, _, pypi = behaviour.run_with_pip_log(toolbox, "missing on pypi", ["missing"], 1, stdin=FROM_PYPI)
    assert "av==18.0.0 was not found at pypi.org." in pypi.stdout
    assert "usually ends in" not in pypi.stdout


def test_the_winget_command_installs_the_version_the_script_names():
    assert 'winget install --id "Python.Python.3.$AutoInstallMinor" ' in script_text(), (
        "install.txt's winget command no longer installs Python.Python.3.$AutoInstallMinor"
    )
