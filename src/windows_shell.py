# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import ctypes
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

_SHELL_MAX_PATH = 260

if IS_WINDOWS:
    from ctypes import wintypes

    FO_RENAME = 0x0001
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_NOCONFIRMMKDIR = 0x0200
    FOF_NOERRORUI = 0x0400

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

_SHELL_ONLY_ERRORS = {
    0x71: "cannot rename multiple items to a single name",
    0x72: "cannot rename an item to a name that is already in use",
    0x75: "the operation was cancelled",
    0x7C: "the path is too long or invalid",
    0x10000: "an unspecified error occurred on the destination",
}


def is_available() -> bool:
    return IS_WINDOWS


def rename_folder_like_explorer(source: Path, target: Path) -> None:
    if not IS_WINDOWS:
        raise OSError(f"the shell rename is Windows-only: {source} -> {target}")

    if max(len(str(source)), len(str(target))) >= _SHELL_MAX_PATH:
        raise OSError(0, _SHELL_ONLY_ERRORS[0x7C], str(source), 0x7C, str(target))

    operation = SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = FO_RENAME
    operation.pFrom = f"{source}\0\0"
    operation.pTo = f"{target}\0\0"
    operation.fFlags = (
        FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOCONFIRMMKDIR | FOF_NOERRORUI
    )

    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))

    if code == 0 and not operation.fAnyOperationsAborted:
        return

    if operation.fAnyOperationsAborted and code == 0:
        message = "the shell aborted the rename"
    else:
        message = _SHELL_ONLY_ERRORS.get(code) or ctypes.FormatError(code)
    raise OSError(0, message, str(source), code, str(target))
