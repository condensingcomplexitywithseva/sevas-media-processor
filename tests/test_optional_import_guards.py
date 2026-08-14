# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import ast
import sys
from functools import lru_cache
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

INTERNAL_MODULES = {p.stem for p in SRC.glob("*.py")} | {
    d.name for d in SRC.iterdir() if d.is_dir()
}

PINNED_GUARDED_LIBRARIES = {
    "av",
    "pypdfium2",
    "pillow_heif",
    "pillow_avif",
    "PIL",
    "openpyxl",
    "webview",
    "werkzeug",
}

TOLERATED_GUARDS = {
    "clr": (
        "pythonnet CLR boot for the native window icon: cosmetic only (the "
        "windows keep their default icon), legitimately absent off-Windows, "
        "and booting the CLR inside pytest workers is not worth a title-bar "
        "icon"
    ),
    "System": (
        "same guard as clr: .NET namespaces are reachable only after "
        "clr.AddReference, so they cannot be import-checked outside that "
        "guard"
    ),
}


def guarded_external_imports(tree):
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not node.handlers:
            continue
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    single = ast.Import(names=[alias])
                    found.setdefault(alias.name.split(".")[0], []).append(single)
            elif isinstance(stmt, ast.ImportFrom):
                if stmt.level:
                    continue
                found.setdefault(stmt.module.split(".")[0], []).append(stmt)
    return {
        name: stmts
        for name, stmts in found.items()
        if name not in sys.stdlib_module_names and name not in INTERNAL_MODULES
    }


@lru_cache(maxsize=1)
def sweep_src():
    result = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, stmts in guarded_external_imports(tree).items():
            result.setdefault(name, {})[path.relative_to(SRC).as_posix()] = stmts
    return result


def test_every_guarded_external_import_is_classified():
    found = set(sweep_src())
    known = PINNED_GUARDED_LIBRARIES | set(TOLERATED_GUARDS)
    where = {
        name: sorted(sites) for name, sites in sweep_src().items()
        if name in found - known
    }
    assert found == known, (
        f"guarded external imports in src/ changed: unclassified {where}, "
        f"gone {sorted(known - found)}. Add a guard over a pinned "
        f"requirements.txt dependency to PINNED_GUARDED_LIBRARIES so its "
        f"imports are executed; add a genuinely tolerable one to "
        f"TOLERATED_GUARDS with the reason swallowing is acceptable."
    )


@pytest.mark.parametrize("library", sorted(PINNED_GUARDED_LIBRARIES))
def test_guarded_imports_of_pinned_library_resolve(library):
    sites = sweep_src().get(library)
    assert sites, (
        f"{library} is classified as pinned-and-guarded but no guard in src/ "
        f"imports it any more - remove it from PINNED_GUARDED_LIBRARIES"
    )
    for relpath, stmts in sorted(sites.items()):
        for stmt in stmts:
            module = ast.Module(body=[stmt], type_ignores=[])
            ast.fix_missing_locations(module)
            spelling = ast.unparse(stmt)
            try:
                exec(compile(module, f"<guard in {relpath}>", "exec"), {})
            except Exception as e:
                pytest.fail(
                    f"'{spelling}' (guarded in {relpath}) does not resolve: "
                    f"{e!r}. The guard would swallow this and silently "
                    f"disable the capability. A 'cannot import name' means "
                    f"the library is installed and merely renamed something "
                    f"- fix the import in {relpath}, do not loosen the guard."
                )


def test_video_pipeline_guard_resolved():
    import pipelines.video as video_module

    reason = getattr(video_module, "av_err", "(no reason recorded)")
    assert video_module.av is not None, (
        f"pipelines/video.py fell back to av=None, so every video will fail "
        f"at runtime. Reason recorded by the guard: {reason}"
    )


def test_document_pipeline_guard_resolved():
    import pipelines.document as document_module

    reason = getattr(document_module, "pdf_err", "(no reason recorded)")
    assert document_module.pdfium is not None, (
        f"pipelines/document.py fell back to pdfium=None, so every PDF will "
        f"fail at runtime. Reason recorded by the guard: {reason}"
    )


def test_image_plugins_actually_registered():
    import to_jpeg_converter as converter
    from PIL import Image

    converter._lazy_load_image_plugins()
    extensions = Image.registered_extensions()
    expected = {".heic": "HEIF", ".heif": "HEIF", ".avif": "AVIF"}
    missing = {
        ext: fmt for ext, fmt in expected.items()
        if extensions.get(ext) != fmt or fmt not in Image.OPEN
    }
    assert not missing, (
        f"_lazy_load_image_plugins() did not register {missing} with Pillow "
        f"(registered: { {e: extensions.get(e) for e in expected} }, "
        f"openable: {sorted(set(expected.values()) & set(Image.OPEN))}). "
        f"Every file of these types will fail as unsupported while the "
        f"plugin wheels sit installed - the guard in to_jpeg_converter.py "
        f"swallowed the real error into a log warning."
    )


def test_the_sweep_sees_the_known_guards():
    swept = sweep_src()
    assert "pipelines/video.py" in swept.get("av", {}), (
        "the sweep no longer finds video.py's module-level av guard - it has "
        "stopped matching the source, so the classification proves nothing"
    )
    assert "to_jpeg_converter.py" in swept.get("pillow_heif", {}), (
        "the sweep no longer finds the function-level pillow_heif guard in "
        "to_jpeg_converter.py - lazy in-function guards are exactly the "
        "members that once escaped, so the sweep has gone blind"
    )


SWEEP_CONTROLS = [
    (
        "function_level_guard",
        "def f():\n"
        "    try:\n"
        "        import extlib\n"
        "    except ImportError:\n"
        "        pass\n",
        {"extlib"},
    ),
    (
        "guard_nested_in_module_level_if",
        "import sys\n"
        "if sys.platform == 'win32':\n"
        "    try:\n"
        "        import extlib\n"
        "    except ImportError:\n"
        "        pass\n",
        {"extlib"},
    ),
    (
        "module_not_found_error_spelling",
        "try:\n"
        "    import extlib\n"
        "except ModuleNotFoundError:\n"
        "    pass\n",
        {"extlib"},
    ),
    (
        "broad_exception_handler",
        "try:\n"
        "    import extlib\n"
        "except Exception:\n"
        "    pass\n",
        {"extlib"},
    ),
    (
        "bare_except",
        "try:\n"
        "    import extlib\n"
        "except:\n"
        "    pass\n",
        {"extlib"},
    ),
    (
        "from_import_of_a_submodule",
        "try:\n"
        "    from extlib.sub.deep import name\n"
        "except ImportError:\n"
        "    pass\n",
        {"extlib"},
    ),
    (
        "unguarded_import_is_not_a_guard",
        "import extlib\n",
        set(),
    ),
    (
        "try_finally_without_handlers_cannot_swallow",
        "try:\n"
        "    import extlib\n"
        "finally:\n"
        "    pass\n",
        set(),
    ),
    (
        "stdlib_import_is_out_of_scope",
        "try:\n"
        "    import json\n"
        "except ImportError:\n"
        "    pass\n",
        set(),
    ),
    (
        "internal_module_is_out_of_scope",
        "try:\n"
        "    import config_loader\n"
        "except ImportError:\n"
        "    pass\n",
        set(),
    ),
    (
        "relative_import_is_out_of_scope",
        "try:\n"
        "    from . import something\n"
        "except ImportError:\n"
        "    pass\n",
        set(),
    ),
]


@pytest.mark.parametrize(
    "source, expected",
    [(source, expected) for _, source, expected in SWEEP_CONTROLS],
    ids=[case_id for case_id, _, _ in SWEEP_CONTROLS],
)
def test_sweep_controls(source, expected):
    assert set(guarded_external_imports(ast.parse(source))) == expected
