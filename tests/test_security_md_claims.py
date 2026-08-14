# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import ast
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY_MD = REPO_ROOT / "SECURITY.md"
SRC = REPO_ROOT / "src"

VERIFY_HEADING = "## Verifying all of this yourself"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

windows_shell_only = pytest.mark.skipif(
    os.name != "nt" or POWERSHELL is None,
    reason="the documented commands are PowerShell on Windows",
)



FENCED_BLOCK = re.compile(
    r"^(?P<fence>```+|~~~+)[^\n]*\n(?P<body>.*?)^(?P=fence)", re.S | re.M
)


def fenced_blocks(text):
    return [m.group("body").strip() for m in FENCED_BLOCK.finditer(text)]


def documented_commands():
    text = SECURITY_MD.read_text(encoding="utf-8")
    assert VERIFY_HEADING in text, (
        f"SECURITY.md no longer has a {VERIFY_HEADING!r} section; this test "
        "file exists to keep that section honest"
    )
    section = text.split(VERIFY_HEADING, 1)[1]
    next_heading = section.find("\n## ")
    if next_heading != -1:
        section = section[:next_heading]
    return fenced_blocks(section)


def command_containing(needle):
    matches = [c for c in documented_commands() if needle in c]
    assert len(matches) == 1, (
        f"expected exactly one documented command containing {needle!r}, "
        f"found {len(matches)}: {matches}"
    )
    return matches[0]


def run_powershell(command, cwd=REPO_ROOT):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=str(cwd), capture_output=True, text=True, timeout=180,
    )


def test_the_verification_section_documents_exactly_two_commands():
    commands = documented_commands()
    assert len(commands) == 2, (
        "SECURITY.md's verification section has changed shape - every command "
        f"it prints must be checked here. Found: {commands}"
    )


def test_the_extraction_sees_every_fence_spelling():
    synthetic = (
        "prose\n\n```powershell\nGet-Tagged\n```\n\nmore prose\n\n"
        "~~~\nTilde-Fenced\n~~~\n"
    )
    assert fenced_blocks(synthetic) == ["Get-Tagged", "Tilde-Fenced"]


def test_no_command_block_hides_outside_the_verification_section():
    everywhere = fenced_blocks(SECURITY_MD.read_text(encoding="utf-8"))
    assert everywhere == documented_commands(), (
        "SECURITY.md prints a fenced block outside the verification section - "
        "every command the page shows a reviewer must be checked here"
    )



def test_only_llm_client_imports_requests():
    importers = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                imported = [(node.module or "").split(".")[0]]
            if "requests" in imported:
                importers.append(path.relative_to(REPO_ROOT).as_posix())

    assert importers == ["src/llm_client.py"], (
        "SECURITY.md claims exactly one file in src/ can open an outbound "
        f"connection. Files importing `requests`: {importers}"
    )


NETWORK_CAPABLE_MODULES = {
    "socket", "ssl", "socketserver", "asyncio",
    "http.client", "http.server", "urllib.request", "ftplib", "smtplib",
    "poplib", "imaplib", "nntplib", "telnetlib", "xmlrpc",
    "urllib3", "aiohttp", "httpx", "websockets", "websocket",
}


def imported_modules(tree):
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            base = node.module or ""
            names.append(base)
            names += [f"{base}.{alias.name}" if base else alias.name
                      for alias in node.names]
    return names


def is_network_capable(name):
    return any(
        name == banned or name.startswith(banned + ".")
        for banned in NETWORK_CAPABLE_MODULES
    )


def test_no_other_network_capable_module_is_imported():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = sorted({n for n in imported_modules(tree) if is_network_capable(n)})
        if found:
            offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {found}")
    assert not offenders, (
        "SECURITY.md promises the `import requests` search names every "
        "outbound-connection site. These imports can reach the network "
        "without it, making that page a lie:\n" + "\n".join(offenders)
    )


def test_the_network_import_scan_catches_the_disguised_spellings():
    snippet = (
        "from urllib import request\n"
        "from http import client\n"
        "import socket as harmless_name\n"
        "import urllib3.util\n"
    )
    found = {
        n for n in imported_modules(ast.parse(snippet)) if is_network_capable(n)
    }
    assert {"urllib.request", "http.client", "socket", "urllib3.util"} <= found


def test_the_network_import_scan_ignores_the_harmless_neighbours():
    snippet = (
        "import urllib.parse\n"
        "from urllib.parse import urlsplit\n"
        "from http import HTTPStatus\n"
    )
    found = {
        n for n in imported_modules(ast.parse(snippet)) if is_network_capable(n)
    }
    assert not found, f"harmless imports flagged as network-capable: {found}"


@windows_shell_only
def test_documented_import_search_returns_only_llm_client():
    result = run_powershell(command_containing("Select-String"))
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, (
        "SECURITY.md promises this command returns a single line. It "
        f"returned {len(lines)}:\n" + "\n".join(lines)
    )
    assert "llm_client.py" in lines[0], lines[0]


@windows_shell_only
def test_documented_import_search_actually_searches(tmp_path):
    planted = tmp_path / "src" / "pipelines"
    planted.mkdir(parents=True)
    (tmp_path / "src" / "innocent.py").write_text("import json\n", encoding="utf-8")
    (planted / "talker.py").write_text("import requests\n", encoding="utf-8")

    result = run_powershell(command_containing("Select-String"), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1 and "talker.py" in lines[0], (
        "the documented command did not find a planted `import requests`, so "
        f"it is not really searching: {result.stdout!r}"
    )



def test_main_py_binds_loopback_on_an_os_assigned_port():
    tree = ast.parse((SRC / "main.py").read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "make_server"
    ]
    assert len(calls) == 1, f"expected one make_server call in main.py, found {len(calls)}"

    host, port = calls[0].args[0], calls[0].args[1]
    assert isinstance(host, ast.Constant) and host.value == "127.0.0.1", (
        "main.py must bind the server to 127.0.0.1 (SECURITY.md: loopback "
        f"only), not {ast.dump(host)}"
    )
    assert isinstance(port, ast.Constant) and port.value == 0, (
        "main.py must bind port 0 so the OS assigns one (SECURITY.md: no "
        f"fixed port to find), not {ast.dump(port)}"
    )


@pytest.fixture
def loopback_server():
    from werkzeug.serving import make_server

    def app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=10)


def test_the_bound_socket_is_loopback_with_an_ephemeral_port(loopback_server):
    host, port = loopback_server.server_address[:2]
    assert host == "127.0.0.1"
    assert port > 1024, f"port 0 should yield an OS-assigned high port, got {port}"


@windows_shell_only
def test_documented_listener_command_shows_only_a_loopback_listener(loopback_server):
    command = command_containing("Get-NetTCPConnection")
    command += " | Select-Object LocalAddress, LocalPort, OwningProcess | ConvertTo-Json"

    result = run_powershell(command)
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout) if result.stdout.strip() else []
    listeners = payload if isinstance(payload, list) else [payload]

    ours = [row for row in listeners if row["LocalPort"] == loopback_server.server_address[1]]
    assert ours, (
        "the documented command did not find this process's listener on port "
        f"{loopback_server.server_address[1]}, so it would not find the "
        f"application's either. It returned: {listeners}"
    )
    assert all(row["LocalAddress"] == "127.0.0.1" for row in ours), ours

    mine = [row for row in listeners if row["OwningProcess"] == os.getpid()]
    off_machine = [row for row in mine if row["LocalAddress"] not in ("127.0.0.1", "::1")]
    assert not off_machine, (
        "SECURITY.md claims the application listens on loopback only, but "
        f"this process has a non-loopback listener: {off_machine}"
    )


@pytest.mark.parametrize("address", ["0.0.0.0", "192.168.1.10", "::"])
def test_the_loopback_check_rejects_a_non_loopback_address(address):
    listeners = [{"LocalAddress": address, "LocalPort": 5000, "OwningProcess": os.getpid()}]
    off_machine = [
        row for row in listeners if row["LocalAddress"] not in ("127.0.0.1", "::1")
    ]
    assert off_machine, f"{address} should have been flagged as non-loopback"



ESTABLISHED_INSTRUCTION = "Replace `-State Listen` with `-State Established`"


def established_command():
    text = SECURITY_MD.read_text(encoding="utf-8")
    assert ESTABLISHED_INSTRUCTION in text, (
        "SECURITY.md no longer tells the reader to run the -State "
        "Established variant; this claim left the page, so retire the "
        "claim-3 tests with it"
    )
    command = command_containing("Get-NetTCPConnection")
    assert "-State Listen" in command, command
    return command.replace("-State Listen", "-State Established")


@windows_shell_only
def test_documented_established_variant_shows_only_loopback_remotes(loopback_server):
    command = established_command()
    command += " | Select-Object LocalAddress, RemoteAddress, RemotePort, OwningProcess | ConvertTo-Json"

    port = loopback_server.server_address[1]
    with socket.create_connection(("127.0.0.1", port)):
        result = run_powershell(command)
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout) if result.stdout.strip() else []
    rows = payload if isinstance(payload, list) else [payload]
    mine = [row for row in rows if row["OwningProcess"] == os.getpid()]

    ours = [row for row in mine if row["RemotePort"] == port]
    assert ours, (
        "the -State Established variant did not find this process's open "
        f"connection to port {port}, so it is not really looking. It "
        f"returned: {rows}"
    )
    off_machine = [
        row for row in mine if row["RemoteAddress"] not in ("127.0.0.1", "::1")
    ]
    assert not off_machine, (
        "SECURITY.md claims every remote address is loopback with AI off; "
        f"this process has a connection leaving the machine: {off_machine}"
    )


@pytest.mark.parametrize("address", ["93.184.216.34", "10.0.0.5", "2606:2800:220:1::1"])
def test_the_established_check_rejects_an_off_machine_remote(address):
    rows = [{"RemoteAddress": address, "RemotePort": 443, "OwningProcess": os.getpid()}]
    off_machine = [
        row for row in rows if row["RemoteAddress"] not in ("127.0.0.1", "::1")
    ]
    assert off_machine, f"{address} should have been flagged as off-machine"




DENIAL_SENTENCE = re.compile(r"\bno\s+telemetry\b[^.]*")

TEXT_ASSET_DIRS = ("locales", "templates", "static")
TEXT_ASSET_SUFFIXES = {".json", ".html", ".js", ".css", ".svg", ".txt", ".md"}


def stem_denied_phrase(phrase):
    if phrase.endswith("ing"):
        return phrase[: -len("ing")]
    if phrase.endswith("s"):
        return phrase[:-1]
    return phrase


def denied_terms():
    text = SECURITY_MD.read_text(encoding="utf-8")
    match = DENIAL_SENTENCE.search(text)
    assert match, (
        "SECURITY.md no longer contains the 'no telemetry, no ...' denial "
        "sentence; this sweep exists to keep that sentence honest, so "
        "update the extraction alongside the page"
    )
    sentence = re.sub(r"\s+", " ", match.group(0))
    phrases = [p.strip() for p in sentence.split(", no ")]
    phrases[0] = phrases[0][len("no "):]
    return [stem_denied_phrase(p) for p in phrases]


def test_the_denied_term_extraction_reads_the_page():
    assert denied_terms() == ["telemetry", "update check", "analytic", "crash report"]


def python_string_literals(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def test_no_runtime_string_claims_a_denied_behaviour():
    terms = denied_terms()
    offenders = []

    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, value in python_string_literals(tree):
            hits = [t for t in terms if t in value.lower()]
            if hits:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: {hits}"
                )

    for dirname in TEXT_ASSET_DIRS:
        for path in sorted((SRC / dirname).rglob("*")):
            if not path.is_file() or path.suffix.lower() not in TEXT_ASSET_SUFFIXES:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                hits = [t for t in terms if t in line.lower()]
                if hits:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: {hits}"
                    )

    assert not offenders, (
        "SECURITY.md denies these behaviours in its opening sentence, and "
        "the app's own strings claim them - a reviewer grepping the logs "
        "or the source finds the exact words the page swears off:\n"
        + "\n".join(offenders)
    )


def test_the_denied_word_sweep_reads_docstrings_and_skips_comments():
    snippet = (
        '"""Writes a crash report somewhere."""\n'
        "# Exact capture-time telemetry\n"
        'x = f"high-fidelity {kind} telemetry"\n'
    )
    terms = denied_terms()
    flagged = [
        value
        for _, value in python_string_literals(ast.parse(snippet))
        if any(t in value.lower() for t in terms)
    ]
    assert len(flagged) == 2, (
        "expected the docstring and the f-string fragment, and only "
        f"those, got: {flagged}"
    )



LAUNCH_HEADING = "## Programs it launches"

COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

SPAWN_HELPER = re.compile(r"_([a-z0-9]+)_path")

SPAWN_FUNCS = {
    "subprocess": {"Popen", "run", "call", "check_call", "check_output"},
    "os": {"system", "popen", "startfile"},
}


def launches_section():
    text = SECURITY_MD.read_text(encoding="utf-8")
    assert LAUNCH_HEADING in text, (
        f"SECURITY.md no longer has a {LAUNCH_HEADING!r} section; this "
        "claim left the page, so retire the claim-5 tests with it"
    )
    section = text.split(LAUNCH_HEADING, 1)[1]
    cut = section.find("\n## ")
    return section[:cut] if cut != -1 else section


def _is_spawn_call(node):
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    ):
        return False
    module, name = node.func.value.id, node.func.attr
    if module not in SPAWN_FUNCS:
        return False
    return name in SPAWN_FUNCS[module] or (
        module == "os" and name.startswith(("spawn", "exec"))
    )


def _program_stems(text, filename="<snippet>"):
    stems = set()
    for node in ast.walk(ast.parse(text)):
        if not _is_spawn_call(node):
            continue
        index = 1 if node.func.attr.startswith("spawn") else 0
        arg = node.args[index] if len(node.args) > index else None
        if isinstance(arg, (ast.List, ast.Tuple)):
            arg = arg.elts[0] if arg.elts else None
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
            match = SPAWN_HELPER.fullmatch(arg.func.id)
            assert match, (
                f"{filename}:{node.lineno}: spawn via {arg.func.id}() - name "
                "the program in a _<program>_path() helper or teach this sweep"
            )
            stems.add(match.group(1))
        elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            token = arg.value.split()[0]
            stems.add(token.replace("\\", "/").rsplit("/", 1)[-1]
                      .rsplit(".", 1)[0].lower())
        else:
            raise AssertionError(
                f"{filename}:{node.lineno}: spawn whose program this sweep "
                "cannot judge statically"
            )
    return stems


def test_the_spawn_modules_carry_no_disguising_import():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in SPAWN_FUNCS and alias.asname:
                        offenders.append(f"{rel}:{node.lineno}: import "
                                         f"{alias.name} as {alias.asname}")
            elif isinstance(node, ast.ImportFrom) and node.module in SPAWN_FUNCS:
                spawny = [
                    alias.name for alias in node.names
                    if alias.name in SPAWN_FUNCS[node.module]
                    or alias.name.startswith(("spawn", "exec"))
                ]
                if spawny:
                    offenders.append(f"{rel}:{node.lineno}: from "
                                     f"{node.module} import {spawny}")
    assert not offenders, (
        "a spawn function reachable outside the subprocess./os. spelling "
        f"would slip past the launched-programs sweep: {offenders}"
    )


def _launched_programs():
    stems = set()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        stems |= _program_stems(path.read_text(encoding="utf-8"), rel)
    return stems


def test_the_page_names_every_program_the_source_launches():
    section = launches_section()
    first_word = section.strip().split()[0].strip(".:").lower()
    assert first_word in COUNT_WORDS, (
        "the launches section must open with its count as a word "
        f"(One./Two./...), got {first_word!r}"
    )
    launched = _launched_programs()
    assert "notepad" in launched, (
        "the sweep no longer sees the settings-editor spawn - has it "
        "stopped working?"
    )
    assert COUNT_WORDS[first_word] == len(launched), (
        f"SECURITY.md counts {first_word} launched program(s); the source "
        f"launches {sorted(launched)}"
    )
    lower = section.lower()
    missing = sorted(stem for stem in launched if stem not in lower)
    assert not missing, (
        f"launched by the source but never named on the page: {missing}"
    )


def test_the_launch_extraction_reads_the_page():
    section = launches_section().lower()
    assert section.strip().split()[0].strip(".:") == "two"
    assert "notepad" in section and "explorer" in section


def test_the_program_stem_sweep_judges_helpers_and_literals():
    snippet = (
        "import subprocess\n"
        "subprocess.Popen([_notepad_path(), str(target)])\n"
        'subprocess.run([r"C:\\Windows\\explorer.exe", "/select," + p])\n'
    )
    assert _program_stems(snippet) == {"notepad", "explorer"}


def test_the_program_stem_sweep_refuses_to_guess():
    snippet = "import subprocess\nsubprocess.Popen([editor, path])\n"
    with pytest.raises(AssertionError, match="cannot judge"):
        _program_stems(snippet)
