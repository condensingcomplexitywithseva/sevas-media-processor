# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from PIL import Image

from fake_llm import make_server
from fake_llm.harness import REQUEST_FORMATS, build_preset_client

from config_validator import ProviderConfig, _default_provider_configs

PRESETS = [name for name in _default_provider_configs() if name != "custom"]


@pytest.fixture
def jpeg(tmp_path):
    p = tmp_path / "frame.jpg"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(p, "JPEG")
    return p


def test_every_preset_has_a_simulator():
    assert sorted(PRESETS) == sorted(REQUEST_FORMATS)


def format_of(preset: ProviderConfig, provider: str) -> ProviderConfig:
    spec = {k: v for k, v in REQUEST_FORMATS[provider].items() if k not in ("path", "model")}
    return ProviderConfig(url=preset.url, model=preset.model, **spec)


@pytest.mark.parametrize("provider", PRESETS)
def test_each_preset_is_its_simulators_request_format(provider):
    preset = _default_provider_configs()[provider]
    assert preset == format_of(preset, provider)
    assert urlparse(preset.url).path == REQUEST_FORMATS[provider]["path"]


@pytest.mark.parametrize("provider", PRESETS)
def test_real_client_round_trips_on_each_preset(jpeg, provider):
    srv = make_server(provider).start()
    try:
        client = build_preset_client(provider, srv.base_url)
        outcomes = client.execute_network_inference([(1, jpeg, "")])
        assert [o.status for o in outcomes] == ["ok"], \
            f"{provider}: {[o.error for o in outcomes]}"
        assert "quick brown fox" in outcomes[0].raw_answer.lower()
    finally:
        srv.stop()


def test_the_format_check_bites():
    preset = _default_provider_configs()["claude"]
    drifted = preset.model_copy(update={"image_payload_style": "data_uri"})
    assert drifted != format_of(drifted, "claude")
