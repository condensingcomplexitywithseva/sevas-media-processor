# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0
# Local stub for the one System.Windows.Forms member src/main.py uses.
from collections.abc import Callable

class MethodInvoker:
    def __init__(self, target: Callable[[], None]) -> None: ...
