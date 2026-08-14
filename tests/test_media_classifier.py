# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import media_classifier
from media_classifier import MediaClassifier
from pipelines.base_pipeline import BaseMediaPipeline
from schemas import FileSummary, PageResult, Status

PIPELINE_ATTRS = ("VideoPipeline", "AnimationPipeline", "DocumentPipeline", "StaticImagePipeline")

EXPECTED_ROUTING = {
    ".mp4": "VideoPipeline", ".mov": "VideoPipeline", ".avi": "VideoPipeline",
    ".mkv": "VideoPipeline", ".wmv": "VideoPipeline", ".webm": "VideoPipeline",
    ".gif": "AnimationPipeline", ".webp": "AnimationPipeline",
    ".pdf": "DocumentPipeline",
    ".jpeg": "StaticImagePipeline", ".jpg": "StaticImagePipeline",
    ".jpe": "StaticImagePipeline", ".jfif": "StaticImagePipeline",
    ".png": "StaticImagePipeline", ".bmp": "StaticImagePipeline",
    ".dib": "StaticImagePipeline", ".tif": "StaticImagePipeline",
    ".tiff": "StaticImagePipeline", ".heic": "StaticImagePipeline",
    ".heif": "StaticImagePipeline", ".avif": "StaticImagePipeline",
    ".JPG": "StaticImagePipeline", ".MP4": "VideoPipeline",
}


def make_classifier():
    return MediaClassifier(
        SimpleNamespace(), None, Path("out"), None, None, None, None
    )


def drain(generator):
    results = []
    try:
        while True:
            results.append(next(generator))
    except StopIteration as stop:
        return results, stop.value


@pytest.fixture
def recorded_pipelines(monkeypatch):
    created = []

    def make_fake(name):
        class FakePipeline:
            def __init__(self, settings, file_id, *args, **kwargs):
                created.append((name, file_id))

            def process(self):
                def gen():
                    yield PageResult(1, "f.jpg", Status.OK.value, "")
                    return FileSummary(1, "1", "ok", Status.OK.value, "done")

                return gen()

        return FakePipeline

    for attr in PIPELINE_ATTRS:
        monkeypatch.setattr(media_classifier, attr, make_fake(attr))
    return created


@pytest.fixture
def forbidden_pipelines(monkeypatch):
    class NeverBuilt:
        def __init__(self, *args, **kwargs):
            raise AssertionError("no pipeline may be constructed for this file")

    for attr in PIPELINE_ATTRS:
        monkeypatch.setattr(media_classifier, attr, NeverBuilt)



def test_the_routing_table_covers_every_extension_the_app_accepts():
    accepted = (
        set(media_classifier.VIDEO_EXTENSIONS)
        | set(media_classifier.ANIMATED_IMAGE_EXTENSIONS)
        | set(media_classifier.PDF_EXTENSIONS)
        | set(media_classifier.IMAGE_EXTENSIONS)
    )
    tabled = {extension.lower() for extension in EXPECTED_ROUTING}

    assert tabled - accepted == set(), (
        "the routing table promises extensions media_classifier no longer accepts"
    )
    assert accepted - tabled == set(), (
        "media_classifier accepts extensions the routing table never checks - "
        "add them to EXPECTED_ROUTING, and to the GUI tab and README lists "
        "(tests/test_supported_extensions_sync.py)"
    )


@pytest.mark.parametrize("extension,expected_pipeline", sorted(EXPECTED_ROUTING.items()))
def test_every_supported_extension_reaches_its_pipeline(
    tmp_path, recorded_pipelines, extension, expected_pipeline
):
    media_file = tmp_path / f"sample{extension}"
    media_file.write_bytes(b"not really media, but not empty either")

    rel, ext, pipeline_name, generator, orphaned = make_classifier().evaluate_and_route(
        5, media_file, tmp_path
    )

    assert pipeline_name == expected_pipeline
    assert recorded_pipelines == [(expected_pipeline, 5)]
    assert ext == extension.lower()
    assert rel == media_file.name
    assert orphaned is False



@pytest.mark.parametrize("filename", ["notes.txt", "report.docx"])
def test_unsupported_extension_is_rejected_through_the_normal_pathway(
    tmp_path, forbidden_pipelines, filename
):
    unsupported = tmp_path / filename
    unsupported.write_bytes(b"some content")

    rel, ext, pipeline_name, generator, orphaned = make_classifier().evaluate_and_route(
        1, unsupported, tmp_path
    )
    results, summary = drain(generator)

    assert pipeline_name == "Rejected"
    assert len(results) == 1
    assert results[0].success == Status.FAILURE.value
    assert "Unsupported file extension" in results[0].comment
    assert summary.final_aggregate_status == Status.FAILURE.value



def test_empty_file_is_rejected_before_any_engine_starts(tmp_path, forbidden_pipelines):
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")

    rel, ext, pipeline_name, generator, orphaned = make_classifier().evaluate_and_route(
        1, empty, tmp_path
    )
    results, summary = drain(generator)

    assert pipeline_name == "Rejected"
    assert "File is completely empty (0 bytes)." in results[0].comment
    assert summary.final_aggregate_status == Status.FAILURE.value



def test_files_outside_the_input_root_get_unique_orphan_names(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    outside_a = tmp_path / "elsewhere" / "photo.jpg"
    outside_b = tmp_path / "somewhere_else" / "photo.jpg"

    name_a, orphaned_a = MediaClassifier.relative_or_orphan(outside_a, root)
    name_b, orphaned_b = MediaClassifier.relative_or_orphan(outside_b, root)

    assert orphaned_a and orphaned_b
    assert name_a != name_b
    assert name_a.endswith("photo.jpg") and name_b.endswith("photo.jpg")

    inside = root / "sub" / "photo.jpg"
    name_inside, orphaned_inside = MediaClassifier.relative_or_orphan(inside, root)
    assert orphaned_inside is False
    assert name_inside == str(Path("sub") / "photo.jpg")



def test_pipeline_construction_crash_degrades_to_a_rejection(tmp_path, monkeypatch):
    class ExplodingPipeline:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("engine exploded during init")

    monkeypatch.setattr(media_classifier, "StaticImagePipeline", ExplodingPipeline)
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"content")

    rel, ext, pipeline_name, generator, orphaned = make_classifier().evaluate_and_route(
        1, photo, tmp_path
    )
    results, summary = drain(generator)

    assert pipeline_name == "Rejected"
    assert results[0].success == Status.FAILURE.value
    assert "Internal routing crash" in results[0].comment
    assert "engine exploded during init" in results[0].comment
    assert summary.final_aggregate_status == Status.FAILURE.value



class _ProbePipeline(BaseMediaPipeline):
    def process(self):
        yield from ()


def _naming_probe(relative_path="in.png", **setting_overrides):
    values = dict(OUTPUT_FILENAME_PREFIX_LENGTH=20, OUTPUT_FILENAME_TIMESTAMPS=True)
    values.update(setting_overrides)
    return _ProbePipeline(SimpleNamespace(**values), 42, Path(relative_path),
                          relative_path, Path(relative_path).suffix, Path("out"))


def test_output_naming_contract_new_scheme():
    probe = _naming_probe("How To Mod Stronghold 4 Graphics & Style.mp4")
    assert probe.get_filename(7) == "42_How To Mod Stronghol_page_7.jpg"
    assert probe.get_filename(5, capture_seconds=900.0) == (
        "42_How To Mod Stronghol_page_5_t00_15_00_00.jpg")
    silent = _naming_probe("clip.mp4", OUTPUT_FILENAME_TIMESTAMPS=False)
    assert silent.get_filename(5, capture_seconds=900.0) == "42_clip_page_5.jpg"
    hopeless = _naming_probe("***.png")
    assert hopeless.get_filename(1) == "42_page_1.jpg"
    assert probe.get_output_path("42_page_7.jpg") == Path("out") / "42_page_7.jpg"


def test_output_naming_contract_multi_day_capture_times():
    probe = _naming_probe("warehouse cctv cam03.mp4")
    two_days_in = 48 * 3600 + 20 * 60 + 15.07
    assert probe.get_filename(9001, capture_seconds=two_days_in) == (
        "42_warehouse cctv cam03_page_9001_t48_20_15_07.jpg")
    past_widen_point = 100 * 3600 + 12 * 60 + 34.37
    assert probe.get_filename(2, capture_seconds=past_widen_point) == (
        "42_warehouse cctv cam03_page_2_t100_12_34_37.jpg")


def test_output_naming_contract_legacy_pin():
    probe = _naming_probe("in.png", OUTPUT_FILENAME_PREFIX_LENGTH=0,
                          OUTPUT_FILENAME_TIMESTAMPS=False)
    assert probe.get_filename(7) == "42_page_7.jpg"
    video = _naming_probe("clip.mp4", OUTPUT_FILENAME_PREFIX_LENGTH=0,
                          OUTPUT_FILENAME_TIMESTAMPS=False)
    assert video.get_filename(3, capture_seconds=93.37) == "42_page_3.jpg"


def test_frames_saved_by_a_real_run_are_found_by_the_ai_lookup(tmp_path, monkeypatch):
    import central_logger
    from app_context import ProcessorCore
    from config_validator import Settings
    from db_controller import SQLiteDatabaseController

    from PIL import Image as PIL

    monkeypatch.setattr(central_logger, "setup_logging", lambda *a, **k: None)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    PIL.new("RGB", (40, 30), (200, 10, 10)).save(input_dir / "photo.png")

    settings = Settings(
        INPUT_FOLDER_PATH=str(input_dir),
        OUTPUT_FOLDER_PATH=str(tmp_path / "output"),
        ENABLE_LLM_INFERENCE=False,
    )
    ProcessorCore(settings, threading.Event(), on_progress=lambda e: None).run()

    db = SQLiteDatabaseController(settings.TECH_FOLDER_PATH / "application_state.db")
    try:
        frame_paths = db.get_successful_frame_paths(1, settings.CURRENT_RUN_FOLDER)
    finally:
        db.close()

    assert frame_paths, "the run saved no frames"
    assert [p.name for p in frame_paths] == ["1_photo_page_1.jpg"]
    assert all(p.exists() for p in frame_paths)
