# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


PROVIDERS: dict[str, dict] = {
    "openai": {
        "needs_token": True,
        "local": False,
        "preset": {
            "url": "https://api.openai.com/v1/chat/completions",
            "model": "gpt-5.6",
            "max_tokens_field": "max_completion_tokens",
            "reasoning_handling": "preserve",
        },
    },
    "claude": {
        "needs_token": True,
        "local": False,
        "preset": {
            "url": "https://api.anthropic.com/v1/messages",
            "model": "claude-sonnet-5",
            "auth_header_key": "x-api-key",
            "auth_header_format": "{token}",
            "extra_header_key": "anthropic-version",
            "extra_header_value": "2023-06-01",
            "require_max_tokens": True,
            "system_prompt_location": "top_level",
            "image_payload_style": "base64_dict",
            "response_extraction_path": "content[0].text",
            "reasoning_handling": "parse_claude_blocks",
        },
    },
    "gemini": {
        "needs_token": True,
        "local": False,
        "preset": {
            "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "model": "gemini-3.8-flash",
            "reasoning_handling": "preserve",
        },
    },
    "deepseek": {
        "needs_token": True,
        "local": False,
        "preset": {
            "url": "https://api.deepseek.com/chat/completions",
            "model": "deepseek-v4-flash-vision-exp",
            "reasoning_handling": "ignore_api_field",
        },
    },
    "mistral": {
        "needs_token": True,
        "local": False,
        "preset": {
            "url": "https://api.mistral.ai/v1/chat/completions",
            "model": "mistral-medium-latest",
            "reasoning_handling": "preserve",
        },
    },
    "zai": {
        "needs_token": True,
        "local": False,
        "preset": {
            "url": "https://api.z.ai/api/paas/v4/chat/completions",
            "model": "GLM-4.6V-Flash",
            "reasoning_handling": "preserve",
        },
    },
    "ollama": {
        "needs_token": False,
        "local": True,
        "preset": {
            "url": "http://localhost:11434/v1/chat/completions",
            "model": "qwen3.5:0.8b",
            "auth_header_key": "",
            "auth_header_format": "",
            "reasoning_handling": "strip_xml",
        },
    },
    "lm-studio": {
        "needs_token": False,
        "local": True,
        "preset": {
            "url": "http://localhost:1234/v1/chat/completions",
            "model": "qwen/qwen3.5-2b",
            "auth_header_key": "",
            "auth_header_format": "",
            "reasoning_handling": "strip_xml",
        },
    },
    "custom": {
        "needs_token": False,
        "local": False,
        "preset": {
            "url": "",
            "model": "",
            "reasoning_handling": "preserve",
        },
    },
}

DEFAULT_PROVIDER = "gemini"

REPOSITORY_URL = "https://github.com/condensingcomplexitywithseva/sevas-media-processor"

VERSION_LABEL_SUFFIX = ""
