# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import subprocess
import sys


def test_installed_packages_are_mutually_consistent():
    result = subprocess.run(
        [sys.executable, "-m", "pip", "check", "--disable-pip-version-check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "pip check found the installed environment inconsistent - some "
        "package's declared requirements are not met by what is installed. "
        "Rebuild the venv from requirements.txt rather than patching one "
        "package at a time:\n" + result.stdout + result.stderr
    )
