# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import struct
import sys
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np
from PIL import Image

import av
import pypdfium2 as pdfium

import pipelines.document as document_module
import pipelines.video as video_module
from pipelines.animation import AnimationPipeline
from pipelines.document import DocumentPipeline
from pipelines.static_image import StaticImagePipeline
from pipelines.video import VideoPipeline
from range_parsers import PageRangeSelector, VideoSelector
from schemas import RangeStatus, Status
from to_jpeg_converter import ToJpegConverter

MAX_DIMENSION = 1000


def make_converter():
    return ToJpegConverter(90, MAX_DIMENSION, 1024, 30, (255, 255, 255))


def make_settings(**overrides):
    values = dict(
        MAX_DIMENSION=MAX_DIMENSION,
        OUTPUT_FILENAME_PREFIX_LENGTH=20,
        OUTPUT_FILENAME_TIMESTAMPS=True,
        DOCUMENT_MAX_PAGES=1000,
        PDF_SCALE=2,
        ANIMATION_TARGET_TOTAL_FRAMES=10,
        ANIMATION_SCENE_SENSITIVITY=5.0,
        VIDEO_MODE="SUMMARY",
        VIDEO_SUMMARY_TARGET_TOTAL_FRAMES=3,
        VIDEO_SUMMARY_SCENE_SENSITIVITY=0.0,
        VIDEO_SAMPLING_CAPTURE_RATE_FPS=2.0,
        VIDEO_SAMPLING_MAX_FRAMES_BUDGET=100,
        VIDEO_SAMPLING_SCENE_SENSITIVITY=0.0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def drain(generator):
    results = []
    try:
        while True:
            results.append(next(generator))
    except StopIteration as stop:
        return results, stop.value


PIPELINE_CLASSES = {
    "static": (StaticImagePipeline, PageRangeSelector),
    "document": (DocumentPipeline, PageRangeSelector),
    "animation": (AnimationPipeline, PageRangeSelector),
    "video": (VideoPipeline, VideoSelector),
}


def run_pipeline(kind, input_path, output_folder, range_string="", **setting_overrides):
    output_folder.mkdir(exist_ok=True)
    pipeline_class, selector_class = PIPELINE_CLASSES[kind]
    pipeline = pipeline_class(
        make_settings(**setting_overrides),
        1,
        input_path,
        input_path.name,
        input_path.suffix,
        output_folder,
        make_converter(),
        selector_class(range_string),
    )
    return drain(pipeline.process())



def make_png(path):
    Image.new("RGB", (80, 60), (200, 30, 30)).save(path)
    return path


def make_tiff(path, pages=3):
    frames = [Image.new("RGB", (80, 60), (80 * i % 256, 120, 60)) for i in range(pages)]
    frames[0].save(path, save_all=True, append_images=frames[1:])
    return path


def make_gif(path):
    static = []
    for i in range(5):
        pixels = np.full((48, 64, 3), (30, 60, 90), dtype=np.uint8)
        pixels[0, i] = (31, 60, 90)
        static.append(Image.fromarray(pixels))
    distinct_colors = [(250, 0, 0), (0, 250, 0), (0, 0, 250), (250, 250, 0), (0, 250, 250)]
    moving = [Image.new("RGB", (64, 48), color) for color in distinct_colors]
    frames = static + moving
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100)
    with Image.open(path) as saved:
        assert saved.n_frames == 10
    return path


def make_pdf(path, pages=5, size_pt=(300, 400)):
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(*size_pt)
    pdf.save(str(path))
    return path


def make_video(path, total_frames=20, fps=10):
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height = 64, 64
        stream.pix_fmt = "yuv420p"
        for i in range(total_frames):
            pixels = np.zeros((64, 64, 3), dtype=np.uint8)
            pixels[:, :] = ((i * 12) % 256, (255 - i * 12) % 256, 40)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path



def test_plain_image_yields_one_frame_regardless_of_configured_range(tmp_path):
    png = make_png(tmp_path / "photo.png")

    results, summary = run_pipeline("static", png, tmp_path / "out", range_string="5-10")

    assert [(r.page_number, r.success) for r in results] == [(1, Status.OK.value)]
    assert summary.final_aggregate_status == Status.OK.value
    assert summary.applied_range_string == "1"
    assert (tmp_path / "out" / "1_photo_page_1.jpg").exists()



def test_multipage_tiff_honours_the_image_range(tmp_path):
    tiff = make_tiff(tmp_path / "scan.tif", pages=3)

    results, summary = run_pipeline("static", tiff, tmp_path / "out", range_string="2")

    assert [(r.page_number, r.success) for r in results] == [(2, Status.OK.value)]
    assert summary.total_discovered_pages == 3
    assert summary.final_aggregate_status == Status.OK.value
    saved = sorted(p.name for p in (tmp_path / "out").glob("*.jpg"))
    assert saved == ["1_scan_page_2.jpg"]



def test_pdf_page_cap_truncates_and_reports_truncated(tmp_path):
    pdf = make_pdf(tmp_path / "report.pdf", pages=5)

    results, summary = run_pipeline(
        "document", pdf, tmp_path / "out", DOCUMENT_MAX_PAGES=2
    )

    assert [(r.page_number, r.success) for r in results] == [
        (1, Status.OK.value), (2, Status.OK.value),
    ]
    assert summary.range_status_code == RangeStatus.TRUNCATED.value
    assert summary.total_discovered_pages == 5


def test_giant_pdf_page_never_produces_output_beyond_max_dimension(tmp_path):
    pdf = make_pdf(tmp_path / "poster.pdf", pages=1, size_pt=(3000, 3000))

    results, summary = run_pipeline("document", pdf, tmp_path / "out", PDF_SCALE=4)

    assert summary.final_aggregate_status == Status.OK.value
    with Image.open(tmp_path / "out" / results[0].output_filename) as rendered:
        assert max(rendered.size) <= MAX_DIMENSION



def make_rotated_pdf(path, rotate):
    import io

    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(300, 400)

    pixels = np.full((400, 300, 3), 30, dtype=np.uint8)
    pixels[0:100, 0:100] = 255
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="JPEG", quality=90)
    buffer.seek(0)

    image = pdfium.PdfImage.new(pdf)
    image.load_jpeg(buffer, autoclose=False)
    image.set_matrix(pdfium.PdfMatrix(300, 0, 0, 400, 0, 0))
    page.insert_obj(image)
    page.gen_content()
    page.set_rotation(rotate)
    pdf.save(str(path))
    pdf.close()
    return path


@pytest.mark.parametrize("rotate,expected_size,marker_lands", [
    (0, (600, 800), "top-left"),
    (90, (800, 600), "top-right"),
    (180, (600, 800), "bottom-right"),
    (270, (800, 600), "bottom-left"),
])
def test_pdf_rotate_flag_is_honored_in_the_render(
        tmp_path, rotate, expected_size, marker_lands):
    pdf = make_rotated_pdf(tmp_path / "scan.pdf", rotate)

    results, summary = run_pipeline("document", pdf, tmp_path / "out")

    assert summary.final_aggregate_status == Status.OK.value
    assert [(r.page_number, r.success) for r in results] == [(1, Status.OK.value)]
    with Image.open(tmp_path / "out" / results[0].output_filename) as rendered:
        assert rendered.size == expected_size
        assert _brightest_quadrant(rendered) == marker_lands



STILL_FORMATS = {
    "jpeg": (".jpg", "JPEG"),
    "tiff": (".tif", "TIFF"),
    "heic": (".heic", "HEIF"),
}

ORIENTATION_CASES = [
    (2, (64, 48), "top-right"),
    (3, (64, 48), "bottom-right"),
    (4, (64, 48), "bottom-left"),
    (5, (48, 64), "top-left"),
    (6, (48, 64), "top-right"),
    (7, (48, 64), "bottom-right"),
    (8, (48, 64), "bottom-left"),
]


def make_marker_pixels():
    pixels = np.full((48, 64, 3), 30, dtype=np.uint8)
    pixels[0:16, 0:16] = 255
    return pixels


def make_oriented_still(path, fmt, orientation=None):
    if fmt == "heic":
        import pillow_heif
        pillow_heif.register_heif_opener()
    save_format = STILL_FORMATS[fmt][1]
    image = Image.fromarray(make_marker_pixels())
    if orientation is None:
        image.save(path, save_format)
    else:
        exif = image.getexif()
        exif[274] = orientation
        image.save(path, save_format, exif=exif.tobytes())
    return path


@pytest.mark.parametrize("fmt", list(STILL_FORMATS), ids=list(STILL_FORMATS))
@pytest.mark.parametrize("orientation,expected_size,marker_lands", ORIENTATION_CASES)
def test_exif_orientation_direction_is_baked_into_the_pixels(
        tmp_path, fmt, orientation, expected_size, marker_lands):
    still = make_oriented_still(
        tmp_path / f"oriented{STILL_FORMATS[fmt][0]}", fmt, orientation)

    results, summary = run_pipeline("static", still, tmp_path / "out")

    assert summary.final_aggregate_status == Status.OK.value
    assert [(r.page_number, r.success) for r in results] == [(1, Status.OK.value)]
    with Image.open(tmp_path / "out" / results[0].output_filename) as saved:
        assert saved.size == expected_size
        assert _brightest_quadrant(saved) == marker_lands


@pytest.mark.parametrize("fmt", list(STILL_FORMATS), ids=list(STILL_FORMATS))
def test_image_without_orientation_tag_stays_untouched(tmp_path, fmt):
    still = make_oriented_still(
        tmp_path / f"plain{STILL_FORMATS[fmt][0]}", fmt, orientation=None)

    results, summary = run_pipeline("static", still, tmp_path / "out")

    assert summary.final_aggregate_status == Status.OK.value
    assert "outside the standard 1-8 range" not in results[0].comment
    with Image.open(tmp_path / "out" / results[0].output_filename) as saved:
        assert saved.size == (64, 48)
        assert _brightest_quadrant(saved) == "top-left"


INVALID_ORIENTATIONS = [0, 9, 65535]


@pytest.mark.parametrize("orientation", INVALID_ORIENTATIONS)
def test_invalid_exif_orientation_warns_and_keeps_image_as_stored(
        tmp_path, orientation):
    still = make_oriented_still(
        tmp_path / "invalid.jpg", "jpeg", orientation)

    results, summary = run_pipeline("static", still, tmp_path / "out")

    assert summary.final_aggregate_status == Status.OK.value
    assert [(r.page_number, r.success) for r in results] == [(1, Status.OK.value)]
    assert (f"EXIF orientation {orientation} is outside the standard 1-8 range"
            in results[0].comment)
    with Image.open(tmp_path / "out" / results[0].output_filename) as saved:
        assert saved.size == (64, 48)
        assert _brightest_quadrant(saved) == "top-left"
        assert dict(saved.getexif()) == {}



def make_oriented_animated_webp(path):
    frames = []
    for i in range(2):
        pixels = make_marker_pixels()
        pixels[40, 40 + i] = 90
        frames.append(Image.fromarray(pixels))
    exif = frames[0].getexif()
    exif[274] = 6
    frames[0].save(path, "WEBP", save_all=True, append_images=frames[1:],
                   duration=100, lossless=True, exif=exif.tobytes())
    with Image.open(path) as check:
        assert getattr(check, "n_frames", 1) == 2
    return path


def test_animated_webp_exif_orientation_reaches_every_frame(tmp_path):
    webp = make_oriented_animated_webp(tmp_path / "anim.webp")

    results, summary = run_pipeline(
        "animation", webp, tmp_path / "out", ANIMATION_SCENE_SENSITIVITY=0.0)

    assert summary.final_aggregate_status == Status.OK.value
    assert [r.success for r in results] == [Status.OK.value] * 2
    for result in results:
        with Image.open(tmp_path / "out" / result.output_filename) as saved:
            assert saved.size == (48, 64)
            assert _brightest_quadrant(saved) == "top-right"



def make_mpo(path):
    photo = Image.new("RGB", (80, 60), (200, 30, 30))
    gain_map = Image.new("L", (40, 30), 128)
    photo.save(path, format="MPO", save_all=True, append_images=[gain_map])
    with Image.open(path) as check:
        assert check.format == "MPO" and check.n_frames == 2
    return path


def test_mpo_yields_exactly_one_output_the_photo_not_the_gain_map(tmp_path):
    mpo = make_mpo(tmp_path / "photo.jpg")

    results, summary = run_pipeline("static", mpo, tmp_path / "out")

    assert [(r.page_number, r.success) for r in results] == [(1, Status.OK.value)]
    assert summary.total_discovered_pages == 1
    assert summary.final_aggregate_status == Status.OK.value
    assert "MPO auxiliary image ignored" in results[0].comment
    saved_names = sorted(p.name for p in (tmp_path / "out").glob("*.jpg"))
    assert saved_names == ["1_photo_page_1.jpg"]
    with Image.open(tmp_path / "out" / "1_photo_page_1.jpg") as saved:
        assert saved.size == (80, 60)
        red, green, _ = saved.resize((1, 1)).getpixel((0, 0))
        assert red > 150 and green < 100



def test_missing_pdf_engine_degrades_to_a_per_file_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(document_module, "pdfium", None)
    monkeypatch.setattr(document_module, "pdf_err", "pypdfium2 disabled for test", raising=False)
    pdf = make_pdf(tmp_path / "report.pdf", pages=1)

    results, summary = run_pipeline("document", pdf, tmp_path / "out")

    assert summary.final_aggregate_status == Status.FAILURE.value
    assert "Library failure" in summary.final_aggregate_comment
    assert len(results) == 1


def test_missing_video_engine_degrades_to_a_per_file_failure(tmp_path, monkeypatch):
    video = make_video(tmp_path / "clip.mp4")
    monkeypatch.setattr(video_module, "av", None)
    monkeypatch.setattr(video_module, "av_err", "PyAV disabled for test", raising=False)

    results, summary = run_pipeline("video", video, tmp_path / "out")

    assert summary.final_aggregate_status == Status.FAILURE.value
    assert "PyAV Missing" in summary.final_aggregate_comment
    assert len(results) == 1



def test_animation_budget_compresses_and_static_scenes_are_skipped(tmp_path):
    gif = make_gif(tmp_path / "anim.gif")

    results, summary = run_pipeline(
        "animation", gif, tmp_path / "out", ANIMATION_TARGET_TOTAL_FRAMES=4
    )

    statuses = [r.success for r in results]
    assert len(statuses) == 4
    assert statuses.count(Status.SKIPPED.value) == 1
    assert statuses.count(Status.OK.value) == 3
    assert "Scene static" in [r.comment for r in results if r.success == Status.SKIPPED.value][0]

    assert summary.final_aggregate_status == Status.OK.value
    assert "Compressed:" in summary.final_aggregate_comment
    assert summary.range_status_code == RangeStatus.TRUNCATED.value



def test_video_summary_mode_extracts_the_configured_total(tmp_path):
    video = make_video(tmp_path / "clip.mp4")

    results, summary = run_pipeline(
        "video", video, tmp_path / "out",
        VIDEO_MODE="SUMMARY", VIDEO_SUMMARY_TARGET_TOTAL_FRAMES=3,
    )

    assert len(results) == 3
    assert summary.final_aggregate_status == Status.OK.value
    assert all(r.success == Status.OK.value for r in results)


def test_video_sampling_mode_follows_the_capture_rate_not_the_summary_target(tmp_path):
    video = make_video(tmp_path / "clip.mp4")

    results, summary = run_pipeline(
        "video", video, tmp_path / "out",
        VIDEO_MODE="SAMPLING",
        VIDEO_SAMPLING_CAPTURE_RATE_FPS=2.0,
        VIDEO_SUMMARY_TARGET_TOTAL_FRAMES=3,
    )

    assert 4 <= len(results) <= 5
    saved_ok = [r for r in results if r.success == Status.OK.value]
    assert len(saved_ok) >= 4



_FIXED_ONE = 65536

_TKHD_ROTATION_MATRICES = {
    -90: (0, _FIXED_ONE, 0, -_FIXED_ONE, 0, 0, 0, 0, 1 << 30),
    90: (0, -_FIXED_ONE, 0, _FIXED_ONE, 0, 0, 0, 0, 1 << 30),
    180: (-_FIXED_ONE, 0, 0, 0, -_FIXED_ONE, 0, 0, 0, 1 << 30),
}


def _iter_mp4_boxes(data, start, end):
    pos = start
    while pos + 8 <= end:
        size, box_type = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        yield pos, header, size, box_type
        pos += size


def _patch_tkhd_matrix(path, matrix):
    data = bytearray(path.read_bytes())
    patched = 0
    for pos, header, size, box_type in _iter_mp4_boxes(data, 0, len(data)):
        if box_type != b"moov":
            continue
        for tpos, theader, tsize, ttype in _iter_mp4_boxes(
                data, pos + header, pos + size):
            if ttype != b"trak":
                continue
            for kpos, kheader, _ksize, ktype in _iter_mp4_boxes(
                    data, tpos + theader, tpos + tsize):
                if ktype != b"tkhd":
                    continue
                payload = kpos + kheader
                version = data[payload]
                struct.pack_into(">9i", data, payload + (52 if version else 40),
                                 *matrix)
                patched += 1
    path.write_bytes(data)
    return patched


def make_rotated_video(path, rotation=None, total_frames=10, fps=10):
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height = 64, 48
        stream.pix_fmt = "yuv420p"
        for _ in range(total_frames):
            pixels = np.full((48, 64, 3), 30, dtype=np.uint8)
            pixels[0:16, 0:16] = 255
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    if rotation is not None:
        assert _patch_tkhd_matrix(path, _TKHD_ROTATION_MATRICES[rotation]) == 1
        with av.open(str(path)) as container:
            first = next(container.decode(container.streams.video[0]))
            assert (getattr(first, "rotation", 0) or 0) % 360 == rotation % 360
    return path


def _brightest_quadrant(image):
    grey = image.convert("L")
    width, height = grey.size
    quadrants = {
        "top-left": (0, 0, width // 2, height // 2),
        "top-right": (width // 2, 0, width, height // 2),
        "bottom-left": (0, height // 2, width // 2, height),
        "bottom-right": (width // 2, height // 2, width, height),
    }
    def mean_of(box):
        region = np.asarray(grey.crop(box), dtype=np.float32)
        return float(region.mean())
    return max(quadrants, key=lambda name: mean_of(quadrants[name]))


@pytest.mark.parametrize("rotation,expected_size,marker_lands", [
    (-90, (48, 64), "top-right"),
    (90, (48, 64), "bottom-left"),
    (180, (64, 48), "bottom-right"),
])
def test_video_rotation_flag_is_baked_into_the_pixels(
        tmp_path, rotation, expected_size, marker_lands):
    video = make_rotated_video(tmp_path / "rotated.mp4", rotation)

    results, summary = run_pipeline("video", video, tmp_path / "out")

    assert summary.final_aggregate_status == Status.OK.value
    assert results and all(r.success == Status.OK.value for r in results)
    for result in results:
        with Image.open(tmp_path / "out" / result.output_filename) as saved:
            assert saved.size == expected_size
            assert _brightest_quadrant(saved) == marker_lands


def test_video_without_rotation_flag_stays_untouched(tmp_path):
    video = make_rotated_video(tmp_path / "plain.mp4", rotation=None)

    results, summary = run_pipeline("video", video, tmp_path / "out")

    assert summary.final_aggregate_status == Status.OK.value
    for result in results:
        with Image.open(tmp_path / "out" / result.output_filename) as saved:
            assert saved.size == (64, 48)
            assert _brightest_quadrant(saved) == "top-left"



@pytest.mark.parametrize(
    "kind,builder,filename",
    [
        ("static", make_png, "photo.png"),
        ("static", make_tiff, "scan.tif"),
        ("animation", make_gif, "anim.gif"),
        ("document", make_pdf, "report.pdf"),
        ("video", make_video, "clip.mp4"),
    ],
)
def test_every_pipeline_releases_its_file_handle(tmp_path, kind, builder, filename):
    media = builder(tmp_path / filename)

    results, summary = run_pipeline(kind, media, tmp_path / "out")
    assert summary is not None

    renamed = media.with_name(f"renamed_{filename}")
    media.rename(renamed)
    assert renamed.exists()



def test_truncated_gif_never_crashes_and_keeps_what_was_extracted(tmp_path):
    gif = make_gif(tmp_path / "anim.gif")
    payload = gif.read_bytes()
    gif.write_bytes(payload[: int(len(payload) * 0.55)])

    results, summary = drain_never_raises("animation", gif, tmp_path / "out")

    assert summary is not None
    assert summary.final_aggregate_status != Status.OK.value
    saved_ok = [r for r in results if r.success == Status.OK.value]
    assert saved_ok, "pipeline extracted nothing before the tear"
    for result in saved_ok:
        assert (tmp_path / "out" / result.output_filename).exists()


def drain_never_raises(kind, input_path, output_folder, **overrides):
    try:
        return run_pipeline(kind, input_path, output_folder, **overrides)
    except Exception as escaped:
        pytest.fail(f"pipeline let an exception escape: {escaped!r}")



class FakeFrame:
    def __init__(self, pts, time=None, color=(200, 30, 30)):
        self.pts = pts
        self.dts = pts
        self.time = time
        self._color = color

    def to_image(self):
        return Image.new("RGB", (32, 32), self._color)


class FakeVideoStream:
    average_rate = 10
    base_rate = 10
    duration = None
    start_time = 0
    codec_context = SimpleNamespace()

    def __init__(self, time_base=Fraction(1, 1000)):
        self.time_base = time_base


class FakeContainer:

    def __init__(self, stream, duration, frames, packet_count):
        self.streams = SimpleNamespace(video=[stream])
        self.duration = duration
        self._frames = frames
        self._packet_count = packet_count

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def seek(self, pts, stream=None, any_frame=False, backward=True):
        pass

    def decode(self, stream):
        return iter(self._frames)

    def demux(self, stream):
        return iter(
            SimpleNamespace(dts=i * 100, pts=i * 100) for i in range(self._packet_count)
        )


def install_fake_av(monkeypatch, container_factory):
    fake_av = SimpleNamespace(
        open=lambda *args, **kwargs: container_factory(),
        time_base=1_000_000,
    )
    monkeypatch.setattr(video_module, "av", fake_av)
    return fake_av


def fake_clip(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"the fake av module never reads this")
    return clip


def test_missing_pts_packets_are_bypassed_with_a_count(tmp_path, monkeypatch):
    stream = FakeVideoStream()

    def container():
        frames = [FakeFrame(None), FakeFrame(None), FakeFrame(999_999, time=1.0)]
        return FakeContainer(stream, duration=2_000_000, frames=frames, packet_count=20)

    install_fake_av(monkeypatch, container)

    results, summary = run_pipeline(
        "video", fake_clip(tmp_path), tmp_path / "out",
        VIDEO_SUMMARY_TARGET_TOTAL_FRAMES=1,
    )

    assert [r.success for r in results] == [Status.OK.value]
    assert "Bypassed 2 corrupted frames" in results[0].comment
    assert summary.final_aggregate_status == Status.OK.value


def test_targets_stop_at_the_last_real_frame_not_the_header_duration(tmp_path, monkeypatch):
    stream = FakeVideoStream()

    def container():
        frames = [
            FakeFrame(0, time=0.0, color=(200, 30, 30)),
            FakeFrame(1900, time=1.9, color=(30, 30, 200)),
        ]
        return FakeContainer(stream, duration=2_000_000, frames=frames, packet_count=20)

    install_fake_av(monkeypatch, container)

    results, summary = run_pipeline(
        "video", fake_clip(tmp_path), tmp_path / "out",
        VIDEO_SUMMARY_TARGET_TOTAL_FRAMES=2,
    )

    assert [r.success for r in results] == [Status.OK.value, Status.OK.value]
    assert "Extracted exactly at 00:00:00.00" in results[0].comment
    assert "Extracted exactly at 00:00:01.90" in results[1].comment
    assert results[0].capture_seconds == pytest.approx(0.0)
    assert results[1].capture_seconds == pytest.approx(1.9)
    assert summary.final_aggregate_status == Status.OK.value


def test_target_past_the_final_frame_captures_the_final_frame(tmp_path, monkeypatch):
    stream = FakeVideoStream()

    def container():
        frames = [
            FakeFrame(0, time=0.0, color=(200, 30, 30)),
            FakeFrame(500, time=0.5, color=(30, 30, 200)),
        ]
        return FakeContainer(stream, duration=2_000_000, frames=frames, packet_count=20)

    install_fake_av(monkeypatch, container)

    results, summary = run_pipeline(
        "video", fake_clip(tmp_path), tmp_path / "out",
        VIDEO_SUMMARY_TARGET_TOTAL_FRAMES=2,
    )

    assert [r.success for r in results] == [Status.OK.value, Status.OK.value]
    assert "past the final frame" in results[1].comment
    assert "extracted the final frame at 00:00:00.50" in results[1].comment
    assert results[1].capture_seconds == pytest.approx(0.5)
    assert summary.final_aggregate_status == Status.OK.value


def test_approximated_duration_is_flagged_in_the_summary(tmp_path, monkeypatch):
    stream = FakeVideoStream()

    def container():
        frames = [FakeFrame(999_999, time=1.0)]
        return FakeContainer(stream, duration=None, frames=frames, packet_count=20)

    install_fake_av(monkeypatch, container)

    results, summary = run_pipeline(
        "video", fake_clip(tmp_path), tmp_path / "out",
        VIDEO_SUMMARY_TARGET_TOTAL_FRAMES=1,
    )

    assert summary.final_aggregate_status == Status.OK.value
    assert "Duration approximated to 00:00:02.00" in summary.final_aggregate_comment


def test_corrupt_timebase_headers_abort_only_that_file(tmp_path, monkeypatch):
    class TornHeaderStream(FakeVideoStream):
        def __init__(self):
            pass

        @property
        def time_base(self):
            raise TypeError("corrupt header bytes")

    def container():
        return FakeContainer(TornHeaderStream(), 2_000_000, [], 20)

    install_fake_av(monkeypatch, container)

    results, summary = run_pipeline("video", fake_clip(tmp_path), tmp_path / "out")

    assert summary.final_aggregate_status == Status.FAILURE.value
    assert "Corrupted video timebase headers" in summary.final_aggregate_comment


def test_zero_timebase_fraction_aborts_only_that_file(tmp_path, monkeypatch):
    stream = FakeVideoStream(time_base=SimpleNamespace(numerator=0, denominator=1))

    def container():
        return FakeContainer(stream, 2_000_000, [], 20)

    install_fake_av(monkeypatch, container)

    results, summary = run_pipeline("video", fake_clip(tmp_path), tmp_path / "out")

    assert summary.final_aggregate_status == Status.FAILURE.value
    assert "Zero timebase fraction" in summary.final_aggregate_comment


def test_unreadable_container_aborts_only_that_file(tmp_path, monkeypatch):
    class FakeAvReadError(Exception):
        pass

    def refuse_to_open(*args, **kwargs):
        raise FakeAvReadError("moov atom not found")

    monkeypatch.setattr(video_module, "AvValueError", FakeAvReadError)
    monkeypatch.setattr(
        video_module, "av",
        SimpleNamespace(open=refuse_to_open, time_base=1_000_000),
    )

    results, summary = run_pipeline("video", fake_clip(tmp_path), tmp_path / "out")

    assert summary.final_aggregate_status == Status.FAILURE.value
    assert "PyAV could not read container" in summary.final_aggregate_comment


def test_zero_valid_packets_is_a_per_file_metadata_error(tmp_path, monkeypatch):
    stream = FakeVideoStream()

    def container():
        return FakeContainer(stream, 2_000_000, [], packet_count=0)

    install_fake_av(monkeypatch, container)

    results, summary = run_pipeline("video", fake_clip(tmp_path), tmp_path / "out")

    assert summary.final_aggregate_status == Status.FAILURE.value
    assert "Metadata error" in summary.final_aggregate_comment



class SeekPoisonedContainer(FakeContainer):

    def decode(self, stream):
        from av.error import InvalidDataError
        raise InvalidDataError(1094995529, "synthetic decoder rejection")


def test_seek_decode_error_recovers_once_with_a_fresh_decoder(tmp_path, monkeypatch):
    stream = FakeVideoStream()
    opens = []

    def container_factory():
        index = len(opens)
        opens.append(index)
        if index == 0:
            return SeekPoisonedContainer(stream, 2_000_000, [], 20)
        return FakeContainer(stream, 2_000_000, [FakeFrame(0, time=0.0)], 20)

    install_fake_av(monkeypatch, container_factory)

    results, summary = run_pipeline(
        "video", fake_clip(tmp_path), tmp_path / "out",
        VIDEO_SUMMARY_TARGET_TOTAL_FRAMES=1,
    )

    assert len(opens) == 3
    assert summary.final_aggregate_status == Status.OK.value
    assert len(results) == 1 and results[0].success == Status.OK.value
    assert "Recovered with a fresh decoder after a seek error" in results[0].comment


def test_seek_decode_error_twice_degrades_per_frame_as_before(tmp_path, monkeypatch):
    stream = FakeVideoStream()
    opens = []

    def container_factory():
        index = len(opens)
        opens.append(index)
        if index == 1:
            return FakeContainer(stream, 2_000_000, [], 20)
        return SeekPoisonedContainer(stream, 2_000_000, [], 20)

    install_fake_av(monkeypatch, container_factory)

    results, summary = run_pipeline(
        "video", fake_clip(tmp_path), tmp_path / "out",
        VIDEO_SUMMARY_TARGET_TOTAL_FRAMES=1,
    )

    assert len(opens) == 3
    assert summary.final_aggregate_status == Status.FAILURE.value
    assert results[0].success == Status.FAILURE.value
    assert "Corrupted video packet" in results[0].comment
    assert "Recovered" not in results[0].comment



@pytest.mark.parametrize(
    "kind,builder,filename,range_string",
    [
        ("static", make_tiff, "scan.tif", "2-9"),
        ("document", make_pdf, "report.pdf", "3-10"),
        ("animation", make_gif, "anim.gif", "5-20"),
    ],
)
def test_range_truncation_warning_reaches_the_summary_comment(
    tmp_path, kind, builder, filename, range_string
):
    media = builder(tmp_path / filename)

    results, summary = run_pipeline(
        kind, media, tmp_path / "out", range_string=range_string
    )

    assert summary.final_aggregate_status == Status.OK.value
    assert summary.range_status_code == RangeStatus.TRUNCATED.value
    assert "Truncated to limit" in summary.final_aggregate_comment



FAKE_EXECUTABLE = (
    b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 56 + b"PE\x00\x00"
    + b"PAYLOAD_MUST_NOT_SURVIVE" + b"\x00" * 512
)

JPEG_MAGIC = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
)


def test_executable_wearing_an_image_extension_fails_cleanly(tmp_path):
    disguised = tmp_path / "totally_an_image_bro.jpeg"
    disguised.write_bytes(FAKE_EXECUTABLE)
    out = tmp_path / "out"

    results, summary = run_pipeline("static", disguised, out)

    assert summary.final_aggregate_status == Status.FAILURE.value
    assert "cannot identify image file" in summary.final_aggregate_comment
    assert len(results) == 1
    assert results[0].success == Status.FAILURE.value
    assert list(out.iterdir()) == []


def test_faked_image_header_over_an_executable_fails_cleanly(tmp_path):
    disguised = tmp_path / "faked_header.jpeg"
    disguised.write_bytes(JPEG_MAGIC + FAKE_EXECUTABLE)
    out = tmp_path / "out"

    results, summary = run_pipeline("static", disguised, out)

    assert summary.final_aggregate_status == Status.FAILURE.value
    assert "cannot identify image file" in summary.final_aggregate_comment
    assert list(out.iterdir()) == []


def test_supported_extension_cannot_smuggle_an_unsupported_format(tmp_path):
    disguised = tmp_path / "sneaky.png"
    Image.new("RGB", (16, 16), (7, 7, 7)).save(disguised, "PPM")
    out = tmp_path / "out"

    results, summary = run_pipeline("static", disguised, out)

    assert summary.final_aggregate_status == Status.FAILURE.value
    assert "cannot identify image file" in summary.final_aggregate_comment
    assert list(out.iterdir()) == []



LONG_PATH_PREFIX = "\\\\?\\"


@pytest.mark.parametrize(
    "kind, filename",
    [
        ("static", "totally_an_image_bro.jpeg"),
        ("animation", "totally_a_gif_bro.gif"),
        ("document", "totally_a_pdf_bro.pdf"),
        ("video", "totally_a_video_bro.mp4"),
    ],
)
def test_no_pipeline_leaks_the_long_path_prefix_when_the_decode_fails(
    kind, filename, tmp_path
):
    junk = tmp_path / filename
    junk.write_bytes(FAKE_EXECUTABLE)
    out = tmp_path / "out"

    results, summary = run_pipeline(kind, junk, out)

    assert summary.final_aggregate_status == Status.FAILURE.value
    assert LONG_PATH_PREFIX not in summary.final_aggregate_comment
    for page in results:
        assert LONG_PATH_PREFIX not in page.comment


def test_the_cleaned_message_still_names_the_file_the_user_recognises(tmp_path):
    junk = tmp_path / "totally_an_image_bro.jpeg"
    junk.write_bytes(FAKE_EXECUTABLE)

    results, summary = run_pipeline("static", junk, tmp_path / "out")

    comment = summary.final_aggregate_comment
    assert LONG_PATH_PREFIX not in comment
    assert "cannot identify image file" in comment
    assert str(junk.resolve()) in comment
    assert "\\\\" not in comment


def test_a_blocked_output_path_never_leaks_the_prefix_either(tmp_path):
    source = make_png(tmp_path / "photo.png")
    out = tmp_path / "out"
    out.mkdir()
    (out / "1_photo_page_1.jpg").mkdir()

    results, summary = run_pipeline("static", source, out)

    assert summary.final_aggregate_status == Status.FAILURE.value
    assert "OS/Disk save error" in summary.final_aggregate_comment
    assert LONG_PATH_PREFIX not in summary.final_aggregate_comment
    assert all(LONG_PATH_PREFIX not in page.comment for page in results)


def test_payload_appended_to_a_real_image_never_reaches_the_output(tmp_path):
    source = tmp_path / "real_polyglot.jpeg"
    Image.new("RGB", (80, 60), (200, 30, 30)).save(source, "JPEG")
    source.write_bytes(source.read_bytes() + FAKE_EXECUTABLE)
    out = tmp_path / "out"

    results, summary = run_pipeline("static", source, out)

    assert summary.final_aggregate_status == Status.OK.value
    written = sorted(out.iterdir())
    assert len(written) == 1
    assert b"PAYLOAD_MUST_NOT_SURVIVE" not in written[0].read_bytes()
    assert b"MZ\x90\x00" not in written[0].read_bytes()


@pytest.mark.parametrize("filename, real_format", [
    ("actually_jpeg.bmp", "JPEG"),
    ("actually_png.jpg", "PNG"),
    ("actually_tiff.heic", "TIFF"),
    ("actually_gif.png", "GIF"),
])
def test_wrong_extension_still_converts_when_the_contents_are_supported(
    tmp_path, filename, real_format
):
    mislabelled = tmp_path / filename
    Image.new("RGB", (40, 30), (90, 20, 20)).save(mislabelled, real_format)
    out = tmp_path / "out"

    results, summary = run_pipeline("static", mislabelled, out)

    assert summary.final_aggregate_status == Status.OK.value
    assert len(list(out.iterdir())) == 1
