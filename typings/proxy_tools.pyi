# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0
# Local stub: proxy_tools ships no py.typed. Its module_property wraps a
# zero-arg function in a Proxy that forwards every operation to the
# function's result, so attribute access yields the return value at
# runtime - declared as exactly that. Without this, a checker that
# cannot see through the untyped decorator types webview.screens as the
# bare function and flags webview.screens[0] (calling it instead would
# crash: Proxy.__call__ forwards to the returned list).

from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")

def module_property(func: Callable[[], _T]) -> _T: ...
