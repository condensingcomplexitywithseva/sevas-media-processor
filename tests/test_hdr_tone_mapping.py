# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np
from PIL import Image

import av

import pipelines.video as video_module
from pipelines.video import (
    HdrExpansionAssumptionError,
    bt2446_method_a,
    detect_hdr_transfer,
    hdr_frame_to_srgb_image,
    hdr_signal_from_yuv,
    hdr_yuv16_to_srgb,
    hlg_eotf,
    pq_eotf,
    srgb_encode,
)
from pipelines.video import VideoPipeline
from range_parsers import VideoSelector
from schemas import Status
from to_jpeg_converter import ToJpegConverter


def synth_yuv16(y_code, u_code, v_code, bit_depth, size=(16, 16)):
    shift = 1 << (16 - bit_depth)
    arr = np.empty((size[0], size[1], 3), dtype=np.uint16)
    arr[..., 0] = y_code * shift
    arr[..., 1] = u_code * shift
    arr[..., 2] = v_code * shift
    return arr



HAND_COMPUTED_PATCHES = [
    ("HLG", 10, False, (940, 512, 512), (255, 255, 255)),
    ("HLG", 10, False, (64, 512, 512), (0, 0, 0)),
    ("HLG", 10, False, (502, 512, 512), (100, 100, 100)),
    ("HLG", 10, False, (410, 600, 450), (30, 86, 117)),
    ("HLG", 8, False, (126, 128, 128), (101, 101, 101)),
    ("HLG", 8, False, (100, 150, 112), (24, 84, 114)),
    ("HLG", 10, True, (1023, 512, 512), (255, 255, 255)),
    ("HLG", 10, True, (512, 512, 512), (100, 100, 100)),
    ("PQ", 10, False, (502, 512, 512), (127, 127, 127)),
    ("PQ", 10, False, (700, 512, 512), (245, 245, 245)),
    ("PQ", 10, False, (400, 600, 450), (0, 89, 184)),
    ("PQ", 10, True, (512, 512, 512), (127, 127, 127)),
    ("PQ", 8, False, (170, 128, 128), (235, 235, 235)),
]


@pytest.mark.parametrize(
    "transfer,bit_depth,full_range,codes,expected", HAND_COMPUTED_PATCHES
)
def test_hand_computed_patches_pin_the_whole_chain(
    transfer, bit_depth, full_range, codes, expected
):
    arr = synth_yuv16(*codes, bit_depth)
    out = hdr_yuv16_to_srgb(arr, bit_depth, full_range, transfer)
    assert out.dtype == np.uint8
    assert out.shape == arr.shape
    unique = np.unique(out.reshape(-1, 3), axis=0)
    assert unique.shape[0] == 1, f"flat patch came out non-uniform: {unique}"
    assert tuple(unique[0]) == expected


def test_expansion_guard_trips_on_off_grid_luma():
    arr = synth_yuv16(502, 512, 512, 10)
    arr[3, 7, 0] += 1
    with pytest.raises(HdrExpansionAssumptionError) as error:
        hdr_yuv16_to_srgb(arr, 10, False, "HLG")
    assert "grid" in str(error.value)


def test_off_grid_chroma_does_not_trip_the_guard():
    arr = synth_yuv16(502, 512, 512, 10)
    arr[3, 7, 1] += 1
    arr[5, 2, 2] += 33
    out = hdr_yuv16_to_srgb(arr, 10, False, "HLG")
    assert out.dtype == np.uint8


def test_the_chain_runs_in_float32():
    arr = synth_yuv16(502, 540, 480, 10)
    signal = hdr_signal_from_yuv(arr, 10, False)
    assert signal.dtype == np.float32
    display_hlg = hlg_eotf(signal)
    assert display_hlg.dtype == np.float32
    display_pq = pq_eotf(signal)
    assert display_pq.dtype == np.float32
    sdr = bt2446_method_a(display_hlg)
    assert sdr.dtype == np.float32
    lin709 = np.clip(sdr @ video_module.M2020_TO_709.T, 0, 1)
    assert lin709.dtype == np.float32
    assert srgb_encode(lin709).dtype == np.float32


@pytest.mark.parametrize(
    "color_trc,expected",
    [
        (18, "HLG"),
        (16, "PQ"),
        (1, None),
        (2, None),
        (6, None),
        (None, None),
    ],
)
def test_detection_trusts_only_the_declared_transfer(color_trc, expected):
    context = (
        SimpleNamespace() if color_trc is None
        else SimpleNamespace(color_trc=color_trc)
    )
    assert detect_hdr_transfer(context) == expected



MAX_DIMENSION = 1000


def make_settings(**overrides):
    values = dict(
        MAX_DIMENSION=MAX_DIMENSION,
        OUTPUT_FILENAME_PREFIX_LENGTH=20,
        OUTPUT_FILENAME_TIMESTAMPS=True,
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


def run_video(input_path, output_folder, **setting_overrides):
    output_folder.mkdir(exist_ok=True)
    pipeline = VideoPipeline(
        make_settings(**setting_overrides),
        1,
        input_path,
        input_path.name,
        input_path.suffix,
        output_folder,
        ToJpegConverter(90, MAX_DIMENSION, 1024, 30, (255, 255, 255)),
        VideoSelector(""),
    )
    return drain(pipeline.process())


def make_tagged_video(path, color_trc, y_code=126, u_code=128, v_code=128):
    width, height = 64, 48
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=10)
        stream.width, stream.height = width, height
        stream.pix_fmt = "yuv420p"
        stream.options = {"qp": "0"}
        if color_trc is not None:
            stream.codec_context.color_trc = color_trc
            stream.codec_context.colorspace = 9
            stream.codec_context.color_primaries = 9
        planes = np.concatenate([
            np.full(width * height, y_code, np.uint8),
            np.full(width * height // 4, u_code, np.uint8),
            np.full(width * height // 4, v_code, np.uint8),
        ]).reshape(-1, width)
        for _ in range(10):
            frame = av.VideoFrame.from_ndarray(planes, format="yuv420p")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def center_pixel(jpeg_path):
    with Image.open(jpeg_path) as image:
        image.load()
        return image.getpixel((image.width // 2, image.height // 2))


HDR_NOTE_HLG = "HDR video (HLG) tone-mapped to SDR (BT.2446-A)"
HDR_NOTE_PQ = "HDR video (PQ) tone-mapped to SDR (BT.2446-A)"

JPEG_TOLERANCE = 3


@pytest.mark.parametrize(
    "color_trc,y_code,expected_level,note",
    [
        (18, 126, 101, HDR_NOTE_HLG),
        (16, 170, 235, HDR_NOTE_PQ),
    ],
)
def test_tagged_hdr_video_frames_are_tone_mapped(
    tmp_path, color_trc, y_code, expected_level, note
):
    video = make_tagged_video(tmp_path / "hdr.mp4", color_trc, y_code=y_code)
    results, summary = run_video(video, tmp_path / "out")

    assert summary.final_aggregate_status == Status.OK.value
    assert len(results) == 3
    for result in results:
        assert result.success == Status.OK.value
        assert note in result.comment
        pixel = center_pixel(tmp_path / "out" / result.output_filename)
        for channel in pixel:
            assert abs(channel - expected_level) <= JPEG_TOLERANCE, (
                f"expected ~{expected_level} per channel, got {pixel} — "
                f"the frame did not go through the tone-mapping chain"
            )


def test_untagged_control_stays_naive_and_note_free(tmp_path, monkeypatch):
    video = make_tagged_video(tmp_path / "sdr.mp4", None)

    results, summary = run_video(video, tmp_path / "out_a")
    assert summary.final_aggregate_status == Status.OK.value
    for result in results:
        assert "HDR" not in result.comment
        pixel = center_pixel(tmp_path / "out_a" / result.output_filename)
        for channel in pixel:
            assert abs(channel - 128) <= JPEG_TOLERANCE

    monkeypatch.setattr(video_module, "detect_hdr_transfer", lambda cc: None)
    results_off, _ = run_video(video, tmp_path / "out_b")

    for on, off in zip(results, results_off):
        bytes_on = (tmp_path / "out_a" / on.output_filename).read_bytes()
        bytes_off = (tmp_path / "out_b" / off.output_filename).read_bytes()
        assert bytes_on == bytes_off, (
            "SDR output changed while the HDR machinery was merely present"
        )


def test_guard_trip_falls_back_gracefully_with_visible_warnings(
    tmp_path, monkeypatch
):
    video = make_tagged_video(tmp_path / "hdr.mp4", 18)

    def tripped_guard(frame, transfer):
        raise HdrExpansionAssumptionError("synthetic guard trip (test)")

    monkeypatch.setattr(video_module, "hdr_frame_to_srgb_image", tripped_guard)
    results, summary = run_video(video, tmp_path / "out")

    assert summary.final_aggregate_status == Status.OK.value
    assert "HDR tone-mapping fell back to plain decode" in (
        summary.final_aggregate_comment
    )
    assert len(results) == 3
    for result in results:
        assert result.success == Status.OK.value
        assert "HDR video decoded without tone-mapping" in result.comment
        assert "washed" in result.comment
        pixel = center_pixel(tmp_path / "out" / result.output_filename)
        for channel in pixel:
            assert abs(channel - 128) <= JPEG_TOLERANCE


def test_hdr_conversion_feeds_the_normal_rotation_and_converter_chain(
    tmp_path,
):
    video = make_tagged_video(tmp_path / "hdr.mp4", 18)
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        assert detect_hdr_transfer(stream.codec_context) == "HLG"
        for frame in container.decode(stream):
            image = hdr_frame_to_srgb_image(frame, "HLG")
            assert image.mode == "RGB"
            assert image.size == (64, 48)
            break
