# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

import setup_answers
from edition import PROVIDERS
from version import APP_LINKS

ADDRESS = re.compile(r"https?://[A-Za-z0-9][^\s\"'<>()`\\\[\]{}^$|]*")
TEXT_SUFFIXES = {".py", ".html", ".js", ".json", ".css", ".txt"}
LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def declared_addresses():
    presets = {spec["preset"]["url"] for spec in PROVIDERS.values() if spec["preset"].get("url")}
    return presets | set(APP_LINKS.values()) | set(setup_answers.SETUP_ADDRESSES)


def application_files():
    files = [path for path in sorted((REPO_ROOT / "src").rglob("*"))
             if path.suffix in TEXT_SUFFIXES and "__pycache__" not in path.parts]
    return [*files, REPO_ROOT / "install.txt", REPO_ROOT / "settings.example.json"]


def is_example(host):
    return host in ("example.com", "example.org") or host.endswith((".example.com", ".test"))


def undeclared(texts, declared):
    found = []
    for name, text in texts:
        for address in ADDRESS.findall(text):
            address = address.rstrip(".,;:")
            try:
                host = (urlsplit(address).hostname or "").lower()
            except ValueError:
                host = ""
            if address in declared or host in LOCAL_HOSTS or is_example(host):
                continue
            found.append(f"{name}: {address}")
    return found


def test_the_application_holds_only_declared_web_addresses():
    files = application_files()
    assert len(files) > 20, files
    texts = [(path.relative_to(REPO_ROOT).as_posix(), path.read_text(encoding="utf-8")) for path in files]
    found = undeclared(texts, declared_addresses())
    assert not found, "web addresses the edition does not declare:\n  " + "\n  ".join(found)


def test_an_undeclared_address_is_reported():
    planted = [
        ("code.py", 'URL = "https://api.service.invalid/v1/chat"\n'),
        ("hint.json", '{"hint": "Open (https://docs.service.invalid/models)."}'),
        ("settings.json", '{"url": "http://10.0.0.5:8080/v1/chat/completions"}'),
        ("fine.py", 'A = "http://127.0.0.1:8000/x"; B = "https://ai.example.com/v1"; C = "https://x.test/"\n'),
    ]
    found = undeclared(planted, {"https://declared.invalid/v1"})
    assert found == ["code.py: https://api.service.invalid/v1/chat",
                     "hint.json: https://docs.service.invalid/models",
                     "settings.json: http://10.0.0.5:8080/v1/chat/completions"]
    assert undeclared([("ok.py", 'U = "https://declared.invalid/v1"')], {"https://declared.invalid/v1"}) == []
