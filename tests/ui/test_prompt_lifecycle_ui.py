# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from fake_llm import make_server
from fake_llm.harness import KEY_SHAPED_TOKENS

GARBAGE = "never a prompt".encode("utf-16-le")
RECOVERY_PROMPT = "Begin with the exact text UI-RECOVERY-TAG then transcribe."


def start_button(page):
    return page.evaluate(
        """() => {
            const b = document.getElementById('btn-start');
            return { disabled: b.disabled, title: b.title };
        }"""
    )


def test_apply_with_garbage_prompt_draft_is_refused_loudly(open_page, tmp_path):
    bad = tmp_path / "bad_prompt.txt"
    bad.write_bytes(GARBAGE)
    page = open_page({"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "ollama"})
    settings_file = tmp_path / "settings.json"
    before = settings_file.read_bytes()

    page.evaluate("window.switchTab('ai')")
    page.wait_for_timeout(400)
    page.select_option("#user_prompt_mode", "FILE")
    page.wait_for_timeout(300)
    page.fill("#user_file_input", str(bad))
    page.wait_for_timeout(300)

    drafted = start_button(page)
    assert drafted["disabled"], "Start must be refused while a draft is pending"
    assert "applied or discarded" in drafted["title"]

    page.click("#btn-apply")
    page.wait_for_function(
        "getComputedStyle(document.getElementById('error-toast')).opacity === '1'",
        timeout=5000,
    )
    state = page.evaluate(
        """() => ({
            toast: document.getElementById('error-toast').innerText,
            field_err: document.getElementById('err-LLM_USER_PROMPT').innerText,
        })"""
    )
    assert "not saved" in state["toast"].lower(), state
    assert "not readable text" in state["field_err"], state
    assert settings_file.read_bytes() == before, \
        "a refused Apply wrote bytes to settings.json"
    after = start_button(page)
    assert after["disabled"], "Start must still be refused after the failed Apply"


def test_garbage_on_disk_fails_loud_then_recovery_run_succeeds(open_page, tmp_path):
    from config_validator import _default_provider_configs

    bad = tmp_path / "bad_prompt.txt"
    bad.write_bytes(GARBAGE)

    srv = make_server("gemini").start()
    try:
        preset = _default_provider_configs()["gemini"]
        entry = preset.model_copy(
            update={"url": srv.base_url.rstrip("/") + urlparse(preset.url).path}
        ).model_dump(mode="json")

        page = open_page(
            {
                "ENABLE_LLM_INFERENCE": True,
                "LLM_PROVIDER": "gemini",
                "LLM_PROVIDERS": {"gemini": entry},
                "LLM_USER_PROMPT_MODE": "FILE",
                "LLM_USER_PROMPT": str(bad),
            },
            tokens={"gemini": KEY_SHAPED_TOKENS["gemini"]},
        )
        Image.new("RGB", (320, 200), (60, 120, 200)).save(
            tmp_path / "input" / "probe.png"
        )

        page.evaluate("window.switchTab('ai')")
        page.wait_for_timeout(900)

        loud = page.evaluate(
            """() => ({
                banner: document.body.innerText.includes('Errors in the settings'),
                preview: document.getElementById('user_file_preview').innerText,
                field_err: document.getElementById('err-LLM_USER_PROMPT').innerText,
            })"""
        )
        assert loud["banner"], "the error banner must name the problem on load"
        assert "could not be read as text" in loud["preview"].lower(), loud
        assert "not readable text" in loud["field_err"], loud
        refused = start_button(page)
        assert refused["disabled"], "Start must be refused with garbage on disk"
        assert "Fix Errors" in refused["title"], refused

        page.select_option("#user_prompt_mode", "TEXT")
        page.wait_for_timeout(300)
        page.fill("#user_text_input", RECOVERY_PROMPT)
        page.wait_for_timeout(300)
        page.click("#btn-apply")
        page.wait_for_function(
            "getComputedStyle(document.getElementById('save-toast')).opacity === '1'",
            timeout=5000,
        )
        recovered = start_button(page)
        assert not recovered["disabled"], "Start must come back after the fix"

        page.click("#btn-start")
        page.wait_for_function(
            """() => {
                const s = document.getElementById('run-status');
                return getComputedStyle(s).display !== 'none' && s.className.startsWith('status-');
            }""",
            timeout=30000,
        )
        status = page.evaluate("document.getElementById('run-status').className")
        assert status == "status-done", f"the recovered run must succeed, got {status}"

        assert any("UI-RECOVERY-TAG" in r.text for r in srv.requests), \
            "no request to the fake gemini carried the recovery prompt"

        registries = list((tmp_path / "output").rglob("file_registry_*.csv"))
        assert registries, "the run must export the file registry CSV"
        registry_text = registries[-1].read_text(encoding="utf-8-sig", errors="replace")
        assert "ok" in registry_text and "probe.png" in registry_text
    finally:
        srv.stop()
