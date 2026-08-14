# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import requests

from fake_llm import available, make_server


@pytest.fixture
def server(request):
    srv = make_server(request.param).start()
    yield srv
    srv.stop()


def _sse_text(resp):
    return resp.text



_HAPPY = {
    "openai":   ("/v1/chat/completions", {"Authorization": "Bearer sk-x"},
                 {"model": "gpt-5.5", "messages": [{"role": "user", "content": "hi"}]}),
    "claude":   ("/v1/messages", {"x-api-key": "sk-ant-x", "anthropic-version": "2023-06-01"},
                 {"model": "claude-sonnet-5", "max_tokens": 50,
                  "messages": [{"role": "user", "content": "hi"}]}),
    "gemini":   ("/v1beta/models/gemini-3.5-flash:generateContent", {"x-goog-api-key": "AIzaX"},
                 {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}),
    "deepseek": ("/chat/completions", {"Authorization": "Bearer sk-x"},
                 {"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "hi"}]}),
    "mistral":  ("/v1/chat/completions", {"Authorization": "Bearer anykey"},
                 {"model": "mistral-medium-latest", "messages": [{"role": "user", "content": "hi"}]}),
    "ollama":   ("/v1/chat/completions", {},
                 {"model": "qwen3.5:0.8b", "messages": [{"role": "user", "content": "hi"}]}),
    "lm-studio": ("/v1/chat/completions", {},
                  {"model": "qwen/qwen3.5:0.8b", "messages": [{"role": "user", "content": "hi"}]}),
}


@pytest.mark.parametrize("server", available(), indirect=True)
def test_every_provider_happy_path(server):
    path, headers, body = _HAPPY[server.name]
    r = requests.post(server.base_url + path, headers=headers, json=body)
    assert r.status_code == 200, r.text
    assert server.requests and server.requests[-1].json_ok



@pytest.mark.parametrize("server", ["openai"], indirect=True)
class TestOpenAI:
    URL = "/v1/chat/completions"
    GOOD = {"Authorization": "Bearer sk-fake"}

    def post(self, server, body, headers=None):
        return requests.post(server.base_url + self.URL,
                             headers=headers if headers is not None else self.GOOD, json=body)

    def test_missing_auth_401(self, server):
        r = self.post(server, {"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]}, headers={})
        assert r.status_code == 401

    def test_bad_key_shape_401_invalid_api_key(self, server):
        r = self.post(server, {"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]},
                      headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"

    def test_bad_model_is_404_not_400(self, server):
        r = self.post(server, {"model": "gpt-6-ultra", "messages": [{"role": "user", "content": "x"}]})
        assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"

    def test_missing_model_400(self, server):
        r = self.post(server, {"messages": [{"role": "user", "content": "x"}]})
        assert r.status_code == 400 and r.json()["error"]["code"] == "missing_required_parameter"

    def test_gpt5_rejects_max_tokens(self, server):
        r = self.post(server, {"model": "gpt-5.5", "messages": [{"role": "user", "content": "x"}],
                               "max_tokens": 100})
        assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_parameter"

    def test_reasoning_tokens_counted_hidden(self, server):
        r = self.post(server, {"model": "gpt-5.5", "messages": [{"role": "user", "content": "x"}],
                               "max_completion_tokens": 100})
        details = r.json()["usage"]["completion_tokens_details"]
        assert details["reasoning_tokens"] > 0

    def test_streaming_sse_terminates_with_done(self, server):
        r = self.post(server, {"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}],
                               "stream": True})
        assert "chat.completion.chunk" in r.text and r.text.strip().endswith("[DONE]")



@pytest.mark.parametrize("server", ["claude"], indirect=True)
class TestAnthropic:
    URL = "/v1/messages"
    GOOD = {"x-api-key": "sk-ant-fake", "anthropic-version": "2023-06-01"}
    BODY = {"model": "claude-3-7-sonnet-latest", "max_tokens": 50,
            "messages": [{"role": "user", "content": "hi"}]}

    def post(self, server, body=None, headers=None):
        return requests.post(server.base_url + self.URL,
                             headers=self.GOOD if headers is None else headers,
                             json=self.BODY if body is None else body)

    def test_uses_x_api_key_not_bearer(self, server):
        r = self.post(server)
        assert r.status_code == 200
        assert r.json()["content"][0]["type"] == "text"

    def test_missing_version_header_400(self, server):
        r = self.post(server, headers={"x-api-key": "sk-ant-fake"})
        assert r.status_code == 400

    def test_bogus_version_value_400(self, server):
        r = self.post(server, headers={"x-api-key": "sk-ant-fake",
                                       "anthropic-version": "banana"})
        assert r.status_code == 400
        assert "not a valid version" in r.json()["error"]["message"]

    def test_older_valid_version_accepted(self, server):
        r = self.post(server, headers={"x-api-key": "sk-ant-fake",
                                       "anthropic-version": "2023-01-01"})
        assert r.status_code == 200

    def test_missing_key_401_auth_error(self, server):
        r = self.post(server, headers={"anthropic-version": "2023-06-01"})
        assert r.status_code == 401 and r.json()["error"]["type"] == "authentication_error"

    def test_both_auth_headers_401(self, server):
        h = dict(self.GOOD, Authorization="Bearer sk-ant-fake")
        r = self.post(server, headers=h)
        assert r.status_code == 401

    def test_missing_max_tokens_400(self, server):
        body = {"model": "claude-3-7-sonnet-latest",
                "messages": [{"role": "user", "content": "hi"}]}
        assert self.post(server, body=body).status_code == 400

    def test_bad_model_404_not_found(self, server):
        body = dict(self.BODY, model="claude-9-nonexistent")
        r = self.post(server, body=body)
        assert r.status_code == 404 and r.json()["error"]["type"] == "not_found_error"

    def test_first_message_must_be_user(self, server):
        body = dict(self.BODY, messages=[{"role": "assistant", "content": "hi"}])
        assert self.post(server, body=body).status_code == 400

    def test_thinking_block_prepended_when_requested(self, server):
        body = dict(self.BODY, model="claude-sonnet-5",
                    thinking={"type": "adaptive"})
        blocks = self.post(server, body=body).json()["content"]
        assert blocks[0]["type"] == "thinking"

    def test_thinking_disabled_yields_no_block(self, server):
        body = dict(self.BODY, thinking={"type": "disabled"})
        blocks = self.post(server, body=body).json()["content"]
        assert all(b["type"] != "thinking" for b in blocks)

    def test_adaptive_thinking_rejected_on_legacy_model(self, server):
        body = dict(self.BODY, thinking={"type": "adaptive"})
        r = self.post(server, body=body)
        assert r.status_code == 400
        assert r.json()["error"]["type"] == "invalid_request_error"

    def test_newest_model_rejects_budget_tokens(self, server):
        body = dict(self.BODY, model="claude-sonnet-5",
                    thinking={"type": "enabled", "budget_tokens": 2048})
        r = self.post(server, body=body)
        assert r.status_code == 400
        assert "budget_tokens" in r.json()["error"]["message"]

    def test_legacy_model_accepts_budget_tokens(self, server):
        body = dict(self.BODY,
                    thinking={"type": "enabled", "budget_tokens": 2048})
        r = self.post(server, body=body)
        assert r.status_code == 200
        assert r.json()["content"][0]["type"] == "thinking"

    def test_newest_model_rejects_temperature(self, server):
        body = dict(self.BODY, model="claude-sonnet-5", temperature=0.7)
        r = self.post(server, body=body)
        assert r.status_code == 400
        assert r.json()["error"]["type"] == "invalid_request_error"

    def test_legacy_model_accepts_temperature(self, server):
        body = dict(self.BODY, temperature=0.7)
        assert self.post(server, body=body).status_code == 200

    def test_newest_model_rejects_prefill(self, server):
        body = dict(self.BODY, model="claude-sonnet-5",
                    messages=[{"role": "user", "content": "hi"},
                              {"role": "assistant", "content": "The answer is"}])
        r = self.post(server, body=body)
        assert r.status_code == 400

    def test_legacy_model_accepts_prefill(self, server):
        body = dict(self.BODY,
                    messages=[{"role": "user", "content": "hi"},
                              {"role": "assistant", "content": "The answer is"}])
        assert self.post(server, body=body).status_code == 200

    def test_bad_message_role_400(self, server):
        body = dict(self.BODY,
                    messages=[{"role": "user", "content": "hi"},
                              {"role": "system", "content": "nope"}])
        r = self.post(server, body=body)
        assert r.status_code == 400
        assert "role" in r.json()["error"]["message"]

    def test_stop_details_present_and_null(self, server):
        r = self.post(server)
        body = r.json()
        assert "stop_details" in body and body["stop_details"] is None

    def test_streaming_has_no_done_terminator(self, server):
        body = dict(self.BODY, stream=True)
        r = self.post(server, body=body)
        assert "message_start" in r.text and "[DONE]" not in r.text



@pytest.mark.parametrize("server", ["gemini"], indirect=True)
class TestGemini:
    GEN = "/v1beta/models/gemini-2.0-flash:generateContent"
    BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}

    def test_native_happy_path(self, server):
        r = requests.post(server.base_url + self.GEN, headers={"x-goog-api-key": "AIzaX"}, json=self.BODY)
        assert r.status_code == 200
        assert r.json()["candidates"][0]["content"]["parts"][0]["text"]

    def test_query_param_key_auth(self, server):
        r = requests.post(server.base_url + self.GEN + "?key=AIzaX", json=self.BODY)
        assert r.status_code == 200

    def test_missing_key_403(self, server):
        r = requests.post(server.base_url + self.GEN, json=self.BODY)
        assert r.status_code == 403

    def test_bad_key_is_400_not_401(self, server):
        r = requests.post(server.base_url + self.GEN, headers={"x-goog-api-key": "badkey"}, json=self.BODY)
        assert r.status_code == 400
        assert r.json()["error"]["status"] == "INVALID_ARGUMENT"

    def test_assistant_role_rejected(self, server):
        body = {"contents": [{"role": "assistant", "parts": [{"text": "hi"}]}]}
        r = requests.post(server.base_url + self.GEN, headers={"x-goog-api-key": "AIzaX"}, json=body)
        assert r.status_code == 400

    def test_bad_model_404(self, server):
        url = server.base_url + "/v1beta/models/gemini-9.9-nope:generateContent"
        r = requests.post(url, headers={"x-goog-api-key": "AIzaX"}, json=self.BODY)
        assert r.status_code == 404

    def test_stream_sse_has_no_done(self, server):
        url = server.base_url + "/v1beta/models/gemini-2.0-flash:streamGenerateContent?alt=sse"
        r = requests.post(url, headers={"x-goog-api-key": "AIzaX"}, json=self.BODY)
        assert "candidates" in r.text and "[DONE]" not in r.text

    def test_unknown_top_level_field_400(self, server):
        body = dict(self.BODY, max_tokens=100)
        r = requests.post(server.base_url + self.GEN,
                          headers={"x-goog-api-key": "AIzaX"}, json=body)
        assert r.status_code == 400
        err = r.json()["error"]
        assert err["status"] == "INVALID_ARGUMENT"
        assert 'Unknown name "max_tokens"' in err["message"]
        assert "Cannot find field" in err["message"]

    def test_unknown_generation_config_field_400(self, server):
        body = dict(self.BODY, generationConfig={"maxOutputTokens": 10,
                                                 "max_completion_tokens": 5})
        r = requests.post(server.base_url + self.GEN,
                          headers={"x-goog-api-key": "AIzaX"}, json=body)
        assert r.status_code == 400
        assert "at 'generation_config'" in r.json()["error"]["message"]

    def test_known_fields_accepted_in_both_spellings(self, server):
        body = dict(self.BODY,
                    generation_config={"max_output_tokens": 64,
                                       "thinkingConfig": {"includeThoughts": True}},
                    safetySettings=[])
        r = requests.post(server.base_url + self.GEN,
                          headers={"x-goog-api-key": "AIzaX"}, json=body)
        assert r.status_code == 200

    def test_fake_answer_hook_survives_strictness(self, server):
        body = dict(self.BODY, fake_answer="steered!")
        r = requests.post(server.base_url + self.GEN,
                          headers={"x-goog-api-key": "AIzaX"}, json=body)
        assert r.status_code == 200
        assert r.json()["candidates"][0]["content"]["parts"][-1]["text"] == "steered!"

    def test_openai_shim_bearer(self, server):
        r = requests.post(server.base_url + "/v1beta/openai/chat/completions",
                          headers={"Authorization": "Bearer AIzaX"},
                          json={"model": "gemini-2.0-flash", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"]



@pytest.mark.parametrize("server", ["deepseek"], indirect=True)
class TestDeepSeek:
    URL = "/chat/completions"
    GOOD = {"Authorization": "Bearer sk-fake"}

    def post(self, server, body, headers=None):
        return requests.post(server.base_url + self.URL,
                             headers=self.GOOD if headers is None else headers, json=body)

    def test_chat_no_reasoning_content(self, server):
        r = self.post(server, {"model": "deepseek-chat", "messages": [{"role": "user", "content": "x"}]})
        assert "reasoning_content" not in r.json()["choices"][0]["message"]

    def test_reasoner_has_reasoning_content(self, server):
        r = self.post(server, {"model": "deepseek-reasoner", "messages": [{"role": "user", "content": "x"}]})
        assert r.json()["choices"][0]["message"]["reasoning_content"]

    def test_reasoning_content_echoed_in_input_400(self, server):
        body = {"model": "deepseek-chat",
                "messages": [{"role": "assistant", "content": "prev", "reasoning_content": "cot"},
                             {"role": "user", "content": "x"}]}
        assert self.post(server, body).status_code == 400

    def test_bad_key_401(self, server):
        r = self.post(server, {"model": "deepseek-chat", "messages": [{"role": "user", "content": "x"}]},
                      headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401

    def test_alias_v1_path(self, server):
        r = requests.post(server.base_url + "/v1/chat/completions", headers=self.GOOD,
                          json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "x"}]})
        assert r.status_code == 200

    def test_balance_endpoint(self, server):
        r = requests.get(server.base_url + "/user/balance", headers=self.GOOD)
        assert r.json()["is_available"] is True



@pytest.mark.parametrize("server", ["mistral"], indirect=True)
class TestMistral:
    URL = "/v1/chat/completions"
    GOOD = {"Authorization": "Bearer anyopaquekey"}
    BODY = {"model": "mistral-large-latest", "messages": [{"role": "user", "content": "hi"}]}

    def post(self, server, body=None, headers=None):
        return requests.post(server.base_url + self.URL,
                             headers=self.GOOD if headers is None else headers,
                             json=self.BODY if body is None else body)

    def test_happy_id_prefix_and_no_fingerprint(self, server):
        j = self.post(server).json()
        assert j["id"].startswith("cmpl-")
        assert "system_fingerprint" not in j

    def test_missing_auth_401_minimal_body(self, server):
        r = self.post(server, headers={})
        assert r.status_code == 401 and r.json().get("message") == "Unauthorized"

    def test_unknown_field_422_extra_forbidden(self, server):
        body = dict(self.BODY, max_completion_tokens=8192)
        r = self.post(server, body=body)
        assert r.status_code == 422
        assert r.json()["message"]["detail"][0]["type"] == "extra_forbidden"

    def test_bad_model_400_numeric_string_code(self, server):
        body = dict(self.BODY, model="gpt-4")
        r = self.post(server, body=body)
        assert r.status_code == 400 and r.json()["code"] == "1500"

    def test_reasoning_effort_makes_content_an_array(self, server):
        body = dict(self.BODY, reasoning_effort="medium")
        content = self.post(server, body=body).json()["choices"][0]["message"]["content"]
        assert isinstance(content, list)
        assert any(c["type"] == "thinking" for c in content)

    def test_no_reasoning_content_is_string(self, server):
        content = self.post(server).json()["choices"][0]["message"]["content"]
        assert isinstance(content, str)

    @staticmethod
    def _chunks(resp):
        return [json.loads(l[len("data: "):]) for l in resp.text.splitlines()
                if l.startswith("data: ") and l != "data: [DONE]"]

    def test_stream_final_chunk_carries_usage_without_opt_in(self, server):
        r = self.post(server, body=dict(self.BODY, stream=True))
        assert r.text.strip().endswith("[DONE]")
        chunks = self._chunks(r)
        final = chunks[-1]
        assert final["choices"][0]["finish_reason"] == "stop"
        assert final["usage"]["total_tokens"] > 0
        for c in chunks[:-1]:
            assert "usage" not in c
        for c in chunks:
            assert "system_fingerprint" not in c
            assert "logprobs" not in c["choices"][0]

    def test_stream_options_is_unknown_field_422(self, server):
        body = dict(self.BODY, stream=True,
                    stream_options={"include_usage": True})
        r = self.post(server, body=body)
        assert r.status_code == 422
        detail = r.json()["message"]["detail"][0]
        assert detail["type"] == "extra_forbidden"
        assert detail["loc"] == ["body", "stream_options"]

    def test_stream_reasoning_uses_content_array_chunks(self, server):
        body = dict(self.BODY, stream=True, reasoning_effort="medium")
        deltas = [c["choices"][0]["delta"] for c in self._chunks(self.post(server, body=body))]
        arrays = [d["content"] for d in deltas if isinstance(d.get("content"), list)]
        assert any(chunk[0]["type"] == "thinking" for chunk in arrays)
        assert any(chunk[0]["type"] == "text" for chunk in arrays)



@pytest.mark.parametrize("server", ["ollama"], indirect=True)
class TestOllama:
    def test_native_non_stream(self, server):
        r = requests.post(server.base_url + "/api/chat",
                          json={"model": "qwen3.5:0.8b", "stream": False,
                                "messages": [{"role": "user", "content": "hi"}]})
        j = r.json()
        assert j["done"] is True and j["message"]["content"]
        assert isinstance(j["total_duration"], int)

    def test_native_default_streams_ndjson(self, server):
        r = requests.post(server.base_url + "/api/chat",
                          json={"model": "qwen3.5:0.8b",
                                "messages": [{"role": "user", "content": "hi"}]})
        lines = [json.loads(l) for l in r.text.splitlines() if l.strip()]
        assert lines[-1]["done"] is True
        assert "data:" not in r.text

    def test_think_true_populates_thinking(self, server):
        r = requests.post(server.base_url + "/api/chat",
                          json={"model": "qwen3.5:0.8b", "stream": False, "think": True,
                                "messages": [{"role": "user", "content": "hi"}]})
        assert r.json()["message"]["thinking"]

    def test_auth_ignored(self, server):
        r = requests.post(server.base_url + "/api/chat",
                          headers={"Authorization": "Bearer whatever"},
                          json={"model": "qwen3.5:0.8b", "stream": False,
                                "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200

    def test_unknown_model_404_flat(self, server):
        r = requests.post(server.base_url + "/api/chat",
                          json={"model": "nope:1b", "stream": False,
                                "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 404
        assert "not found, try pulling it first" in r.json()["error"]

    def test_v1_surface_fp_ollama(self, server):
        r = requests.post(server.base_url + "/v1/chat/completions",
                          json={"model": "qwen3.5:0.8b", "messages": [{"role": "user", "content": "hi"}]})
        assert r.json()["system_fingerprint"] == "fp_ollama"

    def test_v1_bad_model_wrapped_envelope(self, server):
        r = requests.post(server.base_url + "/v1/chat/completions",
                          json={"model": "nope:1b", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 404 and r.json()["error"]["type"] == "not_found_error"



@pytest.mark.parametrize("server", ["lm-studio"], indirect=True)
class TestLMStudio:
    def test_v1_chat_id_shape_and_powered_by(self, server):
        r = requests.post(server.base_url + "/v1/chat/completions",
                          json={"model": "qwen/qwen3.5:0.8b", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        assert r.headers.get("X-Powered-By") == "Express"
        import re
        assert re.fullmatch(r"chatcmpl-[a-z0-9]{25}", r.json()["id"])

    def test_no_auth_needed(self, server):
        r = requests.post(server.base_url + "/v1/chat/completions",
                          json={"model": "qwen/qwen3.5:0.8b", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200

    def test_v0_surface_has_stats(self, server):
        r = requests.post(server.base_url + "/api/v0/chat/completions",
                          json={"model": "qwen/qwen3.5:0.8b", "messages": [{"role": "user", "content": "hi"}]})
        j = r.json()
        assert j["stats"]["stop_reason"] in ("eosFound", "maxPredictedTokensReached")
        assert j["model_info"] and j["runtime"]

    def test_wrong_route_returns_200_with_error(self, server):
        r = requests.post(server.base_url + "/chat/completions",
                          json={"model": "qwen/qwen3.5:0.8b", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        assert "Unexpected endpoint or method" in r.json()["error"]

    def test_unknown_model_404(self, server):
        r = requests.post(server.base_url + "/v1/chat/completions",
                          json={"model": "no-such-model", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 404



@pytest.mark.parametrize("server", ["openai"], indirect=True)
def test_scripting_overrides_real_logic(server):
    server.queue(status=500, json={"error": "boom"})
    r = requests.post(server.base_url + "/v1/chat/completions",
                      headers={"Authorization": "Bearer sk-x"},
                      json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 500
    r2 = requests.post(server.base_url + "/v1/chat/completions",
                       headers={"Authorization": "Bearer sk-x"},
                       json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert r2.status_code == 200
