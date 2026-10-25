# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import ctypes
import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import windows_shell
import contextlib

on_windows = pytest.mark.skipif(
    not windows_shell.IS_WINDOWS, reason="the shell rename is a Windows API"
)


def test_availability_tracks_the_platform():
    assert windows_shell.is_available() is (sys.platform == "win32")


@on_windows
def test_it_renames_a_folder_with_its_contents(tmp_path):
    source = tmp_path / "current_run"
    (source / "TECH").mkdir(parents=True)
    (source / "page_1.jpg").write_bytes(b"a jpeg")
    (source / "TECH" / "application_state.db").write_bytes(b"a database")
    target = tmp_path / "old_current_run_2026-08-08_01-54-41"

    windows_shell.rename_folder_like_explorer(source, target)

    assert not source.exists()
    assert (target / "page_1.jpg").read_bytes() == b"a jpeg"
    assert (target / "TECH" / "application_state.db").read_bytes() == b"a database"


@on_windows
def test_a_missing_source_raises_oserror_carrying_both_paths(tmp_path):
    source = tmp_path / "never_existed"
    target = tmp_path / "old_never_existed"

    with pytest.raises(OSError) as raised:
        windows_shell.rename_folder_like_explorer(source, target)

    assert raised.value.filename == str(source)
    assert raised.value.filename2 == str(target)
    assert raised.value.strerror
    assert not target.exists()


@on_windows
def test_an_existing_target_is_never_silently_overwritten(tmp_path):
    source = tmp_path / "current_run"
    source.mkdir()
    (source / "new.jpg").write_bytes(b"new")
    target = tmp_path / "old_current_run"
    target.mkdir()
    (target / "precious.jpg").write_bytes(b"an earlier archive")

    with contextlib.suppress(OSError):
        windows_shell.rename_folder_like_explorer(source, target)
    assert (target / "precious.jpg").read_bytes() == b"an earlier archive"


def test_it_refuses_rather_than_crashes_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_shell, "IS_WINDOWS", False)

    with pytest.raises(OSError):
        windows_shell.rename_folder_like_explorer(tmp_path / "a", tmp_path / "b")



AWKWARD_NAMES = [
    "plain", "a b", "x  y", "a & b", "OneDrive - Acme, Inc", "a,b", "a;b",
    "it's", "100%", "%USERNAME%", "a^b", "a!b", "#tag", "a=b", "(par)",
    "a[1]", "\u041f\u0430\u043f\u043a\u0430",
    "\u041e\u0442\u0447\u0451\u0442, 2026 & 100% (\u0451)",
    "\U0001F600 emoji", "mix, & it's 100% (ok)!",
]


def _shell_round_trip(path: Path) -> str | None:
    shell32 = ctypes.WinDLL("shell32")
    shell32.ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
    shell32.ILCreateFromPathW.restype = ctypes.c_void_p
    shell32.ILFree.argtypes = [ctypes.c_void_p]
    shell32.SHGetPathFromIDListEx.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_int]
    item = shell32.ILCreateFromPathW(str(path))
    if not item:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        assert shell32.SHGetPathFromIDListEx(item, buffer, len(buffer), 0)
        return buffer.value
    finally:
        shell32.ILFree(item)


@on_windows
@pytest.mark.parametrize("name", AWKWARD_NAMES)
def test_the_shell_reads_any_folder_name_as_exactly_that_file(tmp_path, name):
    report = tmp_path / name / "report.xlsx"
    report.parent.mkdir()
    report.write_bytes(b"xlsx")
    assert _shell_round_trip(report) == str(report)


@on_windows
def test_the_shell_reads_a_path_longer_than_260_characters(tmp_path):
    folder = tmp_path
    while len(str(folder)) < 290:
        folder = folder / ("long_segment_" + "x" * 20)
    os.makedirs("\\\\?\\" + str(folder))
    report = folder / "report.xlsx"
    Path("\\\\?\\" + str(report)).write_bytes(b"xlsx")
    assert len(str(report)) > 260
    assert _shell_round_trip(report) == str(report)


@on_windows
def test_the_shell_makes_no_item_of_a_missing_file(tmp_path):
    assert _shell_round_trip(tmp_path / "missing" / "report.xlsx") is None


class _Recorder:

    def __init__(self, item=0x1234, opened=0, initialized=0):
        self.calls = []
        self.item, self.opened, self.initialized = item, opened, initialized

    def ILCreateFromPathW(self, path):
        self.calls.append(("ILCreateFromPathW", path))
        return self.item

    def SHOpenFolderAndSelectItems(self, item, count, children, flags):
        self.calls.append(("SHOpenFolderAndSelectItems", item, count, children, flags))
        return self.opened

    def ILFree(self, item):
        self.calls.append(("ILFree", item))

    def CoInitializeEx(self, reserved, mode):
        self.calls.append(("CoInitializeEx", mode))
        return self.initialized

    def CoUninitialize(self):
        self.calls.append(("CoUninitialize",))


@pytest.fixture
def recorder(monkeypatch):
    def install(**outcome):
        fake = _Recorder(**outcome)
        monkeypatch.setattr(windows_shell, "IS_WINDOWS", True)
        monkeypatch.setattr(windows_shell, "_shell32", fake, raising=False)
        monkeypatch.setattr(windows_shell, "_ole32", fake, raising=False)
        monkeypatch.setattr(windows_shell, "COINIT_APARTMENTTHREADED", 0x2, raising=False)
        return fake
    return install


def test_show_hands_the_path_over_verbatim_and_frees_the_item(recorder):
    fake = recorder()
    path = Path("D:\\John Smith\\OneDrive - Acme, Inc\\Desktop\\exports\\report.xlsx")
    assert windows_shell.show_in_explorer(path) is True
    assert fake.calls == [
        ("CoInitializeEx", 0x2),
        ("ILCreateFromPathW", str(path)),
        ("SHOpenFolderAndSelectItems", 0x1234, 0, None, 0),
        ("ILFree", 0x1234),
        ("CoUninitialize",),
    ]


def test_show_reports_a_path_the_shell_cannot_resolve(recorder):
    fake = recorder(item=None)
    assert windows_shell.show_in_explorer(Path("C:\\missing\\report.xlsx")) is False
    assert [c[0] for c in fake.calls] == ["CoInitializeEx", "ILCreateFromPathW", "CoUninitialize"]


def test_show_reports_a_refused_call_and_still_frees_the_item(recorder):
    fake = recorder(opened=-2147467259)
    assert windows_shell.show_in_explorer(Path("C:\\exports\\report.xlsx")) is False
    assert ("ILFree", 0x1234) in fake.calls and fake.calls[-1] == ("CoUninitialize",)


@pytest.mark.parametrize("initialized, paired", [(0, True), (1, True), (-2147417850, False)])
def test_show_pairs_com_setup_only_when_it_succeeded(recorder, initialized, paired):
    fake = recorder(initialized=initialized)
    assert windows_shell.show_in_explorer(Path("C:\\exports\\report.xlsx")) is True
    assert (("CoUninitialize",) in fake.calls) is paired
    assert any(c[0] == "SHOpenFolderAndSelectItems" for c in fake.calls)


def test_show_is_false_off_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(windows_shell, "IS_WINDOWS", False)
    assert windows_shell.show_in_explorer(tmp_path) is False
