# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import math
from fractions import Fraction
import logging
import numpy as np
from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import Generator

if TYPE_CHECKING:
    from range_parsers import VideoSelector

from PIL import Image

from fs_utils import format_hms, get_safe_path
from schemas import Status, PageResult, FileSummary
from pipelines.base_pipeline import BaseMediaPipeline
from to_jpeg_converter import ToJpegConverter, is_frame_distinct
import contextlib


class _AvUnavailableError(Exception):
    pass


try:
    import av
    from av.error import InvalidDataError, ArgumentError as AvValueError
except ImportError as e:
    av = None
    av_err = str(e)
    InvalidDataError = AvValueError = _AvUnavailableError
except Exception as unexpected:
    av = None
    av_err = str(unexpected)
    InvalidDataError = AvValueError = _AvUnavailableError

logger = logging.getLogger(__name__)

_ROTATION_TRANSPOSE = {
    90: Image.Transpose.ROTATE_90,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_270,
}


_TRC_PQ = 16
_TRC_HLG = 18
_COLOR_RANGE_FULL = 2

HLG_A, HLG_B, HLG_C = 0.17883277, 0.28466892, 0.55991073
HLG_SYSTEM_GAMMA = 1.2

PQ_M1 = 2610.0 / 16384.0
PQ_M2 = 2523.0 / 4096.0 * 128.0
PQ_C1 = 3424.0 / 4096.0
PQ_C2 = 2413.0 / 4096.0 * 32.0
PQ_C3 = 2392.0 / 4096.0 * 32.0

L_HDR = 1000.0
L_SDR = 100.0

M2020_TO_709 = np.array([[ 1.6605, -0.5876, -0.0728],
                         [-0.1246,  1.1329, -0.0083],
                         [-0.0182, -0.1006,  1.1187]], dtype=np.float32)


class HdrExpansionAssumptionError(Exception):
    pass


def detect_hdr_transfer(codec_context):
    transfer = getattr(codec_context, "color_trc", None)
    if transfer == _TRC_HLG:
        return "HLG"
    if transfer == _TRC_PQ:
        return "PQ"
    return None


def hdr_signal_from_yuv(arr, bit_depth, full_range):
    shift = 1 << (16 - bit_depth)
    if (arr[..., 0] % shift).any():
        raise HdrExpansionAssumptionError(
            f"luma plane is off the 2^(16-{bit_depth}) = x{shift} grid; "
            "swscale no longer expands N-bit video by a plain shift"
        )
    planes = arr.astype(np.float32) / float(shift)
    y, u, v = planes[..., 0], planes[..., 1], planes[..., 2]
    if full_range:
        peak = float((1 << bit_depth) - 1)
        yn = np.clip(y / peak, 0, 1)
        cb = (u - float(1 << (bit_depth - 1))) / peak
        cr = (v - float(1 << (bit_depth - 1))) / peak
    else:
        scale = float(1 << (bit_depth - 8))
        yn = np.clip((y - 16.0 * scale) / (219.0 * scale), 0, 1)
        cb = (u - 128.0 * scale) / (224.0 * scale)
        cr = (v - 128.0 * scale) / (224.0 * scale)
    del planes, y, u, v
    r = yn + 1.4746 * cr
    g = yn - 0.16455312684366 * cb - 0.57135312684366 * cr
    b = yn + 1.8814 * cb
    return np.clip(np.stack([r, g, b], axis=-1), 0, 1)


def hlg_eotf(signal, gamma=HLG_SYSTEM_GAMMA):
    scene = np.where(signal <= 0.5,
                     signal * signal / 3.0,
                     (np.exp((signal - HLG_C) / HLG_A) + HLG_B) / 12.0)
    ys = (0.2627 * scene[..., 0] + 0.6780 * scene[..., 1]
          + 0.0593 * scene[..., 2])
    return scene * (np.maximum(ys, 1e-6) ** (gamma - 1.0))[..., None]


def pq_eotf(signal):
    p = signal ** (1.0 / PQ_M2)
    absolute = (np.maximum(p - PQ_C1, 0) / (PQ_C2 - PQ_C3 * p)) ** (1.0 / PQ_M1)
    return np.clip(absolute * (10000.0 / L_HDR), 0, 1)


def bt2446_method_a(display_linear):
    p = display_linear ** (1 / 2.4)
    y_p = 0.2627 * p[..., 0] + 0.6780 * p[..., 1] + 0.0593 * p[..., 2]
    rho_hdr = 1 + 32 * (L_HDR / 10000.0) ** (1 / 2.4)
    y_pp = np.log(1 + (rho_hdr - 1) * y_p) / math.log(rho_hdr)
    y_c = np.where(
        y_pp <= 0.7399, 1.0770 * y_pp,
        np.where(y_pp < 0.9909,
                 -1.1510 * y_pp ** 2 + 2.7811 * y_pp - 0.6302,
                 0.5000 * y_pp + 0.5000))
    rho_sdr = 1 + 32 * (L_SDR / 10000.0) ** (1 / 2.4)
    y_sdr = (rho_sdr ** y_c - 1) / (rho_sdr - 1)
    f_scale = y_sdr / (1.1 * np.maximum(y_p, 1e-6))
    cb_tmo = f_scale * (p[..., 2] - y_p) / 1.8814
    cr_tmo = f_scale * (p[..., 0] - y_p) / 1.4746
    y_tmo = y_sdr - np.maximum(0.1 * cr_tmo, 0)
    r_p = y_tmo + 1.4746 * cr_tmo
    b_p = y_tmo + 1.8814 * cb_tmo
    g_p = (y_tmo - 0.2627 * r_p - 0.0593 * b_p) / 0.6780
    return np.clip(np.stack([r_p, g_p, b_p], axis=-1), 0, 1) ** 2.4


def srgb_encode(linear):
    linear = np.clip(linear, 0, 1)
    return np.where(linear <= 0.0031308,
                    12.92 * linear,
                    1.055 * linear ** (1 / 2.4) - 0.055)


def hdr_yuv16_to_srgb(arr, bit_depth, full_range, transfer):
    rgb = hdr_signal_from_yuv(arr, bit_depth, full_range)
    rgb = hlg_eotf(rgb) if transfer == "HLG" else pq_eotf(rgb)
    rgb = bt2446_method_a(rgb)
    rgb = np.clip(rgb @ M2020_TO_709.T, 0, 1)
    return (srgb_encode(rgb) * 255 + 0.5).astype(np.uint8)


def hdr_frame_to_srgb_image(frame, transfer):
    arr = frame.to_ndarray(format="yuv444p16le")
    bit_depth = frame.format.components[0].bits
    full_range = getattr(frame, "color_range", None) == _COLOR_RANGE_FULL
    return Image.fromarray(
        hdr_yuv16_to_srgb(arr, bit_depth, full_range, transfer)
    )


class VideoPipeline(BaseMediaPipeline):

    def __init__(
        self,
        settings,
        file_id: int,
        input_path: Path,
        relative_path: str,
        original_extension: str,
        output_folder: Path,
        converter: ToJpegConverter,
        video_selector: "VideoSelector",
    ):
        super().__init__(settings, file_id, input_path, relative_path, original_extension, output_folder)
        self.converter = converter
        self.video_selector = video_selector

    def process(self) -> Generator[PageResult, None, FileSummary]:
        if av is None:
            return (yield from self.abort_pipeline(f"PyAV Missing: {av_err}", av_err))

        ok_count = skipped_count = failed_count = 0
        error_summaries = []
        last_saved = None

        logger.info(f"Starting video extraction for file: {self.relative_path}")

        try:
            with av.open(get_safe_path(self.input_path)) as container:

                if not container.streams.video:
                    return (
                        yield from self.abort_pipeline("No video stream found.", "No video stream")
                    )

                video_stream = container.streams.video[0]

                hdr_transfer = detect_hdr_transfer(video_stream.codec_context)
                hdr_fallback_active = False

                fps, total_frames, content_end_sec, err_msg = self._validate_metadata(
                    video_stream
                )

                if err_msg or fps <= 0 or total_frames <= 0:
                    return (
                        yield from self.abort_pipeline(
                            f"Metadata error: {err_msg} (FPS: {fps}, Total: {total_frames})",
                            err_msg,
                        )
                    )

                duration_approximated = False
                if container.duration is not None:
                    duration = float(container.duration) / av.time_base
                elif video_stream.duration and video_stream.time_base:
                    duration = float(video_stream.duration * video_stream.time_base)
                else:
                    duration_approximated = True
                    duration = (total_frames / fps) if fps > 0 else 0.0

                if duration <= 0:
                    return (
                        yield from self.abort_pipeline(
                            f"Could not determine valid duration. (Calculated: {duration}s)",
                            "Invalid duration",
                        )
                    )

                active_config = (
                    {
                        "TARGET_TOTAL_FRAMES": self.settings.VIDEO_SUMMARY_TARGET_TOTAL_FRAMES,
                        "SCENE_SENSITIVITY": self.settings.VIDEO_SUMMARY_SCENE_SENSITIVITY,
                    }
                    if self.settings.VIDEO_MODE == "SUMMARY"
                    else {
                        "CAPTURE_RATE_FPS": self.settings.VIDEO_SAMPLING_CAPTURE_RATE_FPS,
                        "MAX_FRAMES_BUDGET": self.settings.VIDEO_SAMPLING_MAX_FRAMES_BUDGET,
                        "SCENE_SENSITIVITY": self.settings.VIDEO_SAMPLING_SCENE_SENSITIVITY,
                    }
                )
                time_result = self.video_selector.get_target_times(
                    duration, self.settings.VIDEO_MODE, active_config, content_end_sec
                )
                target_times, range_status, range_details = (
                    time_result.times,
                    time_result.status,
                    time_result.details,
                )

                if duration_approximated:
                    range_details = (
                        f"[Warning: Corrupt headers. Duration approximated to {format_hms(duration)}] "
                        + range_details
                    )

                if range_details:
                    error_summaries.append(range_details)

                range_string = self.video_selector.format_time_range(target_times, truncate=False)

                if not target_times:
                    return self.finalize_results(
                        0,
                        0,
                        0,
                        0,
                        total_frames,
                        "",
                        range_status,
                        [*error_summaries, "Skipped requested range out of bounds"],
                    )

                sensitivity = active_config["SCENE_SENSITIVITY"]
                try:
                    time_base_frac = video_stream.time_base
                    if not time_base_frac:
                        time_base_frac = Fraction(1, av.time_base)
                except (TypeError, ValueError):
                    return (
                        yield from self.abort_pipeline(
                            "Corrupted video timebase headers.", "Timebase TypeError"
                        )
                    )

                if time_base_frac.numerator == 0 or time_base_frac.denominator == 0:
                    return (
                        yield from self.abort_pipeline("Zero timebase fraction.", "Timebase error")
                    )

                start_offset = video_stream.start_time or 0

                for seq_num, target_sec in enumerate(target_times):
                    frame_num = seq_num + 1
                    out_name = self.get_filename(frame_num)

                    captured_img = None
                    corrupted_frames_skipped = 0
                    actual_time_captured = 0.0

                    try:
                        try:
                            target_pts = (
                                round(
                                    target_sec
                                    * time_base_frac.denominator
                                    / time_base_frac.numerator
                                )
                                + start_offset
                            )
                        except ZeroDivisionError:
                            failed_count += 1
                            error_summaries.append("Zero div PTS calculation")
                            yield PageResult(
                                frame_num,
                                out_name,
                                Status.FAILURE.value,
                                f"Fatal Timebase Error at {format_hms(target_sec)}",
                            )
                            continue

                        frame_found = False
                        decoder_recovered = False
                        try:
                            (
                                captured_img,
                                raw_frame_time_sec,
                                past_final_frame,
                                corrupted_frames_skipped,
                                hdr_fell_back_now,
                            ) = self._capture_target_image(
                                container, video_stream, target_pts,
                                frame_num, hdr_transfer, hdr_fallback_active,
                            )
                        except InvalidDataError as decode_error:
                            logger.warning(
                                f"Decoder rejected data seeking to "
                                f"{format_hms(target_sec)} in "
                                f"{self.relative_path} ({decode_error}); "
                                f"retrying once with a fresh decoder."
                            )
                            with av.open(get_safe_path(self.input_path)) as retry_container:
                                (
                                    captured_img,
                                    raw_frame_time_sec,
                                    past_final_frame,
                                    corrupted_frames_skipped,
                                    hdr_fell_back_now,
                                ) = self._capture_target_image(
                                    retry_container,
                                    retry_container.streams.video[0],
                                    target_pts, frame_num,
                                    hdr_transfer, hdr_fallback_active,
                                )
                            decoder_recovered = True

                        if hdr_fell_back_now:
                            hdr_fallback_active = True
                            error_summaries.append(
                                "HDR tone-mapping fell back to plain decode "
                                "(swscale expansion assumption violated)"
                            )

                        if captured_img is not None:
                            stream_start_time_sec = float(start_offset * time_base_frac)
                            actual_time_captured = max(
                                0.0, raw_frame_time_sec - stream_start_time_sec
                            )
                            frame_found = True
                            out_name = self.get_filename(frame_num, actual_time_captured)

                        if not frame_found or not captured_img:
                            failed_count += 1
                            err_msg = f"Seek error: Reached end of stream trying to find {format_hms(target_sec)}."
                            if corrupted_frames_skipped > 0:
                                err_msg += (
                                    f" (Skipped {corrupted_frames_skipped} corrupted frames)."
                                )

                            error_summaries.append("End of stream seek fail")
                            yield PageResult(frame_num, out_name, Status.FAILURE.value, err_msg)
                            continue

                        current_arr = np.array(captured_img)
                        if not is_frame_distinct(current_arr, last_saved, sensitivity):
                            skipped_count += 1
                            yield PageResult(
                                frame_num,
                                out_name,
                                Status.SKIPPED.value,
                                f"Scene static at {format_hms(actual_time_captured)}",
                                capture_seconds=actual_time_captured,
                            )
                            continue

                        success, comment = self.converter.process_image(
                            captured_img, self.get_output_path(out_name)
                        )

                        if success == Status.OK.value and hdr_transfer:
                            if hdr_fallback_active:
                                hdr_note = (
                                    "HDR video decoded without tone-mapping "
                                    "(unexpected decoder output); colors may "
                                    "look washed out"
                                )
                            else:
                                hdr_note = (
                                    f"HDR video ({hdr_transfer}) tone-mapped "
                                    f"to SDR (BT.2446-A)"
                                )
                            comment = f"{comment}; {hdr_note}" if comment else hdr_note

                        if success == Status.OK.value:
                            ok_count += 1
                            last_saved = current_arr

                            if past_final_frame:
                                comment = (
                                    f"Target {format_hms(target_sec)} is past the final frame; "
                                    f"extracted the final frame at {format_hms(actual_time_captured)}. "
                                    + comment
                                )
                            else:
                                comment = (
                                    f"Extracted exactly at {format_hms(actual_time_captured)}. "
                                    + comment
                                )
                            if corrupted_frames_skipped > 0:
                                comment += f" [Warning: Bypassed {corrupted_frames_skipped} corrupted frames]."
                            if decoder_recovered:
                                comment += " [Warning: Recovered with a fresh decoder after a seek error]."

                            logger.debug(
                                f"Successfully extracted and saved frame {frame_num} at {actual_time_captured:.2f}s."
                            )
                        else:
                            failed_count += 1
                            error_summaries.append(comment)

                        yield PageResult(
                            frame_num, out_name, success, comment,
                            capture_seconds=actual_time_captured,
                        )

                    except InvalidDataError as corrupted_packet_error:
                        failed_count += 1
                        err_msg = f"Corrupted video packet near {format_hms(target_sec)}: {corrupted_packet_error!s}"
                        error_summaries.append(err_msg)
                        logger.error(err_msg, exc_info=True)
                        yield PageResult(frame_num, out_name, Status.FAILURE.value, err_msg)

                    except Exception as e:
                        failed_count += 1
                        error_summaries.append(str(e))
                        logger.error(
                            f"Unexpected error extracting frame {frame_num} at {target_sec:.2f}s: {e}",
                            exc_info=True,
                        )
                        yield PageResult(frame_num, out_name, Status.FAILURE.value, str(e))

                    finally:
                        if captured_img is not None:
                            with contextlib.suppress(Exception):
                                captured_img.close()

            return self.finalize_results(
                len(target_times),
                ok_count,
                skipped_count,
                failed_count,
                total_frames,
                range_string,
                range_status,
                error_summaries,
            )

        except AvValueError as e:
            return (
                yield from self.abort_pipeline(
                    f"PyAV could not read container: {e}", f"PyAV read error: {e}"
                )
            )
        except Exception as e:
            return (yield from self.abort_pipeline(f"PyAV critical failure: {e}", str(e)))

    def _capture_target_image(self, container, video_stream, target_pts,
                              frame_num, hdr_transfer, hdr_fallback_active):
        container.seek(
            target_pts, stream=video_stream, any_frame=False, backward=True
        )
        corrupted_frames_skipped = 0
        target_frame = None
        last_decoded_frame = None

        for frame in container.decode(video_stream):
            current_pts = frame.pts if frame.pts is not None else frame.dts

            if current_pts is None:
                corrupted_frames_skipped += 1
                logger.warning(
                    f"Corrupted packet (Missing PTS/DTS) skipped while seeking to frame {frame_num}."
                )
                continue

            if current_pts >= target_pts:
                target_frame = frame
                break

            last_decoded_frame = frame

        past_final_frame = False
        if target_frame is None and last_decoded_frame is not None:
            target_frame = last_decoded_frame
            past_final_frame = True

        if target_frame is None:
            return None, 0.0, past_final_frame, corrupted_frames_skipped, False

        hdr_fell_back_now = False
        if hdr_transfer and not hdr_fallback_active:
            try:
                captured_img = hdr_frame_to_srgb_image(target_frame, hdr_transfer)
            except HdrExpansionAssumptionError as assumption:
                logger.error(
                    f"HDR tone-mapping disabled for "
                    f"{self.relative_path}: {assumption}"
                )
                captured_img = target_frame.to_image()
                hdr_fell_back_now = True
        else:
            captured_img = target_frame.to_image()

        rotation = int(getattr(target_frame, "rotation", 0) or 0) % 360
        transpose = _ROTATION_TRANSPOSE.get(rotation)
        if transpose is not None:
            upright = captured_img.transpose(transpose)
            captured_img.close()
            captured_img = upright

        raw_frame_time_sec = (
            float(target_frame.time) if target_frame.time is not None else 0.0
        )
        return (captured_img, raw_frame_time_sec, past_final_frame,
                corrupted_frames_skipped, hdr_fell_back_now)

    def _validate_metadata(self, stream):
        if av is None:
            return 0.0, 0, None, "PyAV not installed"

        fps = (
            float(stream.average_rate)
            if stream.average_rate
            else (float(stream.base_rate) if stream.base_rate else 0.0)
        )
        if math.isnan(fps):
            fps = 0.0

        if fps > 240:
            logger.warning(f"Suspiciously High FPS ({fps:.2f}) detected in {self.relative_path}.")

        count = 0
        max_packet_ts = None
        scan_time_base = None
        scan_start_time = 0
        try:
            with av.open(
                get_safe_path(self.input_path), mode="r", metadata_errors="ignore"
            ) as temp:
                if temp.streams.video:
                    scan_stream = temp.streams.video[0]
                    try:
                        scan_time_base = scan_stream.time_base
                        scan_start_time = scan_stream.start_time or 0
                    except Exception:
                        scan_time_base = None
                    for pkt in temp.demux(scan_stream):
                        if pkt.dts is not None:
                            count += 1
                        pkt_ts = pkt.pts if pkt.pts is not None else pkt.dts
                        if pkt_ts is not None and (max_packet_ts is None or pkt_ts > max_packet_ts):
                            max_packet_ts = pkt_ts
        except Exception as e:
            logger.warning(f"Demux scan failed prematurely. Counted {count} packets: {e}")

        content_end_sec = None
        if max_packet_ts is not None and scan_time_base:
            try:
                content_end_sec = max(
                    0.0, float((max_packet_ts - scan_start_time) * scan_time_base)
                )
            except Exception:
                content_end_sec = None

        return fps, count, content_end_sec, "" if count > 0 else "0 packets"
