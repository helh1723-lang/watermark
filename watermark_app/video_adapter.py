from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
from PIL import Image

from .image_watermark import embed_packet_into_image, extract_fixed_packet_legacy, extract_image
from .paths import unique_output_path
from .payload import Payload, build_auth_packet_bytes, build_payload_bytes, file_sha256
from .robust_watermark import normalize_profile


VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
ProgressCallback = Callable[[float, str], None]


@dataclass
class VideoResult:
    output_path: Path
    watermark_id: str
    core_text: str
    mode: str
    quality_psnr: float = 0.0
    quality_ssim: float = 0.0
    paper_diff: float = 0.0
    frames_total: int = 0
    frames_marked: int = 0
    tiles_total: int = 0
    tiles_used: int = 0
    audio_copied: bool = False


@dataclass
class VideoExtractResult:
    payload: Payload
    frames_checked: int
    frames_verified: int
    confidence: float
    mode: str


def _frame_interval(fps: float, profile: str) -> int:
    seconds = 1.0 if profile in {"durable", "benchmark"} else 2.0
    return max(1, int(round(max(fps, 1.0) * seconds)))


def _mark_frame_indices(frames_total: int, fps: float, profile: str) -> set[int]:
    if frames_total <= 0:
        return set()
    targets = {
        "invisible": 3,
        "balanced": 6,
        "durable": 10,
        "benchmark": 12,
        "video": 8,
    }
    count = min(frames_total, targets.get(profile, 6))
    duration = frames_total / max(fps, 1.0)
    indices: set[int] = set()

    early_count = min(count, max(1, int(min(duration, 4))))
    for second in range(early_count):
        indices.add(min(frames_total - 1, int(round(second * fps))))

    remaining = count - len(indices)
    if remaining == 1:
        indices.add(frames_total // 2)
    elif remaining > 1:
        for slot in range(remaining):
            ratio = slot / max(1, remaining - 1)
            indices.add(min(frames_total - 1, int(round(ratio * (frames_total - 1)))))
    return indices


def _fourcc_for_output(path: Path) -> int:
    if path.suffix.lower() == ".avi":
        return cv2.VideoWriter_fourcc(*"FFV1")
    return cv2.VideoWriter_fourcc(*"mp4v")


def _ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def _copy_audio_if_possible(original: Path, marked_video: Path, output_path: Path) -> bool:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        if marked_video != output_path:
            shutil.move(str(marked_video), str(output_path))
        return False
    merged = output_path.with_name(output_path.stem + "_audio" + output_path.suffix)
    completed = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(marked_video),
            "-i",
            str(original),
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-shortest",
            str(merged),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode == 0 and merged.exists():
        merged.replace(output_path)
        return True
    if marked_video != output_path:
        shutil.move(str(marked_video), str(output_path))
    return False


def embed_video(
    input_path: str | Path,
    output_dir: str | Path,
    text: str,
    password: str,
    strength: str = "balanced",
    profile: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> VideoResult:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    full_packet, meta = build_payload_bytes(text, password, source_sha256=file_sha256(input_path))
    _ = full_packet
    auth_packet, _auth_meta = build_auth_packet_bytes(
        text,
        password,
        source_sha256=file_sha256(input_path),
        watermark_id=str(meta["id"]),
        created_at=int(meta["created_at"]),
    )
    profile_name = normalize_profile(profile, strength)
    carrier_profile = "video" if profile_name in {"balanced", "durable", "benchmark"} else profile_name

    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise RuntimeError("OpenCV could not open the video file.")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if width <= 0 or height <= 0:
        cap.release()
        raise RuntimeError("Video has invalid frame dimensions.")

    output_path = unique_output_path(output_dir / f"{input_path.stem}_wm.mp4")
    with tempfile.TemporaryDirectory(prefix="iwm_video_", ignore_cleanup_errors=True) as tmp_name:
        tmp_dir = Path(tmp_name)
        no_audio_path = tmp_dir / "marked_no_audio.mp4"
        writer = cv2.VideoWriter(str(no_audio_path), _fourcc_for_output(no_audio_path), fps, (width, height))
        if not writer.isOpened() and no_audio_path.suffix.lower() == ".mp4":
            writer = cv2.VideoWriter(str(no_audio_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        if not writer.isOpened():
            cap.release()
            raise RuntimeError("OpenCV could not create the output video.")

        marked_indices = _mark_frame_indices(frames_total, fps, carrier_profile)
        interval = _frame_interval(fps, carrier_profile)
        quality_psnr = 0.0
        quality_ssim = 0.0
        paper_diff = 0.0
        tiles_total = 0
        tiles_used = 0
        frames_marked = 0
        index = 0
        if progress_callback:
            progress_callback(5, "视频已打开")
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            should_mark = index in marked_indices if marked_indices else frames_marked < 4 and index % interval == 0
            if should_mark:
                if progress_callback:
                    frame_label = f"{index + 1}/{frames_total}" if frames_total else str(index + 1)
                    progress_callback(10 + min(75, (index / max(1, frames_total or index + 1)) * 75), f"写入视频帧 {frame_label}")
                frame_path = tmp_dir / f"frame_{index:08d}.png"
                marked_path = tmp_dir / f"marked_{index:08d}.png"
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                Image.fromarray(rgb).save(frame_path)
                marked_output, repeat = embed_packet_into_image(
                    frame_path,
                    tmp_dir,
                    auth_packet,
                    password,
                    output_name=marked_path.name,
                    strength="video",
                )
                marked_rgb = cv2.cvtColor(cv2.imread(str(marked_output)), cv2.COLOR_BGR2RGB)
                frame = cv2.cvtColor(marked_rgb, cv2.COLOR_RGB2BGR)
                tiles_total += repeat
                tiles_used += repeat
                frames_marked += 1
            elif progress_callback and frames_total and index % max(1, int(round(fps))) == 0:
                progress_callback(10 + min(75, (index / frames_total) * 75), f"编码视频帧 {index + 1}/{frames_total}")
            writer.write(frame)
            index += 1
        cap.release()
        writer.release()
        if progress_callback:
            progress_callback(90, "生成 MP4 输出")
        audio_copied = _copy_audio_if_possible(input_path, no_audio_path, output_path)

    divisor = max(1, frames_marked)
    return VideoResult(
        output_path=output_path,
        watermark_id=str(meta["id"]),
        core_text=str(meta["core_text"]),
        mode=f"video-dct-auth-{profile_name}-carrier-{carrier_profile}",
        quality_psnr=quality_psnr / divisor if quality_psnr else 0.0,
        quality_ssim=quality_ssim / divisor if quality_ssim else 0.0,
        paper_diff=paper_diff / divisor if paper_diff else 0.0,
        frames_total=frames_total or index,
        frames_marked=frames_marked,
        tiles_total=tiles_total,
        tiles_used=tiles_used,
        audio_copied=audio_copied,
    )


def extract_video(
    input_path: str | Path,
    password: str,
    deep_scan: bool = False,
    max_frames: int = 24,
) -> VideoExtractResult:
    input_path = Path(input_path)
    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise RuntimeError("OpenCV could not open the video file.")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    interval = max(1, int(round(max(fps, 1.0))))
    frames_checked = 0
    frames_verified = 0
    best = None
    last_error: Exception | None = None
    saved_frames: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="iwm_video_read_") as tmp_name:
        tmp_dir = Path(tmp_name)
        index = 0
        while frames_checked < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            if index % interval == 0:
                frame_path = tmp_dir / f"read_{index:08d}.png"
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                Image.fromarray(rgb).save(frame_path)
                saved_frames.append(frame_path)
                frames_checked += 1
                try:
                    extracted = extract_fixed_packet_legacy(frame_path, password)
                    frames_verified += 1
                    if best is None or extracted.confidence > best.confidence:
                        best = extracted
                except Exception as exc:
                    last_error = exc
                    try:
                        extracted = extract_image(frame_path, password, deep_scan=False)
                        frames_verified += 1
                        if best is None or extracted.confidence > best.confidence:
                            best = extracted
                    except Exception as fallback_exc:
                        last_error = fallback_exc
            index += 1
        if best is None and deep_scan:
            for frame_path in saved_frames[: min(3, len(saved_frames))]:
                try:
                    extracted = extract_fixed_packet_legacy(frame_path, password)
                    frames_verified += 1
                    if best is None or extracted.confidence > best.confidence:
                        best = extracted
                except Exception as exc:
                    last_error = exc
                    try:
                        extracted = extract_image(frame_path, password, deep_scan=True)
                        frames_verified += 1
                        if best is None or extracted.confidence > best.confidence:
                            best = extracted
                    except Exception as fallback_exc:
                        last_error = fallback_exc
    cap.release()
    if best is None:
        detail = f": {last_error}" if last_error else ""
        raise RuntimeError(f"No authenticated video watermark was recovered{detail}")
    return VideoExtractResult(
        payload=best.payload,
        frames_checked=frames_checked,
        frames_verified=frames_verified,
        confidence=max(best.confidence, frames_verified / max(1, frames_checked)),
        mode=f"video-{best.algorithm}-{best.profile}",
    )
