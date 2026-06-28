from __future__ import annotations

import math
import struct
import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .payload import (
    MAGIC,
    AUTH_PACKET_LEN,
    Payload,
    PayloadError,
    bits_to_bytes,
    bytes_to_bits,
    build_payload_bytes,
    parse_payload_bytes,
    position_seed,
)
from .paths import unique_output_path
from .robust_watermark import embed_iwm2_image, extract_iwm2_image, normalize_profile


SLOT_PAIRS = [((1, 2), (2, 1)), ((2, 3), (3, 2))]
REPEAT_TRIES = [7, 5, 3, 2, 1]
STRENGTHS = {
    "subtle": {"delta": 10.0, "target_repeat": 2},
    "balanced": {"delta": 18.0, "target_repeat": 3},
    "strong": {"delta": 30.0, "target_repeat": 5},
    "video": {"delta": 46.0, "target_repeat": 7},
}


class WatermarkCapacityError(ValueError):
    pass


@dataclass
class EmbedResult:
    output_path: Path
    watermark_id: str
    core_text: str
    repeat: int
    payload_bytes: int
    algorithm: str = "image-dct"
    profile: str = "legacy"
    quality_psnr: float = 0.0
    quality_ssim: float = 0.0
    paper_diff: float = 0.0
    tiles_total: int = 0
    tiles_used: int = 0


@dataclass
class ExtractResult:
    payload: Payload
    repeat: int
    payload_bytes: int
    algorithm: str = "image-dct"
    profile: str = "legacy"
    tiles_total: int = 0
    tiles_checked: int = 0
    tiles_verified: int = 0
    bit_error_estimate: float = 0.0
    confidence: float = 0.0


def _dct_matrix(size: int = 8) -> np.ndarray:
    matrix = np.zeros((size, size), dtype=np.float32)
    factor = math.pi / (2 * size)
    for k in range(size):
        alpha = math.sqrt(1 / size) if k == 0 else math.sqrt(2 / size)
        for n in range(size):
            matrix[k, n] = alpha * math.cos((2 * n + 1) * k * factor)
    return matrix


DCT = _dct_matrix()


def _block_dct(block: np.ndarray) -> np.ndarray:
    return DCT @ block @ DCT.T


def _block_idct(coeff: np.ndarray) -> np.ndarray:
    return DCT.T @ coeff @ DCT


def _available_slots(width: int, height: int) -> list[tuple[int, int, int]]:
    block_w = width // 8
    block_h = height // 8
    margin_x = 1 if block_w > 8 else 0
    margin_y = 1 if block_h > 8 else 0
    slots: list[tuple[int, int, int]] = []
    for by in range(margin_y, max(margin_y, block_h - margin_y)):
        for bx in range(margin_x, max(margin_x, block_w - margin_x)):
            for slot_index in range(len(SLOT_PAIRS)):
                slots.append((bx, by, slot_index))
    return slots


def _ordered_slots(width: int, height: int, password: str) -> list[tuple[int, int, int]]:
    slots = _available_slots(width, height)
    seed = position_seed(password).to_bytes(8, "big")

    def sort_key(slot: tuple[int, int, int]) -> bytes:
        bx, by, slot_index = slot
        return hashlib.sha256(seed + bx.to_bytes(4, "big") + by.to_bytes(4, "big") + bytes([slot_index])).digest()

    return sorted(slots, key=sort_key)


def _choose_repeat(bit_count: int, capacity: int, target_repeat: int) -> int:
    for repeat in [target_repeat, 3, 2, 1]:
        if repeat <= target_repeat and bit_count * repeat <= capacity:
            return repeat
    raise WatermarkCapacityError(
        f"Image is too small for this watermark ({bit_count} bits, {capacity} available slots). "
        "Use a larger image, shorter text, or the subtle strength level."
    )


def _set_bit(coeff: np.ndarray, bit: int, slot_index: int, delta: float) -> None:
    first, second = SLOT_PAIRS[slot_index]
    a = coeff[first]
    b = coeff[second]
    if bit:
        diff = a - b
        if diff < delta:
            adjust = (delta - diff) / 2
            coeff[first] = a + adjust
            coeff[second] = b - adjust
    else:
        diff = b - a
        if diff < delta:
            adjust = (delta - diff) / 2
            coeff[first] = a - adjust
            coeff[second] = b + adjust


def _read_bit(coeff: np.ndarray, slot_index: int) -> int:
    first, second = SLOT_PAIRS[slot_index]
    return 1 if coeff[first] > coeff[second] else 0


def _normal_output_path(input_path: Path, output_dir: Path) -> Path:
    suffix = input_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        suffix = ".png"
    return output_dir / f"{input_path.stem}_wm{suffix}"


def embed_image_legacy(
    input_path: str | Path,
    output_dir: str | Path,
    text: str,
    password: str,
    strength: str = "balanced",
    source_sha256: str = "",
) -> EmbedResult:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    packet, meta = build_payload_bytes(text, password, source_sha256=source_sha256)
    output_path, repeat = embed_packet_into_image(input_path, output_dir, packet, password, strength)
    return EmbedResult(
        output_path=output_path,
        watermark_id=str(meta["id"]),
        core_text=str(meta["core_text"]),
        repeat=repeat,
        payload_bytes=len(packet),
        algorithm="image-dct",
        profile="legacy",
    )


def embed_packet_into_image(
    input_path: str | Path,
    output_dir: str | Path,
    packet: bytes,
    password: str,
    strength: str = "balanced",
    output_name: str | None = None,
) -> tuple[Path, int]:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    params = STRENGTHS.get(strength, STRENGTHS["balanced"])
    bits = bytes_to_bits(packet)

    image = Image.open(input_path)
    alpha = None
    if image.mode in {"RGBA", "LA"}:
        alpha = image.getchannel("A")
    ycbcr = image.convert("YCbCr")
    y, cb, cr = ycbcr.split()
    y_array = np.asarray(y, dtype=np.float32)
    height, width = y_array.shape
    slots = _ordered_slots(width, height, password)
    repeat = _choose_repeat(len(bits), len(slots), int(params["target_repeat"]))

    for repeated_index, bit in enumerate(bit for bit in bits for _ in range(repeat)):
        bx, by, slot_index = slots[repeated_index]
        y0 = by * 8
        x0 = bx * 8
        block = y_array[y0 : y0 + 8, x0 : x0 + 8] - 128.0
        coeff = _block_dct(block)
        _set_bit(coeff, bit, slot_index, float(params["delta"]))
        y_array[y0 : y0 + 8, x0 : x0 + 8] = _block_idct(coeff) + 128.0

    y_marked = Image.fromarray(np.clip(y_array, 0, 255).astype(np.uint8), "L")
    marked = Image.merge("YCbCr", (y_marked, cb, cr)).convert("RGB")
    if alpha is not None:
        marked.putalpha(alpha)
    output_path = output_dir / output_name if output_name else _normal_output_path(input_path, output_dir)
    output_path = unique_output_path(output_path)
    save_kwargs = {}
    if output_path.suffix.lower() in {".jpg", ".jpeg"}:
        save_kwargs["quality"] = 95
    marked.save(output_path, **save_kwargs)
    return output_path, repeat


def _read_repeated_bits(
    y_array: np.ndarray,
    width: int,
    height: int,
    password: str,
    bit_count: int,
    repeat: int,
) -> list[int]:
    slots = _ordered_slots(width, height, password)
    if bit_count * repeat > len(slots):
        raise PayloadError("Image capacity is smaller than the requested recovery size.")
    recovered: list[int] = []
    cursor = 0
    for _ in range(bit_count):
        votes = 0
        for _ in range(repeat):
            bx, by, slot_index = slots[cursor]
            cursor += 1
            y0 = by * 8
            x0 = bx * 8
            block = y_array[y0 : y0 + 8, x0 : x0 + 8] - 128.0
            coeff = _block_dct(block)
            votes += _read_bit(coeff, slot_index)
        recovered.append(1 if votes >= (repeat / 2) else 0)
    return recovered


def extract_image_legacy(input_path: str | Path, password: str) -> ExtractResult:
    image = Image.open(input_path).convert("YCbCr")
    y_array = np.asarray(image.getchannel("Y"), dtype=np.float32)
    height, width = y_array.shape
    capacity = len(_available_slots(width, height))
    last_error: Exception | None = None

    for repeat in REPEAT_TRIES:
        try:
            header_bits = _read_repeated_bits(y_array, width, height, password, 6 * 8, repeat)
            header = bits_to_bytes(header_bits)
            if len(header) != 6 or header[:4] != MAGIC:
                continue
            body_len = struct.unpack(">H", header[4:6])[0]
            packet_len = 6 + body_len + 4
            if packet_len <= 10 or packet_len * 8 * repeat > capacity:
                continue
            packet_bits = _read_repeated_bits(y_array, width, height, password, packet_len * 8, repeat)
            packet = bits_to_bytes(packet_bits)
            payload = parse_payload_bytes(packet, password)
            return ExtractResult(
                payload=payload,
                repeat=repeat,
                payload_bytes=packet_len,
                algorithm="image-dct",
                profile="legacy",
            )
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise PayloadError(f"No valid invisible watermark could be recovered: {last_error}") from last_error
    raise PayloadError("No valid invisible watermark could be recovered.")


def extract_fixed_packet_legacy(input_path: str | Path, password: str, packet_len: int = AUTH_PACKET_LEN) -> ExtractResult:
    image = Image.open(input_path).convert("YCbCr")
    y_array = np.asarray(image.getchannel("Y"), dtype=np.float32)
    height, width = y_array.shape
    capacity = len(_available_slots(width, height))
    last_error: Exception | None = None

    for repeat in REPEAT_TRIES:
        try:
            if packet_len * 8 * repeat > capacity:
                continue
            packet_bits = _read_repeated_bits(y_array, width, height, password, packet_len * 8, repeat)
            packet = bits_to_bytes(packet_bits)
            payload = parse_payload_bytes(packet, password)
            return ExtractResult(
                payload=payload,
                repeat=repeat,
                payload_bytes=packet_len,
                algorithm="image-dct-auth",
                profile="video-fast",
            )
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise PayloadError(f"No valid fixed-length DCT watermark could be recovered: {last_error}") from last_error
    raise PayloadError("No valid fixed-length DCT watermark could be recovered.")


def embed_image(
    input_path: str | Path,
    output_dir: str | Path,
    text: str,
    password: str,
    strength: str = "balanced",
    source_sha256: str = "",
    profile: str | None = None,
) -> EmbedResult:
    profile_name = normalize_profile(profile, strength)
    if profile_name == "legacy":
        return embed_image_legacy(input_path, output_dir, text, password, strength, source_sha256)
    robust = embed_iwm2_image(input_path, output_dir, text, password, profile_name, source_sha256)
    return EmbedResult(
        output_path=robust.output_path,
        watermark_id=robust.watermark_id,
        core_text=robust.core_text,
        repeat=0,
        payload_bytes=robust.payload_bytes,
        algorithm="image-iwm2-local",
        profile=robust.profile,
        quality_psnr=robust.quality_psnr,
        quality_ssim=robust.quality_ssim,
        paper_diff=robust.paper_diff,
        tiles_total=robust.tiles_total,
        tiles_used=robust.tiles_used,
    )


def extract_image(input_path: str | Path, password: str, deep_scan: bool = False) -> ExtractResult:
    robust_error: Exception | None = None
    metadata_profile = ""
    try:
        with Image.open(input_path) as meta_image:
            metadata_profile = str(meta_image.info.get("iwm_profile", ""))
    except Exception:
        metadata_profile = ""
    base_profiles = ("balanced", "durable", "video", "invisible", "benchmark")
    if metadata_profile in base_profiles:
        if deep_scan:
            profiles = (metadata_profile,) + tuple(profile for profile in base_profiles if profile != metadata_profile)
        else:
            profiles = (metadata_profile,)
    elif deep_scan:
        profiles = ("video", "durable", "balanced", "benchmark", "invisible")
    else:
        profiles = base_profiles

    def robust_result_to_extract(robust) -> ExtractResult:
        return ExtractResult(
            payload=robust.payload,
            repeat=0,
            payload_bytes=AUTH_PACKET_LEN,
            algorithm="image-iwm2-local",
            profile=robust.profile,
            tiles_total=robust.tiles_total,
            tiles_checked=robust.tiles_checked,
            tiles_verified=robust.tiles_verified,
            bit_error_estimate=robust.bit_error_estimate,
            confidence=robust.confidence,
        )

    for profile_name in profiles:
        try:
            return robust_result_to_extract(
                extract_iwm2_image(input_path, password, profile=profile_name, deep_scan=deep_scan)
            )
        except Exception as exc:
            robust_error = exc
    try:
        return extract_image_legacy(input_path, password)
    except Exception:
        if not deep_scan:
            raise robust_error or PayloadError("No valid invisible watermark could be recovered.")
    for profile_name in profiles:
        try:
            return robust_result_to_extract(
                extract_iwm2_image(input_path, password, profile=profile_name, deep_scan=True)
            )
        except Exception as exc:
            robust_error = exc
    raise robust_error or PayloadError("No authenticated IWM2 local robust watermark could be recovered.")
