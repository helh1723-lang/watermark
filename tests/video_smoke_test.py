from __future__ import annotations

import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from watermark_app.video_adapter import embed_video, extract_video


TMP = ROOT / "tmp" / "video_smoke_test"


def make_video(path: Path) -> None:
    fps = 6
    width, height = 512, 512
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not create a smoke-test video.")
    for index in range(12):
        y = np.linspace(0, 255, height, dtype=np.uint8)[:, None]
        x = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, :, 0] = (x + index * 5) % 255
        frame[:, :, 1] = (y + index * 7) % 255
        frame[:, :, 2] = ((x // 2 + y // 2 + index * 9) % 255).astype(np.uint8)
        cv2.rectangle(frame, (50 + index, 70), (460, 430), (30, 30, 30), 3)
        cv2.putText(frame, f"watermark video {index}", (80, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (245, 245, 245), 2)
        writer.write(frame)
    writer.release()


def make_tail_clip(source: Path, target: Path, start_frame: int = 5) -> None:
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError("OpenCV could not open the marked video.")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 6.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError("OpenCV could not create a clipped smoke-test video.")
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        writer.write(frame)
    cap.release()
    writer.release()


def main() -> int:
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True)

    password = "video-test-password"
    source = TMP / "source.mp4"
    make_video(source)

    embedded = embed_video(source, TMP, "video smoke auth", password, profile="durable")
    assert embedded.frames_marked == embedded.frames_total
    recovered = extract_video(embedded.output_path, password, max_frames=4)
    assert recovered.payload.watermark_id == embedded.watermark_id
    assert recovered.frames_verified >= 1
    assert recovered.frames_checked <= 2

    clipped = TMP / "source_wm_tail_clip.mp4"
    make_tail_clip(embedded.output_path, clipped)
    recovered_clip = extract_video(clipped, password, max_frames=2)
    assert recovered_clip.payload.watermark_id == embedded.watermark_id
    assert recovered_clip.frames_checked <= 2

    print("Video smoke test passed.")
    print(f"Video: {embedded.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
