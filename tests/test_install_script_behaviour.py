# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_readme_step_lists import readme_lists

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "install.txt"
README = REPO_ROOT / "README.md"
STEP_5_MARKER = 'Write-Host "6/6'
FROM_PYPI = "1\n\n\n"
VERSION_PROBE = re.compile(r"function Get-PythonVersion \{\n.*?\n\}\n", re.S)
DESKTOP_LOOKUP = "[Environment]::GetFolderPath('Desktop')"
SHORTCUT_NAME = "Seva's Media Processor.lnk"

FAKE_PIP_MAIN = '''
import os
import ssl
import sys
arguments = sys.argv[1:]
pip_log = None
if "--log" in arguments:
    at = arguments.index("--log")
    pip_log = arguments[at + 1]
    del arguments[at:at + 2]
no_input = "--no-input" in arguments
if no_input:
    arguments.remove("--no-input")
with open(os.environ["FAKE_PIP_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(arguments) + "\\n")
if pip_log and os.environ.get("FAKE_PIP_LOG_TEXT"):
    with open(pip_log, "a", encoding="utf-8") as log:
        log.write(os.environ["FAKE_PIP_LOG_TEXT"])
if os.environ.get("FAKE_PIP_TRACE"):
    with open(os.environ["FAKE_PIP_TRACE"], "a", encoding="utf-8") as trace:
        trace.write(f"log={pip_log is not None} no_input={no_input} "
                    f"windows_trust={getattr(ssl, 'FAKE_WINDOWS_TRUST', False)}\\n")
sys.exit(int(os.environ.get("FAKE_PIP_EXIT", "0")))
'''
FAKE_TRUSTSTORE = '''
import os
__version__ = os.environ.get("FAKE_TRUSTSTORE_VERSION", "0.10.4")
def inject_into_ssl():
    import ssl
    ssl.FAKE_WINDOWS_TRUST = True
'''

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="install.txt is Windows-only")

POWERSHELL = shutil.which("powershell.exe")


def supported_minors():
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    listed = re.search(r"^\$SupportedMinors\s*=\s*@\(([\d,\s]+)\)", text, re.M)
    assert listed
    return [int(minor) for minor in listed.group(1).split(",")]


def auto_install_minor():
    found = re.search(r"^\$AutoInstallMinor\s*=\s*(\d+)", INSTALL_SCRIPT.read_text(encoding="utf-8"), re.M)
    assert found
    return int(found.group(1))


def script_text():
    return INSTALL_SCRIPT.read_text(encoding="utf-8")


def script_body():
    lines = script_text().splitlines()
    opens = [i for i, line in enumerate(lines) if line == "& {"]
    assert len(opens) == 1, f"expected one '& {{' line, found {len(opens)}"
    assert lines[-1] == "}", f"the script must end with the block's '}}', not {lines[-1]!r}"
    for line in lines[:opens[0]]:
        assert not line.strip() or line.startswith("#"), f"code outside the block: {line!r}"
    return lines[opens[0] + 1:-1]


def split_at_step_5():
    lines = script_body()
    index = next(i for i, line in enumerate(lines) if line.startswith(STEP_5_MARKER))
    return "\n".join(lines[:index]) + "\n", "\n".join(lines[index:]) + "\n"


def run_powershell(script_path, cwd, env, timeout=420, stdin="\n\n\n"):
    assert POWERSHELL is not None
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
        cwd=cwd, env=env, input=stdin, capture_output=True, text=True, timeout=timeout,
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
        (fake_pip / "_vendor").mkdir()
        (fake_pip / "_vendor" / "__init__.py").write_text("", encoding="utf-8")
        (fake_pip / "_vendor" / "truststore.py").write_text(FAKE_TRUSTSTORE, encoding="utf-8")
        home = root / "home"
        home.mkdir()
        windows = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        self.base_env = {
            key: value for key, value in os.environ.items()
            if key.upper() not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONSAFEPATH",
                                   "PIP_CONFIG_FILE"}
        }
        self.base_env.update({
            "PATH": os.pathsep.join([str(base_python.parent), str(windows / "System32"), str(windows)]),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "ProgramFiles": str(root / "Program Files"),
            "PYTHONPATH": str(fake_pip.parent),
        })
        self.steps_1_to_4, self.step_5 = split_at_step_5()
        self.fresh_folder = self.app_folder("fresh")
        self.fresh_log = self.root / "fresh_pip_calls.log"
        self.fresh_result = self.run(self.fresh_folder, self.fresh_log)

    def app_folder(self, name, with_venv=False, app_files=True, reported_version=None):
        folder = self.root / name
        folder.mkdir()
        if app_files:
            for filename in ("install.txt", "settings.example.json",
                             "requirements.lock", "requirements_no_version.txt"):
                shutil.copy2(REPO_ROOT / filename, folder / filename)
            (folder / "src").mkdir()
            (folder / "src" / "main.py").write_text("", encoding="utf-8")
        steps = self.steps_1_to_4
        if reported_version:
            steps, count = VERSION_PROBE.subn(
                f'function Get-PythonVersion {{\n    return [version]"{reported_version}"\n}}\n', steps)
            assert count == 1, "install.txt must probe versions in exactly one Get-PythonVersion function"
        (folder / "install_steps_1_to_4.ps1").write_text(steps, encoding="utf-8")
        if with_venv:
            shutil.copytree(self.fresh_folder / "venv", folder / "venv")
        return folder

    def run(self, folder, pip_log, pip_exit=0, stdin=FROM_PYPI, env=None):
        env = {**self.base_env, **(env or {}), "FAKE_PIP_LOG": str(pip_log), "FAKE_PIP_EXIT": str(pip_exit)}
        return run_powershell(folder / "install_steps_1_to_4.ps1", folder, env, stdin=stdin)

    def no_python_env(self, fakebin):
        windows = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        env = {"PATH": os.pathsep.join([str(fakebin), str(windows / "System32")])}
        assert POWERSHELL is not None
        probe = subprocess.run(
            [POWERSHELL, "-NoProfile", "-Command",
             "Get-Command python.exe, py.exe -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }"],
            env={**self.base_env, **env}, capture_output=True, text=True, timeout=60)
        assert probe.stdout.strip() == "", f"the 'no Python' PATH still reaches: {probe.stdout}"
        return env


@pytest.fixture(scope="module")
def toolbox(tmp_path_factory):
    if POWERSHELL is None:
        pytest.skip("powershell.exe not found")
    if sys.version_info[:2] not in [(3, minor) for minor in supported_minors()]:
        pytest.skip(
            f"install.txt supports Python 3.{supported_minors()}; this interpreter is "
            f"{sys.version_info[:2]} and the script would prompt"
        )
    return Toolbox(tmp_path_factory.mktemp("install_sandbox"))


def pip_calls(log):
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []



def test_a_fresh_folder_gets_venv_folders_settings_and_the_lock_installed(toolbox):
    folder, result = toolbox.fresh_folder, toolbox.fresh_result
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAILED" not in result.stdout
    assert f"Installing into: {folder}\n" in result.stdout
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
    assert f"Installing into: {folder}\n" in result.stdout
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


def test_outside_the_extracted_folder_with_nothing_dragged_in_it_creates_nothing(toolbox):
    folder = toolbox.app_folder("elsewhere", app_files=False)
    result = toolbox.run(folder, toolbox.root / "elsewhere_pip_calls.log")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Drag install.txt from the extracted folder" in result.stdout
    lines = result.stdout.splitlines()
    heading = next(i for i, line in enumerate(lines) if line.startswith("# ACTION NEEDED"))
    assert lines[heading - 1].strip("#") == "" and len(lines[heading - 1]) >= 20
    assert "Nothing was dragged in, so nothing was installed." in result.stdout
    assert "Installing into" not in result.stdout and "1/6" not in result.stdout
    assert sorted(p.name for p in folder.iterdir()) == ["install_steps_1_to_4.ps1"]
    assert pip_calls(toolbox.root / "elsewhere_pip_calls.log") == []


def test_dragging_install_txt_in_installs_into_its_folder(toolbox):
    started_in = toolbox.app_folder("start menu home", app_files=False)
    target = toolbox.app_folder("dragged [2] folder", with_venv=True)
    wrong = started_in / "install_steps_1_to_4.ps1"
    log = toolbox.root / "dragged_pip_calls.log"
    result = toolbox.run(started_in, log, stdin=f'"{wrong}"\n"{target}\\install.txt"\n' + FROM_PYPI)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "That is not the install.txt from the extracted folder." in result.stdout
    assert f"Installing into: {target}\n" in result.stdout
    assert (target / "input").is_dir() and (target / "settings.json").exists()
    assert sorted(p.name for p in started_in.iterdir()) == ["install_steps_1_to_4.ps1"]
    assert pip_calls(log) == ["install --upgrade pip", f"install -r {target}\\requirements.lock"]


def fake_winget(tmp_path, log, target):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    (fakebin / "winget.cmd").write_text(
        "@echo off\r\n"
        f'echo %*>>"{log}"\r\n'
        f'mkdir "{target.parent}" 2>nul\r\n'
        f'mklink /J "{target}" "{Path(sys.base_prefix)}" >nul\r\n'
        "exit /b 0\r\n",
        encoding="utf-8",
    )
    return fakebin


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


def test_the_no_python_sandbox_refuses_a_reachable_python(toolbox, tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    toolbox.no_python_env(fakebin)
    shutil.copy2(Path(sys.base_prefix) / "python.exe", fakebin / "python.exe")
    with pytest.raises(AssertionError, match="still reaches"):
        toolbox.no_python_env(fakebin)


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


def test_python_3_13_and_3_14_both_install_the_tested_lock(toolbox):
    for version in ("3.13.1", "3.14.0"):
        folder = toolbox.app_folder(f"python {version}", with_venv=True, reported_version=version)
        log = toolbox.root / f"python_{version}_pip_calls.log"
        result = toolbox.run(folder, log)
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"Found Python {version} at " in result.stdout
        assert "IS NEEDED" not in result.stdout
        assert pip_calls(log) == ["install --upgrade pip", f"install -r {folder}\\requirements.lock"]


def test_a_newer_python_may_continue_at_own_risk_with_unpinned_libraries(toolbox, tmp_path):
    folder = toolbox.app_folder("python 3.15", with_venv=True, reported_version="3.15.0")
    log = toolbox.root / "python_3.15_pip_calls.log"
    mirror = "https://mirror.example.test/simple/"
    result = toolbox.run(folder, log, stdin=f"3\n2\n{mirror}\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Found Python 3.15.0, which this application is not tested with." in result.stdout
    assert "3 - Continue with Python 3.15.0 at my own risk" in result.stdout
    assert "Continuing with Python 3.15.0 at your own risk" in result.stdout
    source = f"--isolated --disable-pip-version-check --index-url {mirror}"
    assert pip_calls(log) == [f"install {source} --upgrade pip",
                              f"install {source} -r {folder}\\requirements_no_version.txt"]


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


def test_a_package_mirror_is_the_only_source_for_every_pip_call(toolbox):
    typed = "https://nexus.corp.test/repository/pypi/simple/"
    source = f"--isolated --disable-pip-version-check --index-url {typed}"
    folder = toolbox.app_folder("mirror", with_venv=True)
    log = toolbox.root / "mirror_pip_calls.log"
    refused = "nexus.corp.test\nhttp://pypi-mirror.corp.test:8080/simple\n"
    result = toolbox.run(folder, log, stdin=f"2\n{refused}{typed}\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("The address must start with https://.") == 2, result.stdout
    assert f"The libraries will come only from {typed}" in result.stdout
    assert pip_calls(log) == [f"install {source} --upgrade pip",
                              f"install {source} -r {folder}\\requirements.lock"]


def test_no_library_source_chosen_creates_nothing(toolbox):
    folder = toolbox.app_folder("no source")
    log = toolbox.root / "no_source_pip_calls.log"
    result = toolbox.run(folder, log, stdin="\n")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "WHERE SHOULD THE LIBRARIES COME FROM?" in result.stdout
    assert "Nothing was chosen, so nothing was installed." in result.stdout
    assert not (folder / "venv").exists() and pip_calls(log) == []


def test_a_python_known_only_to_the_py_launcher_is_found(toolbox):
    launcher = shutil.which("py")
    if launcher is None:
        pytest.skip("no py launcher on this computer")
    folder = toolbox.app_folder("launcher only", with_venv=True)
    windows = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    env = {"PATH": os.pathsep.join([str(Path(launcher).parent), str(windows / "System32")])}
    assert not any((Path(part) / "python.exe").exists() for part in env["PATH"].split(os.pathsep))
    result = toolbox.run(folder, toolbox.root / "launcher_pip_calls.log", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    found = re.search(r"Found Python (3\.1[34])\.\d+ at (.+python\.exe)\.", result.stdout)
    assert found, result.stdout
    assert Path(found.group(2)).parent != Path(launcher).parent


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


MIRROR = "https://mirror.corp.test/simple/"
FROM_MIRROR = f"2\n{MIRROR}\n"
LOG_PREFIX = "2026-10-25T19:27:22,244 "
FETCH = LOG_PREFIX + "Could not fetch URL https://mirror.corp.test/simple/pip/: "
PIP_LOG_LINES = {
    "certificate": FETCH + "There was a problem confirming the ssl certificate: ('A certificate chain "
                   "processed, but terminated in a root certificate which is not trusted by the trust "
                   "provider.',) - skipping",
    "file_certificate": LOG_PREFIX + "ERROR: <SSLVerificationError: ssl-verification-failed>",
    "proxy_sign_in": FETCH + "proxy connection error: ('Unable to connect to proxy', OSError('Tunnel "
                     "connection failed: 407 Proxy Authentication Required')) - skipping",
    "refused": FETCH + "connection error: [WinError 10061] No connection could be made because the "
               "target machine actively refused it - skipping",
    "unknown_host": FETCH + "connection error: Failed to resolve IP address for 'mirror.corp.test' "
                    "([Errno 11001] getaddrinfo failed) - skipping",
    "not_found": FETCH + "404 Client Error: Not Found for url: https://mirror.corp.test/simple/pip/ - skipping",
    "forbidden": FETCH + "403 Client Error: Forbidden for url: https://mirror.corp.test/simple/pip/ - skipping",
    "fetched": LOG_PREFIX + "Fetched page https://pypi.org/simple/pip/ as application/vnd.pypi.simple.v1+json",
    "extra_unreachable": LOG_PREFIX + "Could not fetch URL https://extra.corp.test/simple/pip/: connection "
                         "error: Failed to resolve IP address for 'extra.corp.test' ([Errno 11001] getaddrinfo "
                         "failed) - skipping",
    "missing": LOG_PREFIX + "ERROR: No matching distribution found for av==18.0.0",
    "retried": LOG_PREFIX + "Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) "
               "after connection broken by 'SSLError(SSLCertVerificationError('A certificate chain "
               "processed, but terminated in a root certificate which is not trusted by the trust "
               "provider.'))': /simple/pip/",
}


def run_with_pip_log(toolbox, name, lines, pip_exit, stdin=FROM_MIRROR, env=None):
    folder = toolbox.app_folder(name, with_venv=True)
    log = toolbox.root / f"{name}_pip_calls.log"
    text = "".join(PIP_LOG_LINES[line] + "\n" for line in lines)
    result = toolbox.run(folder, log, pip_exit=pip_exit, stdin=stdin,
                         env={"FAKE_PIP_LOG_TEXT": text, **(env or {})})
    return folder, log, result


def test_every_pip_call_runs_with_windows_trust_and_its_own_log(toolbox, tmp_path):
    assert " -m pip" not in script_text(), "every pip call goes through Invoke-Pip"
    for version, injected in (("0.10.4", True), ("0.9.1", True), ("0.8.0", False)):
        trace = tmp_path / f"trace_{version}.log"
        _, log, result = run_with_pip_log(toolbox, f"trust {version}", [], 0, stdin=FROM_PYPI,
                                          env={"FAKE_PIP_TRACE": str(trace), "FAKE_TRUSTSTORE_VERSION": version})
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(pip_calls(log)) == 2
        assert trace.read_text(encoding="utf-8").splitlines() == [
            f"log=True no_input=True windows_trust={injected}"] * 2, "pip never waits for typed input"


@pytest.mark.parametrize("lines, pip_exit, step, headline, named", [
    (["retried", "certificate"], 0, "upgrading pip.", "CERTIFICATE NOT TRUSTED",
     "IT installs its root"),
    (["file_certificate"], 1, "upgrading pip (exit code 1).", "CERTIFICATE NOT TRUSTED",
     "IT installs its root"),
    (["proxy_sign_in"], 0, "upgrading pip.", "PROXY SIGN-IN NEEDED", "(HTTP 407)"),
    (["refused"], 0, "upgrading pip.", "PACKAGE SOURCE NOT REACHED", f"could not connect to {MIRROR}."),
    (["unknown_host"], 0, "upgrading pip.", "PACKAGE SOURCE NOT REACHED", f"could not connect to {MIRROR}."),
    (["missing"], 1, "upgrading pip (exit code 1).", "LIBRARY NOT FOUND",
     f"av==18.0.0 was not found at {MIRROR}."),
    (["forbidden"], 0, "upgrading pip.", "ACCESS REFUSED", f"{MIRROR} refused access (HTTP 403)."),
], ids=["certificate", "file_certificate", "proxy_sign_in", "refused", "unknown_host", "missing", "forbidden"])
def test_pip_stops_with_the_cause_its_log_names(toolbox, lines, pip_exit, step, headline, named):
    folder, log, result = run_with_pip_log(toolbox, f"cause {lines[0]}", lines, pip_exit)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"FAILED: {step} The steps after this one were not run." in result.stdout
    assert headline in result.stdout and named in result.stdout, result.stdout
    assert len(pip_calls(log)) == 1, "the libraries are not installed after a failed upgrade"
    assert not (folder / "settings.json").exists()


def test_a_missing_library_box_names_the_mirror_only_in_mirror_mode(toolbox):
    _, _, mirror = run_with_pip_log(toolbox, "missing on mirror", ["missing"], 1)
    _, _, pypi = run_with_pip_log(toolbox, "missing on pypi", ["missing"], 1, stdin=FROM_PYPI)
    assert "usually ends in" in mirror.stdout
    assert "av==18.0.0 was not found at pypi.org." in pypi.stdout
    assert "usually ends in" not in pypi.stdout


@pytest.mark.parametrize("lines", [["retried"], ["not_found"], ["fetched", "extra_unreachable"]],
                         ids=["retried_then_reached", "mirror_without_pip", "source_reached_extra_failed"])
def test_a_successful_pip_call_carries_on(toolbox, lines):
    folder, log, result = run_with_pip_log(toolbox, f"carries on {lines[0]}", lines, 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAILED" not in result.stdout
    assert len(pip_calls(log)) == 2
    assert (folder / "settings.json").exists()


def finish_function():
    found = re.search(r"^function Show-Finish \{\n.*?\n\}\n", script_text().replace("\r\n", "\n"), re.S | re.M)
    assert found, "install.txt shows its success message through one Show-Finish function"
    return found.group()


def test_the_shortcut_step_writes_the_lnk_into_the_desktop_folder_windows_reports(toolbox, tmp_path):
    folder = toolbox.app_folder("shortcut")
    env = toolbox.base_env
    desktop = tmp_path / "onedrive" / "Desktop"
    desktop.mkdir(parents=True)
    step_5 = toolbox.step_5
    assert step_5.count(DESKTOP_LOOKUP) == 1, (
        "install.txt must ask Windows where the Desktop is, exactly once, with "
        f"{DESKTOP_LOOKUP}"
    )
    step_5 = step_5.replace(DESKTOP_LOOKUP, f'"{desktop}"')
    preamble = (
        '$ErrorActionPreference = "Stop"\n'
        f'$InstallDir = "{folder}"\n'
        f'$venvPythonw = "{folder}\\venv\\Scripts\\pythonw.exe"\n'
        + finish_function()
    )
    script = folder / "install_step_5.ps1"
    script.write_text(preamble + step_5, encoding="utf-8")
    result = run_powershell(script, folder, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "INSTALLATION COMPLETE!" in result.stdout
    assert "You can close this window now." in result.stdout
    assert result.stdout.rstrip().endswith("#" * 20)
    assert "Press Enter" not in result.stdout
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


class _RecordingIndex:

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        self.paths = []
        index = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                index.paths.append(self.path)
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/simple/"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def mirror_pip_arguments(mirror):
    found = re.search(r"\$PipSource = @\(([^)]*\$mirror[^)]*)\)", script_text())
    assert found, "install.txt no longer builds its mirror options in one $PipSource line"
    return [mirror if item.strip() == "$mirror" else item.strip().strip('"')
            for item in found.group(1).split(",")]


@pytest.mark.parametrize("isolated", [True, False], ids=["script_options", "positive_control"])
@pytest.mark.parametrize("route", ["settings_file", "variable"])
def test_mirror_mode_never_falls_back_to_another_package_address(tmp_path, route, isolated):
    assert '$env:PIP_CONFIG_FILE = "nul"' in script_text()
    mirror, extra = _RecordingIndex(), _RecordingIndex()
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PIP_")}
    if route == "settings_file":
        settings = tmp_path / "pip.ini"
        settings.write_text(f"[global]\nextra-index-url = {extra.url}\n", encoding="utf-8")
        env["PIP_CONFIG_FILE"] = str(settings)
    else:
        env["PIP_EXTRA_INDEX_URL"] = extra.url
    options = ["--index-url", mirror.url]
    if isolated:
        env["PIP_CONFIG_FILE"] = "nul"
        options = mirror_pip_arguments(mirror.url)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *options, "--dry-run", "--no-deps",
             "--retries", "0", "--timeout", "5", "sevas-mirror-probe==1.0"],
            env=env, capture_output=True, text=True, timeout=120)
    finally:
        mirror.stop()
        extra.stop()
    assert result.returncode != 0, result.stdout + result.stderr
    assert "No matching distribution found for sevas-mirror-probe==1.0" in result.stderr
    assert mirror.paths, "the mirror was never asked"
    assert bool(extra.paths) is not isolated, extra.paths



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
        if not following or not following[0].startswith(("Stop-OnFailure", "Stop-OnPipFailure")):
            offenders.append(f"line {index + 1}: {line.strip()}")
    assert not offenders, "native calls without Stop-OnFailure:\n  " + "\n  ".join(offenders)
    assert sum(1 for line in lines if native.match(line)) >= 3, "the sweep saw too few native calls"


def exits_without_a_pause(text):
    offenders = []
    previous = ""
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"exit\b", stripped) and not previous.startswith("Read-Host"):
            offenders.append(f"line {number}: {stripped}")
        previous = stripped
    return offenders


def test_the_pause_check_bites():
    assert exits_without_a_pause('Write-Host "Setup cancelled."\nexit 1\n') == ["line 2: exit 1"]
    assert exits_without_a_pause('Read-Host "Press Enter to close this window"\n\nexit 1\n') == []


def test_every_exit_waits_for_enter_first():
    text = script_text()
    assert re.search(r"^\s*exit\b", text, re.M), "the sweep saw no exit"
    offenders = exits_without_a_pause(text)
    assert not offenders, "exits that close the window unread:\n  " + "\n  ".join(offenders)


def test_the_readme_setup_steps_name_the_script_file():
    text = README.read_text(encoding="utf-8")
    assert text.count(f"Double-click `{INSTALL_SCRIPT.name}`") == 2


HOW_TO_HEADING = "# How to install"
README_RUN_LIST = "Option 1: Copy and paste the setup script"


def how_to_steps(text):
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(HOW_TO_HEADING))
    steps = []
    for line in lines[start + 1:]:
        if line.strip() == "#":
            return steps
        numbered = re.match(r"# (\d+)\. (.*\S)\s*$", line)
        if numbered:
            assert int(numbered.group(1)) == len(steps) + 1, line
            steps.append(numbered.group(2))
            continue
        more = re.match(r"#\s{3,}(\S.*?)\s*$", line)
        assert more and steps, f"not a step or its continuation: {line!r}"
        steps[-1] += " " + more.group(1)
    raise AssertionError("the how-to block never ends with a bare '#' line")


def test_the_how_to_parser_joins_continuations():
    sample = "# How to install:\n# 1. First\n#    continued\n# 2. Second\n#\n$code = 1\n"
    assert how_to_steps(sample) == ["First continued", "Second"]


def test_the_how_to_in_the_file_is_the_readmes_steps():
    lists, _ = readme_lists()
    readme = [re.sub(r"`([^`]*)`", r"\1", step) for step in lists[README_RUN_LIST]]
    assert readme[0].startswith("Open the extracted folder"), readme[0]
    assert readme[1].startswith(f"Double-click {INSTALL_SCRIPT.name}"), readme[1]
    file_steps = how_to_steps(script_text())
    assert len(file_steps) >= 5, file_steps
    assert file_steps == readme[2:]
    assert not any(f"Double-click {INSTALL_SCRIPT.name}" in step for step in file_steps), (
        "the file's own steps must not tell its reader to open it"
    )
    assert "# If you run into problems, open README.md in this folder." in script_text()


def test_the_screen_is_cleared_only_in_a_real_window():
    text = script_text().replace("\r\n", "\n")
    assert text.count("Clear-Host") == 1
    assert text.count("FlushInputBuffer") == 1
    assert ("if (-not [Console]::IsOutputRedirected) {\n    try { Clear-Host } catch { }\n"
            "    if (-not [Console]::IsInputRedirected) {\n"
            "        try { $Host.UI.RawUI.FlushInputBuffer() } catch { }\n    }\n}") in text


def test_a_pasted_copy_ends_by_asking_for_enter_and_starts_by_itself_where_it_can():
    raw = INSTALL_SCRIPT.read_bytes()
    assert raw.endswith((b"\n}\n", b"\n}\r\n")), "the file ends with the block's '}' and one line break"
    body = script_body()
    tail = [line for line in body if line.strip()][-4:]
    assert tail[0] == "Show-Finish", tail
    assert all(line.startswith("# ") for line in tail[1:]) and "Press Enter to start the setup." in tail[2], tail
    assert any('"Press Enter to start the setup."' in step for step in how_to_steps(script_text())), (
        "the how-to step, which equals the README's, quotes the box")
    lines = script_text().splitlines()
    first_step = next(i for i, line in enumerate(lines) if line.startswith('Write-Host "1/6'))
    complete = [i for i, line in enumerate(lines) if "INSTALLATION COMPLETE" in line]
    assert complete and all(i < first_step for i in complete), complete


def test_every_path_test_is_literal_and_no_profile_desktop_remains():
    text = script_text()
    assert "$Home" not in text and "$HOME" not in text
    for match in re.finditer(r"\bTest-Path\b[^\n]*", text):
        assert "-LiteralPath" in match.group(), match.group()
    assert not re.search(r"\b(Copy-Item|New-Item)\b", text), (
        "use the literal .NET calls; the cmdlets read [ ] in a folder name as a wildcard"
    )
