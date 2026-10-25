# Security and privacy notes

Seva's Media Processor is a local Windows desktop application. It reads media files
from a folder you choose, converts them to JPEGs, and writes the results to a
folder you choose. **It makes no network connections of its own** — no
telemetry, no update checks, no analytics, no crash reporting. Its window is
Microsoft WebView2, the browser engine built into Windows; as in any program
that uses it, WebView2 talks to Microsoft on its own and keeps its own data.

The optional AI feature is **off by default**. When you switch it on, the
application sends the converted images to the AI endpoint *you* configured,
and to nothing else. If you would rather nothing left the machine at all,
point it at Ollama or LM Studio running locally — both are built-in choices.
The `custom` provider can point at any other endpoint, such as an internal
AI gateway.

It needs **no administrator rights**. It installs no service, writes nothing
to the registry, and installs no driver. Everything lives in folders you
control plus one folder under your own `%APPDATA%`; the one exception is a
crash log written only if the application cannot start (see "Where your data
goes"). The setup script does more — see "What the installer does" below.

## The application's network activity

| When | Where to | What is sent |
|---|---|---|
| AI feature **off** (the default) | nowhere | nothing |
| AI feature **on**, during a run | the URL in the selected provider's `url` setting — a value you typed | the JPEGs produced from your files, plus your prompt text, plus your API token in the auth header |
| You click a link in the About dialog | your default web browser opens the project's GitHub page or YouTube channel | the application sends nothing; the browser loads the page |
| Any other time | nowhere | nothing |

There is exactly **one** place in the entire codebase that opens an outbound
connection: `src/llm_client.py`. The `requests` library is imported nowhere
else in `src/`. The application has no hardcoded endpoint that it contacts on
its own behalf — the destination is always a settings value.

The relevant defaults and code:

- `ENABLE_LLM_INFERENCE` defaults to `False` — `src/config_validator.py`
- the request itself — `src/llm_client.py`
- provider URLs, all user-editable — `settings.example.json`

## The local web server

The user interface is HTML. The application runs a small
[Flask](https://flask.palletsprojects.com/) server on the machine and
displays it in a desktop window (via
[pywebview](https://pywebview.flowrl.com/), which uses the Microsoft WebView2
runtime already present on Windows 10 and 11):

- **All of it stays on this machine.** The interface is shown in the
  application's own window, not in your web browser, and that window loads
  nothing except pages from that local server.
- **Loopback only.** The server binds to `127.0.0.1`, which is the machine
  itself. It is not reachable from the network. — `src/main.py`
- **A random port each launch.** It binds to port `0`, meaning the operating
  system picks an unused port. There is no fixed port to find.
- **A per-launch secret.** A fresh random token is generated at startup and
  handed only to the application's own window. Every request except the
  page itself and its static assets must present it as an `X-App-Token`
  header, or is refused with 403; the open paths are an allowlist. Only the
  event stream, which cannot send headers, accepts it as a `?token=` query
  parameter instead. Per-request access
  logging is off (errors only), and no log line contains `token=`. —
  `src/routes/web_server.py`, `src/central_logger.py`
- **The secret is never written into the page.** `src/main.py` pushes it
  into the window after the window reports that it has loaded; the page
  holds its requests until it arrives.
- **A Host-header check.** Requests whose `Host` header is not a loopback name
  are refused. — `src/routes/web_server.py`
- **Debug mode is hardcoded off.**
- **It is Werkzeug's development server** (`make_server`). It binds
  loopback, serves exactly one client (the application's own window), and
  carries one user's clicks — a desktop app's load profile. — `src/main.py`

## Where your data goes

| What | Where |
|---|---|
| Converted JPEGs and reports | the output folder you choose (`OUTPUT_FOLDER_PATH`) |
| The run database (`application_state.db`, SQLite) | inside that same output folder |
| API tokens | `%APPDATA%\SevasMediaProcessor\.env` — plain text, guarded by your Windows profile's file permissions (your own account, plus the machine's administrators, as with any file in your profile) |
| Logs | `%APPDATA%\SevasMediaProcessor\logs` — the newest 30 are kept, older ones deleted at startup |
| The window's browser data (WebView2) | `%APPDATA%\SevasMediaProcessor\webview` |
| Settings | `settings.json` in the application folder |
| A crash log, only if the application cannot start | `MEDIA_PROCESSOR_CRASH_LOG.txt` in the output folder, or the folder above it if the output folder cannot be written; if `settings.json` cannot be read or neither folder can be written, on your Desktop (or in your home folder when there is no Desktop folder) |

API tokens are never written to `settings.json`, never committed to the
repository, and are shown masked (`********`) in the user interface.

## Programs it launches

Two. The "Open settings file" button opens `settings.json` in Windows
Notepad (`%SystemRoot%\System32\notepad.exe`) — `src/routes/settings_api.py`.
It is launched by its full path, never by a bare name Windows would resolve
through its search order. The two links in the About dialog open in your
default web browser, through Python's `webbrowser` module —
`src/routes/about_api.py`; it opens only the two addresses listed in
`src/version.py`, and only when you click one. The export buttons start no
program: they ask the Windows Shell to show the exported file, or the newest
log in the logs folder, selected in an Explorer window
(`SHOpenFolderAndSelectItems`, `src/windows_shell.py`), which can show items
but not start anything. Two tests keep this section true: one sweeps the
source for any program started by bare name, the other checks the count and
the names above against every program-launching call in the source,
including the Windows functions that start programs.

## Third-party libraries

Seva's Media Processor itself is licensed **Apache-2.0** (see `LICENSE.txt`).

The libraries it uses are **not redistributed by this project**. `pip`
downloads them onto your machine at install time, from PyPI or from your
organisation's package mirror. `README.md` lists them with a plain
description of what each one does.

`requirements.lock` records the exact version of every package. On the
tested Pythons (3.13 and 3.14) the install comes from that lock. On a
newer Python, if you choose to continue at your own risk, the script
installs unpinned instead — a path the lock file does not describe.

Five of the libraries carry native decoders written in C and C++, and they
— not the readable Python — are what your media files actually enter:
Pillow (libjpeg, libtiff, libwebp, zlib), `av` (all of FFmpeg),
`pillow_heif` (libheif, libde265), `pillow-avif-plugin` (libavif), and
`pypdfium2` (Google's PDFium).

## What the installer does

The application writes only to its own folder, the folders you choose and
`%APPDATA%\SevasMediaProcessor`, apart from the startup crash log described
above. The setup script — `install.txt`, which `README.md` tells you
to paste into PowerShell — does more:

- **If no Python 3.13 or 3.14 is found**, it asks first. It looks on PATH,
  in the list of the `py` launcher and in the usual install folders. Only
  if you choose the internet
  install does it run `winget install --id Python.Python.3.14 --source winget
  --silent --accept-source-agreements --accept-package-agreements`: a network
  download of the official Python installer, its licence terms accepted,
  and the things a Python install does to a machine — registry keys, PATH
  changes, `.py` file associations. The other choice stops the script, with
  nothing installed, so that you or your IT department can install Python.
- **It asks where the libraries come from**: pypi.org, or a package mirror
  whose address you type. With a mirror, `pip` runs with `--isolated` and
  `--index-url` set to that address; while it runs, `PIP_CONFIG_FILE` is
  `nul` and every other `PIP_*` environment variable is removed (pip builds
  source packages with a second pip that does not get `--isolated`). So no
  other package address from pip's settings files or `PIP_*` variables is
  used; a library missing on the mirror stops the setup. The variables are
  restored afterwards.
- **`pip` verifies certificates against the Windows certificate store**,
  through its own `truststore`.
- **It creates a virtual environment** (the `venv` folder inside the
  application folder) and installs the libraries into it, not into the
  machine's Python. An existing one that can no longer start, because the
  Python that made it was uninstalled, is deleted and built again.
- **It lets `pip` upgrade itself first**, from the same source.
- **It chooses between two dependency lists**: the pinned
  `requirements.lock` on Python 3.13 or 3.14, or the unpinned fallback on
  a newer Python, after you choose to continue at your own risk.
- **It creates the first-run files**, all inside the application folder:
  the empty `input` and `output` folders, and your `settings.json`, copied
  from the committed `settings.example.json` template.
- **It creates one Desktop shortcut.** No service, no autostart, no
  registry entry of its own.

On a work or managed computer, choose to install Python yourself (or ask
your IT department) and use your organisation's package mirror.

## Verifying all of this yourself

Nothing above requires trusting the author. The code was developed with AI
assistance; a human directed, reviewed, and tested every change, and this
page's claims are checked by tests rather than taken on trust.

From the application folder:

```
Get-ChildItem src -Recurse -Filter *.py | Select-String -CaseSensitive -Pattern '^\s*(import|from)\s+requests\b'
```

names every file that imports the networking library, in either Python
spelling (`import requests` or `from requests import ...`). It returns a
single line, in `src/llm_client.py`. (The test suite separately checks that
nothing else in `src/` imports any other network-capable module - raw
sockets included.)

With the application running, this lists the ports it is listening on:

```
Get-NetTCPConnection -State Listen | Where-Object { $_.OwningProcess -in (Get-Process python, pythonw -ErrorAction SilentlyContinue).Id }
```

The command covers every Python program on the machine, so if this
application is the only one running there is exactly one line. Its address
is `127.0.0.1` and its port is different every launch.
Replace `-State Listen` with `-State Established` to see the connections
it has open instead: with the AI feature off, every connection its Python
process holds has `127.0.0.1` as its remote address too.

Both commands are PowerShell, the terminal the install instructions
already tell you to open.

The project's own test suite runs both commands above and checks their
output against the claims on this page — `tests/test_security_md_claims.py`.

You can run the whole suite yourself. The test tools are not part of the
application's requirements; they are pinned in `requirements-dev.txt`.
Install them with `venv\Scripts\python.exe -m pip install -r
requirements-dev.txt`, add the browser the user-interface tests drive
with `venv\Scripts\python.exe -m playwright install chromium` (without
it those tests skip), then run
`venv\Scripts\python.exe -m pytest tests -q` from the application folder.

The full source is about ten thousand lines of readable Python, plus the
interface's HTML and JavaScript, with no build step and no minified assets.
`src/llm_client.py` and `src/routes/web_server.py` are the two files worth
reading.

## Reporting a problem

To report a security issue privately, use "Report a vulnerability" on the
Security tab of this project's GitHub page, which keeps the report private.
Anything else can go in an ordinary issue.
