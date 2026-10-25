# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import ctypes
import sys
from pathlib import Path

from fs_utils import plain_path

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
    source, target = Path(plain_path(str(source))), Path(plain_path(str(target)))

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


if IS_WINDOWS:
    from ctypes import wintypes

    _shell32 = ctypes.WinDLL("shell32")
    _ole32 = ctypes.WinDLL("ole32")
    _shell32.ILCreateFromPathW.argtypes = [wintypes.LPCWSTR]
    _shell32.ILCreateFromPathW.restype = ctypes.c_void_p
    _shell32.ILFree.argtypes = [ctypes.c_void_p]
    _shell32.ILFree.restype = None
    _shell32.SHOpenFolderAndSelectItems.argtypes = [
        ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.DWORD]
    _shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    _ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _ole32.CoInitializeEx.restype = ctypes.c_long
    _ole32.CoUninitialize.argtypes = []
    _ole32.CoUninitialize.restype = None

    COINIT_APARTMENTTHREADED = 0x2


def show_in_explorer(path: Path) -> bool:
    if not IS_WINDOWS:
        return False
    initialized = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    try:
        item = _shell32.ILCreateFromPathW(plain_path(str(path)))
        if not item:
            return False
        try:
            return _shell32.SHOpenFolderAndSelectItems(item, 0, None, 0) >= 0
        finally:
            _shell32.ILFree(item)
    finally:
        if initialized >= 0:
            _ole32.CoUninitialize()
