# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from user_data import DEFAULT_INPUT_FOLDER, DEFAULT_OUTPUT_FOLDER


@pytest.fixture(autouse=True)
def fresh_working_folder(tmp_path_factory, monkeypatch):
    folder = tmp_path_factory.mktemp("working_folder")
    for name in (DEFAULT_INPUT_FOLDER, DEFAULT_OUTPUT_FOLDER):
        (folder / name).mkdir()
    monkeypatch.chdir(folder)
    return folder
