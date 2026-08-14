# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import ast
import base64
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fs_utils import get_safe_path
import windows_shell

on_windows = pytest.mark.skipif(
    not windows_shell.IS_WINDOWS, reason="long-path spelling is a Windows concern"
)



_FS_METHODS = {
    "exists", "is_file", "is_dir", "stat", "mkdir", "unlink", "rename",
    "rglob", "glob", "iterdir", "read_text", "read_bytes", "write_text",
    "write_bytes", "touch", "open", "save", "connect", "PdfDocument",
}
_OS_FUNCS = {"replace", "makedirs", "remove", "rename", "listdir", "walk",
             "stat", "startfile"}


_EXEMPT_FILES = {
    "fs_utils.py",
    "config_loader.py",
    "central_logger.py",
    "routes\\web_server.py",
}


def _normalize(segment: str) -> str:
    return " ".join(segment.split())[:100]


def _fs_call_sites(text: str):
    tree = ast.parse(text)
    lines = text.splitlines()
    sites = []

    safe_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if "get_safe_path" in (ast.get_source_segment(text, value) or ""):
            safe_names.update(
                t.id for t in targets if isinstance(t, ast.Name)
            )
    safe_marker = re.compile(
        "|".join(["get_safe_path"] + [rf"\b{re.escape(n)}\b" for n in sorted(safe_names)])
    )

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stmt_stack = []

        def visit(self, node):
            is_stmt = isinstance(node, ast.stmt)
            if is_stmt:
                self.stmt_stack.append(node)
            try:
                super().visit(node)
            finally:
                if is_stmt:
                    self.stmt_stack.pop()

        def visit_Call(self, node):
            name = None
            func = node.func
            if isinstance(func, ast.Attribute):
                if (
                    isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                    and func.attr in _OS_FUNCS
                ):
                    name = f"os.{func.attr}"
                elif func.attr in _FS_METHODS:
                    name = func.attr
            elif isinstance(func, ast.Name) and func.id in ("open", "PdfDocument"):
                name = func.id
            if name and self.stmt_stack:
                stmt = self.stmt_stack[-1]
                start = stmt.lineno
                end = max(node.end_lineno or start, start)
                segment = "\n".join(lines[start - 1:end])
                sites.append((_normalize(segment), bool(safe_marker.search(segment))))
            self.generic_visit(node)

    Visitor().visit(tree)
    return sites


_ALLOWED_PLAIN = {
    "main.py": {
        'if not (locales_dir / target_json).exists():',
        'with open(locales_dir / target_json, "r", encoding="utf-8") as f:',
        'if settings_file.exists():',
        'with open(settings_file, "r", encoding="utf-8") as sf:',
        'return "data:image/png;base64," + base64.b64encode(icon_png.read_bytes()).decode("ascii")',
        'panic_log_path = ( desktop_path if desktop_path.parent.exists()',
    },
    "routes\\export_api.py": {
        'if not logs_dir.exists():',
        'if logs_dir.exists():',
        'txt_files = sorted(logs_dir.glob("system_log_*.txt"), key=lambda p: p.stat().st_mtime)',
        'for txt_file in logs_dir.glob("system_log_*.txt*"):',
        'if txt_file.is_file():',
        'txt_file.unlink()',
    },
    "routes\\settings_api.py": {
        'if not file_path.exists():',
        'with open(file_path, "r", encoding="utf-8") as f:',
        'if not target_path.exists():',
        'if active_path.exists():',
        'os.replace(active_path, backup_path)',
        'backups = list(active_path.parent.glob("settings_corrupted_backup_*.json"))',
    },
    "routes\\about_api.py": {
        'webbrowser.open(url)',
    },
    "to_jpeg_converter.py": {
        'current.save( buffer, "JPEG", quality=mid, subsampling=0 )',
        'current.save( buffer, "JPEG", quality=self.lowest_quality_limit, subsampling=0 )',
        'return Image.open( path, formats=[f for f in SUPPORTED_OPEN_FORMATS if f in Image.OPEN] )',
    },
}


def test_positive_control_the_sweep_sees_calls():
    flagged = _fs_call_sites("import os\nPath(p).exists()\nopen(p)\nos.replace(a, b)\n")
    assert len([s for s, safe in flagged if not safe]) == 3, (
        "the sweep no longer flags plain calls"
    )
    safe_only = _fs_call_sites(
        "open(get_safe_path(p))\nsafe_x = Path(get_safe_path(p))\nsafe_x.mkdir()\n"
    )
    assert safe_only and all(safe for _, safe in safe_only), (
        "the sweep no longer honours the safe spelling or safe-name inference"
    )
    unearned_name = _fs_call_sites("y = p\nopen(y)\n")
    assert not all(safe for _, safe in unearned_name), (
        "a name never assigned from get_safe_path must not bless a call"
    )


def test_every_fs_call_in_src_uses_the_long_path_spelling_or_is_recorded():
    found_plain = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = str(path.relative_to(SRC))
        if rel in _EXEMPT_FILES:
            continue
        for segment, safe in _fs_call_sites(path.read_text(encoding="utf-8")):
            if not safe:
                found_plain.setdefault(rel, set()).add(segment)

    allowed = {file: set(entries) for file, entries in _ALLOWED_PLAIN.items()}
    new_offenders = {
        file: entries - allowed.get(file, set())
        for file, entries in found_plain.items()
        if entries - allowed.get(file, set())
    }
    stale = {
        file: entries - found_plain.get(file, set())
        for file, entries in allowed.items()
        if entries - found_plain.get(file, set())
    }
    assert not new_offenders, (
        "filesystem call without the long-path spelling and not recorded as "
        f"deliberately plain: {new_offenders}"
    )
    assert not stale, f"stale allowlist entries (the code moved on): {stale}"



def _deep_dir(base: Path, minimum_length: int = 300) -> Path:
    deep = base
    while len(str(deep)) < minimum_length:
        deep = deep / ("x" * 40)
    Path(get_safe_path(deep)).mkdir(parents=True, exist_ok=True)
    return deep


@on_windows
def test_a_jpeg_written_past_260_chars_reaches_the_llm_encoder(tmp_path):
    from PIL import Image
    from schemas import Status
    from to_jpeg_converter import ToJpegConverter
    from llm_client import LLMClient

    deep = _deep_dir(tmp_path)
    output_path = deep / "page_001.jpeg"
    converter = ToJpegConverter(85, 2560, 0, 0, (255, 255, 255))

    status, _comment = converter.process_image(Image.new("RGB", (8, 8), "red"), output_path)
    assert status == Status.OK.value

    encoded = LLMClient._encode_image_to_base64(object(), output_path)
    assert base64.b64decode(encoded)[:2] == b"\xff\xd8", "not the JPEG the converter wrote"


@on_windows
def test_discovery_walks_past_260_chars_and_reports_the_plain_spelling(tmp_path):
    from batch_orchestrator import BatchOrchestrator

    deep = _deep_dir(tmp_path)
    deep_file = deep / "buried.png"
    Path(get_safe_path(deep_file)).write_bytes(b"png bytes")
    shallow_file = tmp_path / "top.png"
    shallow_file.write_bytes(b"png bytes")

    orchestrator = BatchOrchestrator(
        SimpleNamespace(INPUT_FOLDER_PATH=tmp_path), None, None, None, None
    )
    discovered = orchestrator._discover_files()

    assert deep_file in discovered, "the walk lost the deep file"
    assert shallow_file in discovered
    assert all("\\\\?\\" not in str(p) for p in discovered), (
        "machine spelling leaked into discovery results - it would reach the "
        "database and the exports"
    )
    assert all(str(p).startswith(str(tmp_path)) for p in discovered), (
        "results must stay rooted at the folder's own stored spelling"
    )


@on_windows
def test_the_run_database_opens_past_260_chars(tmp_path):
    from db_controller import SQLiteDatabaseController
    from data_exporter import SQLiteDataExporter

    deep = _deep_dir(tmp_path)
    database_path = deep / "application_state.db"

    controller = SQLiteDatabaseController(database_path)
    try:
        assert controller.get_highest_file_id() == 0
    finally:
        controller.close()
    assert Path(get_safe_path(database_path)).exists()

    exporter = SQLiteDataExporter(database_path)
    try:
        exporter.export_csv(deep / "exports", timestamp="pinned")
    finally:
        exporter.close()
    exports = list(Path(get_safe_path(deep / "exports")).glob("*.csv"))
    assert len(exports) == 2, "both CSV reports must land next to the deep database"


@on_windows
def test_both_database_engines_hand_sqlite_the_safe_spelling(tmp_path, monkeypatch):
    import sqlite3
    import sqlite3.dbapi2

    from db_controller import SQLiteDatabaseController
    from data_exporter import SQLiteDataExporter

    captured = []
    real_connect = sqlite3.dbapi2.connect

    def capturing_connect(database, *args, **kwargs):
        captured.append(str(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", capturing_connect)
    monkeypatch.setattr(sqlite3.dbapi2, "connect", capturing_connect)

    database_path = tmp_path / "application_state.db"
    controller = SQLiteDatabaseController(database_path)
    controller.close()
    exporter = SQLiteDataExporter(database_path)
    exporter.export_csv(tmp_path / "exports", timestamp="pinned")
    exporter.close()

    assert captured, "no connection was made - the capture went blind"
    plain = [c for c in captured if not c.startswith("\\\\?\\")]
    assert not plain, f"sqlite opened without the long-path spelling: {plain}"



@on_windows
def test_the_shell_rename_refuses_overlong_paths_readably(tmp_path):
    source = tmp_path / "current_run"
    source.mkdir()
    target = tmp_path / ("y" * 300)

    with pytest.raises(OSError) as raised:
        windows_shell.rename_folder_like_explorer(source, target)

    assert raised.value.winerror == 0x7C
    assert "too long" in raised.value.strerror
    assert raised.value.filename == str(source)
    assert raised.value.filename2 == str(target)
    assert source.exists(), "a refusal must leave the source untouched"


def test_archive_error_lines_shed_the_machine_spelling():
    from app_context import _format_os_error

    error = OSError(0, "Access is denied", "\\\\?\\C:\\out\\current_run", 5,
                    "\\\\?\\C:\\out\\old_current_run")
    line = _format_os_error(error)
    assert "C:\\out\\current_run -> C:\\out\\old_current_run" in line
    assert "\\\\?\\" not in line
