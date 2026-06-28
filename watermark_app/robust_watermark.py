from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import pywt
from PIL import Image, PngImagePlugin
from skimage.metrics import peak_signal_noise_ratio, structural_similarity

from .paths import unique_output_path
from .payload import (
    AUTH_PACKET_LEN,
    MICRO_AUTH_PACKET_LEN,
    Payload,
    PayloadError,
    build_micro_auth_packet_bytes,
    bits_to_bytes,
    bytes_to_bits,
    build_auth_packet_bytes,
    parse_auth_packet_bytes,
    parse_micro_auth_packet_bytes,
    position_seed,
)


SLOT_PAIRS = [((2, 3), (3, 2)), ((1, 4), (4, 1))]
DATA_BITS_PER_CODEWORD = 11
HAMMING_BITS_PER_CODEWORD = 15


@dataclass(frozen=True)
class ProfileConfig:
    name: str
    delta: float
    tile_size: int
    stride: int
    max_tiles: int
    min_tiles: int
    psnr_target: float
    ssim_target: float
    paper_diff_target: float
    scales: tuple[float, ...]
    method: str = "qim"


PROFILES: dict[str, ProfileConfig] = {
    "invisible": ProfileConfig("invisible", 6.5, 128, 64, 14, 4, 48.0, 0.992, 0.65, (1.0, 0.85, 0.70, 0.55, 0.45)),
    "balanced": ProfileConfig("balanced", 6.5, 128, 64, 22, 6, 44.0, 0.988, 0.90, (1.0, 0.85, 0.70, 0.55, 0.45)),
    "durable": ProfileConfig("durable", 20.0, 256, 128, 18, 4, 38.0, 0.975, 1.80, (1.0, 0.90, 0.80, 0.70), "compare"),
    "benchmark": ProfileConfig("benchmark", 12.0, 256, 128, 16, 4, 40.0, 0.980, 1.60, (1.0, 0.90, 0.75), "compare"),
}

STRENGTH_TO_PROFILE = {
    "subtle": "invisible",
    "balanced": "balanced",
    "strong": "durable",
}


@dataclass
class RobustEmbedResult:
    output_path: Path
    watermark_id: str
    core_text: str
    profile: str
    quality_psnr: float
    quality_ssim: float
    paper_diff: float
    tiles_total: int
    tiles_used: int
    payload_bytes: int


@dataclass
class RobustExtractResult:
    payload: Payload
    profile: str
    tiles_total: int
    tiles_checked: int
    tiles_verified: int
    bit_error_estimate: float
    confidence: float


@dataclass(frozen=True)
class Tile:
    x: int
    y: int
    size: int
    score: float
    kind: str = "grid"


def normalize_profile(profile: str | None = None, strength: str | None = None) -> str:
    if profile:
        profile = profile.lower()
        if profile in PROFILES:
            return profile
        if profile in STRENGTH_TO_PROFILE:
            return STRENGTH_TO_PROFILE[profile]
        if profile == "legacy":
            return profile
    strength_name = (strength or "balanced").lower()
    return STRENGTH_TO_PROFILE.get(strength_name, "balanced")


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


def _hamming_encode_word(data_bits: list[int]) -> list[int]:
    code = [0] * 16
    cursor = 0
    for position in range(1, 16):
        if position in {1, 2, 4, 8}:
            continue
        code[position] = int(data_bits[cursor]) if cursor < len(data_bits) else 0
        cursor += 1
    for parity in (1, 2, 4, 8):
        value = 0
        for position in range(1, 16):
            if position & parity:
                value ^= code[position]
        code[parity] = value
    return code[1:]


def _hamming_decode_word(code_bits: list[int]) -> tuple[list[int], int]:
    code = [0] + [int(bit) for bit in code_bits[:15]]
    syndrome = 0
    for parity in (1, 2, 4, 8):
        value = 0
        for position in range(1, 16):
            if position & parity:
                value ^= code[position]
        if value:
            syndrome += parity
    corrected = 0
    if 1 <= syndrome <= 15:
        code[syndrome] ^= 1
        corrected = 1
    data = [code[position] for position in range(1, 16) if position not in {1, 2, 4, 8}]
    return data, corrected


def _hamming_encode(bits: list[int]) -> list[int]:
    out: list[int] = []
    for index in range(0, len(bits), DATA_BITS_PER_CODEWORD):
        out.extend(_hamming_encode_word(bits[index : index + DATA_BITS_PER_CODEWORD]))
    return out


def _hamming_decode(bits: list[int], expected_data_bits: int) -> tuple[list[int], int]:
    out: list[int] = []
    corrections = 0
    for index in range(0, len(bits), HAMMING_BITS_PER_CODEWORD):
        word = bits[index : index + HAMMING_BITS_PER_CODEWORD]
        if len(word) < HAMMING_BITS_PER_CODEWORD:
            break
        decoded, corrected = _hamming_decode_word(word)
        out.extend(decoded)
        corrections += corrected
    return out[:expected_data_bits], corrections


@lru_cache(maxsize=256)
def _permutation(length: int, password: str) -> list[int]:
    seed = position_seed(password).to_bytes(8, "big")
    return sorted(
        range(length),
        key=lambda index: hashlib.sha256(seed + index.to_bytes(4, "big")).digest(),
    )


def _interleave(bits: list[int], password: str) -> list[int]:
    perm = _permutation(len(bits), password)
    return [bits[index] for index in perm]


def _deinterleave(bits: list[int], password: str) -> list[int]:
    perm = _permutation(len(bits), password)
    out = [0] * len(bits)
    for slot_index, original_index in enumerate(perm):
        out[original_index] = bits[slot_index]
    return out


def encoded_auth_bits(packet: bytes, password: str) -> list[int]:
    bits = bytes_to_bits(packet)
    return _interleave(_hamming_encode(bits), password)


def _decode_auth_bits(bits: list[int], password: str, packet_len: int = AUTH_PACKET_LEN) -> tuple[bytes, int]:
    expected_bits = packet_len * 8
    deinterleaved = _deinterleave(bits, password)
    decoded, corrections = _hamming_decode(deinterleaved, expected_bits)
    return bits_to_bytes(decoded), corrections


def _slot_count_for_tile(tile_size: int) -> int:
    band_size = tile_size // 2
    blocks = (band_size // 8) * (band_size // 8)
    return blocks * len(SLOT_PAIRS) * 3


def _local_slot_score(tile: np.ndarray | None, bx: int, by: int) -> int:
    if tile is None:
        return 0
    x0 = max(0, min(tile.shape[1] - 1, bx * 2))
    y0 = max(0, min(tile.shape[0] - 1, by * 2))
    patch = tile[y0 : min(tile.shape[0], y0 + 16), x0 : min(tile.shape[1], x0 + 16)]
    if patch.size == 0:
        return 0
    std = float(np.std(patch))
    if std < 1.0:
        return 0
    sobel_x = cv2.Sobel(patch.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(patch.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    edge = float(np.mean(np.sqrt(sobel_x * sobel_x + sobel_y * sobel_y)))
    flat_penalty = 8.0 if float(np.mean(patch > 245)) > 0.80 or float(np.mean(patch < 10)) > 0.80 else 0.0
    raw = max(0.0, std * 0.8 + edge * 0.2 - flat_penalty)
    return int(min(15, raw // 2))


@lru_cache(maxsize=128)
def _ordered_slots_cached(
    tile_size: int,
    password: str,
    low_frequency_first: bool,
) -> tuple[tuple[int, int, int, int], ...]:
    band_size = tile_size // 2
    slots: list[tuple[int, int, int, int]] = []
    for band_index in range(3):
        for by in range(0, band_size - 7, 8):
            for bx in range(0, band_size - 7, 8):
                for pair_index in range(len(SLOT_PAIRS)):
                    slots.append((band_index, bx, by, pair_index))
    seed = position_seed(password).to_bytes(8, "big")
    band_priority = {2: 0, 0: 1, 1: 2} if low_frequency_first else {0: 0, 1: 1, 2: 2}
    ordered = sorted(
        slots,
        key=lambda slot: (
            band_priority.get(slot[0], 9),
            hashlib.sha256(seed + bytes([slot[0], slot[3]]) + slot[1].to_bytes(2, "big") + slot[2].to_bytes(2, "big")).digest(),
        ),
    )
    return tuple(ordered)


@lru_cache(maxsize=128)
def _band_slots_cached(
    tile_size: int,
    password: str,
    band_index: int,
) -> tuple[tuple[int, int, int, int], ...]:
    band_size = tile_size // 2
    slots: list[tuple[int, int, int, int]] = []
    for by in range(0, band_size - 7, 8):
        for bx in range(0, band_size - 7, 8):
            for pair_index in range(len(SLOT_PAIRS)):
                slots.append((band_index, bx, by, pair_index))
    seed = position_seed(password).to_bytes(8, "big")
    ordered = sorted(
        slots,
        key=lambda slot: hashlib.sha256(
            seed + b"band" + bytes([slot[0], slot[3]]) + slot[1].to_bytes(2, "big") + slot[2].to_bytes(2, "big")
        ).digest(),
    )
    return tuple(ordered)


def _ordered_slots(
    tile_size: int,
    password: str,
    tile: np.ndarray | None = None,
    low_frequency_first: bool = True,
) -> list[tuple[int, int, int, int]]:
    _ = tile
    return list(_ordered_slots_cached(tile_size, password, low_frequency_first))


def _set_bit_qim(coeff: np.ndarray, bit: int, pair_index: int, delta: float) -> None:
    first, second = SLOT_PAIRS[pair_index]
    step = max(0.1, abs(float(delta)))
    diff = float(coeff[first] - coeff[second])
    q = int(np.rint(diff / step))
    desired_parity = int(bit) & 1
    if (q & 1) != desired_parity:
        lower = q - 1
        upper = q + 1
        q = lower if abs(diff - lower * step) <= abs(diff - upper * step) else upper
    target = q * step
    adjust = (target - diff) / 2.0
    coeff[first] = coeff[first] + adjust
    coeff[second] = coeff[second] - adjust


def _read_bit_qim(coeff: np.ndarray, pair_index: int, delta: float) -> int:
    first, second = SLOT_PAIRS[pair_index]
    step = max(0.1, abs(float(delta)))
    q = int(np.rint(float(coeff[first] - coeff[second]) / step))
    return q & 1


def _set_bit_compare(coeff: np.ndarray, bit: int, pair_index: int, delta: float) -> None:
    first, second = SLOT_PAIRS[pair_index]
    a = coeff[first]
    b = coeff[second]
    target = abs(float(delta))
    if bit:
        diff = a - b
        if diff < target:
            adjust = (target - diff) / 2
            coeff[first] = a + adjust
            coeff[second] = b - adjust
    else:
        diff = b - a
        if diff < target:
            adjust = (target - diff) / 2
            coeff[first] = a - adjust
            coeff[second] = b + adjust


def _read_bit_compare(coeff: np.ndarray, pair_index: int) -> int:
    first, second = SLOT_PAIRS[pair_index]
    return 1 if coeff[first] > coeff[second] else 0


def _set_bit(coeff: np.ndarray, bit: int, pair_index: int, delta: float, method: str) -> None:
    if method in {"compare", "compare-replicated"}:
        _set_bit_compare(coeff, bit, pair_index, delta)
    else:
        _set_bit_qim(coeff, bit, pair_index, delta)


def _read_bit(coeff: np.ndarray, pair_index: int, delta: float, method: str) -> int:
    if method in {"compare", "compare-replicated"}:
        return _read_bit_compare(coeff, pair_index)
    return _read_bit_qim(coeff, pair_index, delta)


def _tile_score(y_array: np.ndarray, alpha_array: np.ndarray | None, x: int, y: int, size: int) -> float:
    tile = y_array[y : y + size, x : x + size]
    if tile.shape != (size, size):
        return -1.0
    if alpha_array is not None:
        alpha = alpha_array[y : y + size, x : x + size]
        if alpha.size and float(np.mean(alpha < 16)) > 0.25:
            return -1.0
    std = float(np.std(tile))
    sobel_x = cv2.Sobel(tile.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(tile.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    edge = float(np.mean(np.sqrt(sobel_x * sobel_x + sobel_y * sobel_y)))
    flat_white = float(np.mean(tile > 245))
    flat_black = float(np.mean(tile < 10))
    penalty = 20.0 * max(flat_white - 0.72, 0.0) + 20.0 * max(flat_black - 0.72, 0.0)
    return std * 0.75 + edge * 0.25 - penalty


def _candidate_tiles(
    y_array: np.ndarray,
    alpha_array: np.ndarray | None,
    config: ProfileConfig,
    scan_offsets: bool = False,
    max_tiles: int | None = None,
) -> list[Tile]:
    height, width = y_array.shape
    sizes = [config.tile_size]
    if min(width, height) < config.tile_size and min(width, height) >= 128:
        sizes = [128]
    tiles: list[Tile] = []
    seen: set[tuple[int, int, int]] = set()
    for size in sizes:
        if width < size or height < size or _slot_count_for_tile(size) < _encoded_bit_count():
            continue
        if scan_offsets:
            feature_tiles = _feature_tiles(
                y_array,
                alpha_array,
                size,
                max(config.max_tiles * 3, 80),
                min_distance=max(24, size // 2),
            )
            for tile in feature_tiles:
                key = (tile.x, tile.y, tile.size)
                if key not in seen:
                    seen.add(key)
                    tiles.append(tile)
            for tile in feature_tiles[:24]:
                for dy in (-8, 0, 8):
                    for dx in (-8, 0, 8):
                        if dx == 0 and dy == 0:
                            continue
                        x = max(0, min(width - size, tile.x + dx))
                        y = max(0, min(height - size, tile.y + dy))
                        key = (x, y, size)
                        if key in seen:
                            continue
                        score = _tile_score(y_array, alpha_array, x, y, size)
                        if score >= 0:
                            seen.add(key)
                            tiles.append(Tile(x, y, size, score + 0.5, "feature"))
        step = max(4, size // 64) if scan_offsets else config.stride
        offsets = range(0, size, step) if scan_offsets else (0,)
        for oy in offsets:
            for ox in offsets:
                for y in range(oy, height - size + 1, config.stride):
                    for x in range(ox, width - size + 1, config.stride):
                        key = (x, y, size)
                        if key in seen:
                            continue
                        score = _tile_score(y_array, alpha_array, x, y, size)
                        if score >= 0:
                            seen.add(key)
                            tiles.append(Tile(x, y, size, score))
    if not tiles:
        return []
    limit = max_tiles if max_tiles is not None else config.max_tiles
    if scan_offsets:
        feature_tiles = [tile for tile in tiles if tile.kind == "feature"]
        grid_tiles = sorted((tile for tile in tiles if tile.kind != "feature"), key=lambda tile: tile.score, reverse=True)
        phase_groups: dict[tuple[int, int, int], list[Tile]] = {}
        for tile in grid_tiles:
            phase_groups.setdefault((tile.x % config.stride, tile.y % config.stride, tile.size), []).append(tile)
        phase_tiles: list[Tile] = []
        for group in phase_groups.values():
            phase_tiles.extend(group[:8])

        merged: list[Tile] = []
        merged_seen: set[tuple[int, int, int]] = set()
        for tile in feature_tiles + phase_tiles + grid_tiles:
            key = (tile.x, tile.y, tile.size)
            if key in merged_seen:
                continue
            merged_seen.add(key)
            merged.append(tile)
            if len(merged) >= limit:
                break
        return merged

    tiles = sorted(tiles, key=lambda tile: tile.score, reverse=True)
    if not scan_offsets:
        positive = [tile for tile in tiles if tile.score >= 3.5]
        if len(positive) >= config.min_tiles:
            supplemental = [tile for tile in tiles if tile.score < 3.5]
            return (positive + supplemental)[:limit]
    return tiles[:limit]


def _feature_tiles(
    y_array: np.ndarray,
    alpha_array: np.ndarray | None,
    size: int,
    max_features: int,
    min_distance: int = 0,
    border_margin: int = 0,
) -> list[Tile]:
    if y_array.shape[0] < size or y_array.shape[1] < size:
        return []
    image = np.clip(y_array, 0, 255).astype(np.uint8)
    orb = cv2.ORB_create(nfeatures=max(120, max_features * 2), fastThreshold=9)
    keypoints = orb.detect(image, None)
    tiles: list[Tile] = []
    deferred: list[Tile] = []
    seen: set[tuple[int, int]] = set()

    def too_close(tile: Tile) -> bool:
        if min_distance <= 0:
            return False
        cx = tile.x + tile.size / 2
        cy = tile.y + tile.size / 2
        for existing in tiles:
            ex = existing.x + existing.size / 2
            ey = existing.y + existing.size / 2
            if math.hypot(cx - ex, cy - ey) < min_distance:
                return True
        return False

    for keypoint in sorted(keypoints, key=lambda item: item.response, reverse=True):
        x = int(round(float(keypoint.pt[0]) - size / 2))
        y = int(round(float(keypoint.pt[1]) - size / 2))
        x = max(0, min(y_array.shape[1] - size, x))
        y = max(0, min(y_array.shape[0] - size, y))
        x = int(round(x / 4) * 4)
        y = int(round(y / 4) * 4)
        if border_margin:
            if (
                x < border_margin
                or y < border_margin
                or x + size > y_array.shape[1] - border_margin
                or y + size > y_array.shape[0] - border_margin
            ):
                continue
        key = (x, y)
        if key in seen:
            continue
        score = _tile_score(y_array, alpha_array, x, y, size)
        if score < 0:
            continue
        seen.add(key)
        tile = Tile(x, y, size, score + min(2.0, float(keypoint.response) * 2.0), "feature")
        if too_close(tile):
            deferred.append(tile)
            continue
        tiles.append(tile)
        if len(tiles) >= max_features:
            break
    for tile in deferred:
        if len(tiles) >= max_features:
            break
        tiles.append(tile)
    return tiles


def _select_embed_tiles(
    y_array: np.ndarray,
    alpha_array: np.ndarray | None,
    config: ProfileConfig,
    profile_name: str,
) -> tuple[list[Tile], int]:
    grid_tiles = _candidate_tiles(y_array, alpha_array, config)
    if len(grid_tiles) < config.min_tiles:
        raise PayloadError(
            f"Not enough textured IWM2 tiles were found ({len(grid_tiles)} found, {config.min_tiles} required)."
        )

    def profile_order(tiles: list[Tile]) -> list[Tile]:
        if profile_name in {"durable", "benchmark"}:
            margin = max(24, int(min(y_array.shape) * 0.06))

            def near_border(tile: Tile) -> bool:
                return (
                    tile.x < margin
                    or tile.y < margin
                    or tile.x + tile.size > y_array.shape[1] - margin
                    or tile.y + tile.size > y_array.shape[0] - margin
                )

            return sorted(tiles, key=lambda tile: (near_border(tile), tile.score > 34.0, -tile.score))
        return tiles

    feature_budget = 0
    if profile_name in {"balanced", "durable", "benchmark"}:
        if profile_name in {"durable", "benchmark"}:
            feature_budget = min(8, max(4, config.max_tiles // 2))
        else:
            feature_budget = min(6, max(3, config.max_tiles // 3))
    if profile_name in {"durable", "benchmark"}:
        grid_budget = config.min_tiles
    else:
        grid_budget = max(config.min_tiles, config.max_tiles - feature_budget)

    selected: list[Tile] = []
    seen: set[tuple[int, int, int]] = set()
    overlap_limit = 0.40 if profile_name in {"durable", "benchmark"} else 0.65

    def overlap_ratio(first: Tile, second: Tile) -> float:
        x0 = max(first.x, second.x)
        y0 = max(first.y, second.y)
        x1 = min(first.x + first.size, second.x + second.size)
        y1 = min(first.y + first.size, second.y + second.size)
        if x1 <= x0 or y1 <= y0:
            return 0.0
        return ((x1 - x0) * (y1 - y0)) / float(min(first.size, second.size) ** 2)

    def add_tile(tile: Tile, allow_overlap: bool = False) -> None:
        key = (tile.x, tile.y, tile.size)
        if key in seen or len(selected) >= config.max_tiles:
            return
        if not allow_overlap and any(overlap_ratio(tile, existing) > overlap_limit for existing in selected):
            return
        seen.add(key)
        selected.append(tile)

    ordered_grid_tiles = profile_order(grid_tiles)
    for tile in ordered_grid_tiles[:grid_budget]:
        add_tile(tile)

    if feature_budget:
        feature_tiles = _feature_tiles(
            y_array,
            alpha_array,
            config.tile_size,
            max(feature_budget * 4, 24),
            min_distance=max(24, config.tile_size // 2),
            border_margin=max(24, int(min(y_array.shape) * 0.06)),
        )
        feature_tiles = profile_order(feature_tiles)
        added_features = 0
        for tile in feature_tiles:
            if added_features >= feature_budget or len(selected) >= config.max_tiles:
                break
            before = len(selected)
            add_tile(tile)
            if len(selected) > before:
                added_features += 1

    if profile_name not in {"durable", "benchmark"}:
        for tile in ordered_grid_tiles[grid_budget:]:
            if len(selected) >= config.max_tiles:
                break
            add_tile(tile)
    else:
        durable_target_tiles = min(config.max_tiles, 12)
        for tile in ordered_grid_tiles[grid_budget:]:
            if len(selected) >= durable_target_tiles:
                break
            add_tile(tile)

    if len(selected) < config.min_tiles:
        for tile in ordered_grid_tiles[grid_budget:]:
            if len(selected) >= config.min_tiles:
                break
            add_tile(tile)
    if len(selected) < config.min_tiles:
        for tile in ordered_grid_tiles:
            if len(selected) >= config.min_tiles:
                break
            add_tile(tile, allow_overlap=True)
    if len(selected) < config.min_tiles:
        raise PayloadError(
            f"Not enough usable IWM2 tiles were selected ({len(selected)} selected, {config.min_tiles} required)."
        )
    return selected, len(grid_tiles)


def _synchronized_grid_tiles(
    y_array: np.ndarray,
    alpha_array: np.ndarray | None,
    config: ProfileConfig,
    anchors: list[Tile],
    max_tiles: int,
) -> list[Tile]:
    height, width = y_array.shape
    size = config.tile_size
    if width < size or height < size:
        return []
    tiles: list[Tile] = []
    seen_tiles: set[tuple[int, int, int]] = set()
    seen_offsets: set[tuple[int, int]] = set()
    for anchor in anchors:
        ox = anchor.x % config.stride
        oy = anchor.y % config.stride
        offset_key = (ox, oy)
        if offset_key in seen_offsets:
            continue
        seen_offsets.add(offset_key)
        for y in range(oy, height - size + 1, config.stride):
            for x in range(ox, width - size + 1, config.stride):
                key = (x, y, size)
                if key in seen_tiles:
                    continue
                score = _tile_score(y_array, alpha_array, x, y, size)
                if score >= 0:
                    seen_tiles.add(key)
                    tiles.append(Tile(x, y, size, score, "sync"))
    return sorted(tiles, key=lambda tile: tile.score, reverse=True)[:max_tiles]


def _encoded_bit_count(packet_len: int = AUTH_PACKET_LEN) -> int:
    return math.ceil((packet_len * 8) / DATA_BITS_PER_CODEWORD) * HAMMING_BITS_PER_CODEWORD


def _split_bands(tile: np.ndarray) -> tuple[np.ndarray, list[np.ndarray]]:
    c_a, (c_h, c_v, c_d) = pywt.dwt2(tile, "haar")
    return c_a, [c_h, c_v, c_a.copy(), c_d]


def _merge_bands(original_c_a: np.ndarray, bands: list[np.ndarray]) -> np.ndarray:
    c_h, c_v, c_a_marked, c_d = bands
    _ = original_c_a
    return pywt.idwt2((c_a_marked, (c_h, c_v, c_d)), "haar")


def _embed_bits_in_tile(
    tile: np.ndarray,
    bits: list[int],
    password: str,
    delta: float,
    method: str,
    low_frequency_first: bool,
) -> np.ndarray:
    tile_size = tile.shape[0]
    c_a, bands = _split_bands(tile)
    if method == "compare":
        slots = _band_slots_cached(tile_size, password, 2)
        if len(bits) > len(slots):
            raise PayloadError("IWM2 tile capacity is too small for the authentication packet.")
        for bit, (band_index, bx, by, pair_index) in zip(bits, slots):
            band = bands[band_index]
            block = band[by : by + 8, bx : bx + 8]
            coeff = _block_dct(block)
            _set_bit(coeff, bit, pair_index, delta, method)
            band[by : by + 8, bx : bx + 8] = _block_idct(coeff)
        merged = _merge_bands(c_a, bands)
        return merged[:tile_size, :tile_size]

    if method == "compare-replicated":
        band_slots = [_band_slots_cached(tile_size, password, band_index) for band_index in (2, 0, 1)]
        if all(len(slots) >= len(bits) for slots in band_slots):
            for bit_index, bit in enumerate(bits):
                for slots in band_slots:
                    band_index, bx, by, pair_index = slots[bit_index]
                    band = bands[band_index]
                    block = band[by : by + 8, bx : bx + 8]
                    coeff = _block_dct(block)
                    _set_bit(coeff, bit, pair_index, delta, method)
                    band[by : by + 8, bx : bx + 8] = _block_idct(coeff)
            merged = _merge_bands(c_a, bands)
            return merged[:tile_size, :tile_size]

    slots = _ordered_slots(tile_size, password, tile, low_frequency_first)
    if len(bits) > len(slots):
        raise PayloadError("IWM2 tile capacity is too small for the authentication packet.")
    for bit, (band_index, bx, by, pair_index) in zip(bits, slots):
        band = bands[band_index]
        block = band[by : by + 8, bx : bx + 8]
        coeff = _block_dct(block)
        _set_bit(coeff, bit, pair_index, delta, method)
        band[by : by + 8, bx : bx + 8] = _block_idct(coeff)
    merged = _merge_bands(c_a, bands)
    return merged[:tile_size, :tile_size]


def _read_bits_from_tile(
    tile: np.ndarray,
    bit_count: int,
    password: str,
    delta: float,
    method: str,
    low_frequency_first: bool,
) -> list[int]:
    tile_size = tile.shape[0]
    _, bands = _split_bands(tile)
    if method == "compare":
        slots = _band_slots_cached(tile_size, password, 2)
        if bit_count > len(slots):
            raise PayloadError("IWM2 tile capacity is too small for recovery.")
        recovered: list[int] = []
        for band_index, bx, by, pair_index in slots[:bit_count]:
            band = bands[band_index]
            block = band[by : by + 8, bx : bx + 8]
            coeff = _block_dct(block)
            recovered.append(_read_bit(coeff, pair_index, delta, method))
        return recovered

    if method == "compare-replicated":
        band_slots = [_band_slots_cached(tile_size, password, band_index) for band_index in (2, 0, 1)]
        if all(len(slots) >= bit_count for slots in band_slots):
            recovered: list[int] = []
            for bit_index in range(bit_count):
                votes = 0
                for slots in band_slots:
                    band_index, bx, by, pair_index = slots[bit_index]
                    band = bands[band_index]
                    block = band[by : by + 8, bx : bx + 8]
                    coeff = _block_dct(block)
                    votes += _read_bit(coeff, pair_index, delta, method)
                recovered.append(1 if votes >= 2 else 0)
            return recovered

    slots = _ordered_slots(tile_size, password, tile, low_frequency_first)
    if bit_count > len(slots):
        raise PayloadError("IWM2 tile capacity is too small for recovery.")
    recovered: list[int] = []
    for band_index, bx, by, pair_index in slots[:bit_count]:
        band = bands[band_index]
        block = band[by : by + 8, bx : bx + 8]
        coeff = _block_dct(block)
        recovered.append(_read_bit(coeff, pair_index, delta, method))
    return recovered


def _dark_pixel_coords(tile: np.ndarray, bit_count: int, password: str, salt: str) -> np.ndarray | None:
    mask = np.argwhere((tile > 8) & (tile < 185))
    if len(mask) < bit_count * 2:
        return None
    seed = hashlib.sha256((password + ":" + salt).encode("utf-8")).digest()
    order = sorted(
        range(len(mask)),
        key=lambda index: hashlib.sha256(seed + index.to_bytes(4, "big")).digest(),
    )
    return mask[order[: bit_count * 2]]


def _embed_dark_bits_in_tile(
    tile: np.ndarray,
    bits: list[int],
    password: str,
    salt: str,
    delta: float = 10.0,
) -> tuple[np.ndarray, bool]:
    coords = _dark_pixel_coords(tile, len(bits), password, salt)
    if coords is None:
        return tile, False
    out = tile.copy()
    for index, bit in enumerate(bits):
        y1, x1 = coords[index * 2]
        y2, x2 = coords[index * 2 + 1]
        midpoint = max(18.0, min(170.0, float(out[y1, x1] + out[y2, x2]) / 2.0))
        if bit:
            out[y1, x1] = max(0.0, midpoint - delta / 2.0)
            out[y2, x2] = min(255.0, midpoint + delta / 2.0)
        else:
            out[y1, x1] = min(255.0, midpoint + delta / 2.0)
            out[y2, x2] = max(0.0, midpoint - delta / 2.0)
    return out, True


def _read_dark_bits_from_tile(
    tile: np.ndarray,
    bit_count: int,
    password: str,
    salt: str,
) -> list[int]:
    coords = _dark_pixel_coords(tile, bit_count, password, salt)
    if coords is None:
        raise PayloadError("IWM2 dark-stroke tile has too few stable dark pixels.")
    recovered: list[int] = []
    for index in range(bit_count):
        y1, x1 = coords[index * 2]
        y2, x2 = coords[index * 2 + 1]
        recovered.append(1 if tile[y1, x1] < tile[y2, x2] else 0)
    return recovered


def image_quality_metrics(original: Image.Image, marked: Image.Image) -> dict[str, float]:
    original_rgb = np.asarray(original.convert("RGB"), dtype=np.uint8)
    marked_rgb = np.asarray(marked.convert("RGB"), dtype=np.uint8)
    psnr = float(peak_signal_noise_ratio(original_rgb, marked_rgb, data_range=255))
    ssim = float(structural_similarity(original_rgb, marked_rgb, channel_axis=2, data_range=255))
    orig_y = np.asarray(original.convert("L"), dtype=np.float32)
    mark_y = np.asarray(marked.convert("L"), dtype=np.float32)
    diff = np.abs(orig_y - mark_y)
    sobel_x = cv2.Sobel(orig_y, cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(orig_y, cv2.CV_32F, 0, 1, ksize=3)
    edge = np.sqrt(sobel_x * sobel_x + sobel_y * sobel_y)
    paper_mask = (orig_y > 235) & (edge < 3.0)
    paper_diff = float(np.mean(diff[paper_mask])) if np.any(paper_mask) else 0.0
    return {
        "psnr": psnr,
        "ssim": ssim,
        "paper_diff": paper_diff,
        "max_diff": float(np.max(diff)) if diff.size else 0.0,
    }


def _self_verified_tiles(
    y_array: np.ndarray,
    selected: list[Tile],
    password: str,
    delta: float,
    method: str,
    packet_len: int = AUTH_PACKET_LEN,
) -> int:
    encoded_count = _encoded_bit_count(packet_len)
    quantized = np.clip(y_array, 0, 255).astype(np.uint8).astype(np.float32)
    verified = 0
    for tile in selected:
        try:
            tile_data = quantized[tile.y : tile.y + tile.size, tile.x : tile.x + tile.size]
            bits = _read_bits_from_tile(tile_data, encoded_count, password, delta, method, True)
            packet, _ = _decode_auth_bits(bits, password, packet_len)
            if packet_len == MICRO_AUTH_PACKET_LEN:
                parse_micro_auth_packet_bytes(packet, password)
            else:
                parse_auth_packet_bytes(packet, password)
            verified += 1
        except Exception:
            continue
    return verified


def embed_iwm2_packet_into_image(
    input_path: str | Path,
    output_dir: str | Path,
    packet: bytes,
    password: str,
    profile: str = "balanced",
    output_name: str | None = None,
    watermark_id: str = "",
    core_text: str = "",
) -> RobustEmbedResult:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_name = normalize_profile(profile)
    if profile_name == "legacy":
        profile_name = "balanced"
    config = PROFILES[profile_name]
    low_frequency_first = True
    carrier_packet = packet
    if profile_name in {"durable", "benchmark"} and watermark_id:
        carrier_packet = build_micro_auth_packet_bytes(watermark_id, password)
    carrier_packet_len = len(carrier_packet)
    encoded_bits = encoded_auth_bits(carrier_packet, password)

    image = Image.open(input_path)
    alpha = None
    alpha_array = None
    if image.mode in {"RGBA", "LA"}:
        alpha = image.getchannel("A")
        alpha_array = np.asarray(alpha, dtype=np.uint8)
    ycbcr = image.convert("YCbCr")
    y, cb, cr = ycbcr.split()
    original_y = np.asarray(y, dtype=np.float32)
    selected, tiles_total = _select_embed_tiles(original_y, alpha_array, config, profile_name)
    best_image: Image.Image | None = None
    best_metrics: dict[str, float] | None = None
    best_scale = config.scales[-1]
    fallback_image: Image.Image | None = None
    fallback_metrics: dict[str, float] | None = None
    fallback_scale = config.scales[0]
    min_self_verified = 2
    if profile_name in {"invisible", "durable", "benchmark"}:
        min_self_verified = 1

    for scale in config.scales:
        y_array = original_y.copy()
        for tile in selected:
            tile_data = y_array[tile.y : tile.y + tile.size, tile.x : tile.x + tile.size]
            marked_tile = _embed_bits_in_tile(
                tile_data,
                encoded_bits,
                password,
                config.delta * scale,
                config.method,
                low_frequency_first,
            )
            if profile_name in {"durable", "benchmark"} and carrier_packet_len == MICRO_AUTH_PACKET_LEN:
                marked_tile, _ = _embed_dark_bits_in_tile(
                    marked_tile,
                    encoded_bits,
                    password,
                    f"{tile.x},{tile.y},{tile.size}",
                    delta=10.0,
                )
            y_array[tile.y : tile.y + tile.size, tile.x : tile.x + tile.size] = marked_tile
        y_marked = Image.fromarray(np.clip(y_array, 0, 255).astype(np.uint8), "L")
        marked = Image.merge("YCbCr", (y_marked, cb, cr)).convert("RGB")
        if alpha is not None:
            marked.putalpha(alpha)
        metrics = image_quality_metrics(image, marked)
        verified = _self_verified_tiles(
            y_array,
            selected,
            password,
            config.delta * scale,
            config.method,
            carrier_packet_len,
        )
        if verified >= min_self_verified:
            fallback_image = marked
            fallback_metrics = metrics
            fallback_scale = scale
        else:
            continue
        if (
            metrics["psnr"] >= config.psnr_target
            and metrics["ssim"] >= config.ssim_target
            and metrics["paper_diff"] <= config.paper_diff_target
        ):
            best_image = marked
            best_metrics = metrics
            best_scale = scale
            break

    if best_image is None or best_metrics is None:
        if fallback_image is None or fallback_metrics is None:
            raise PayloadError("IWM2 embedding did not produce a self-verifiable output image.")
        best_image = fallback_image
        best_metrics = fallback_metrics
        best_scale = fallback_scale
    suffix = input_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        suffix = ".png"
    output_path = output_dir / output_name if output_name else output_dir / f"{input_path.stem}_wm{suffix}"
    output_path = unique_output_path(output_path)
    if output_path.suffix.lower() == ".png":
        pnginfo = PngImagePlugin.PngInfo()
        pnginfo.add_text("iwm_algorithm", "iwm2-local")
        pnginfo.add_text("iwm_profile", profile_name)
        pnginfo.add_text("iwm_watermark_id", watermark_id)
        best_image.save(output_path, pnginfo=pnginfo)
    else:
        best_image.save(output_path)
    return RobustEmbedResult(
        output_path=output_path,
        watermark_id=watermark_id,
        core_text=core_text,
        profile=f"{profile_name}@{best_scale:.2f}",
        quality_psnr=best_metrics["psnr"],
        quality_ssim=best_metrics["ssim"],
        paper_diff=best_metrics["paper_diff"],
        tiles_total=tiles_total,
        tiles_used=len(selected),
        payload_bytes=carrier_packet_len,
    )


def embed_iwm2_image(
    input_path: str | Path,
    output_dir: str | Path,
    text: str,
    password: str,
    profile: str = "balanced",
    source_sha256: str = "",
) -> RobustEmbedResult:
    packet, meta = build_auth_packet_bytes(text, password, source_sha256=source_sha256)
    return embed_iwm2_packet_into_image(
        input_path,
        output_dir,
        packet,
        password,
        profile=profile,
        watermark_id=str(meta["id"]),
        core_text=str(meta["core_text"]),
    )


def extract_iwm2_image(
    input_path: str | Path,
    password: str,
    profile: str = "balanced",
    deep_scan: bool = False,
) -> RobustExtractResult:
    input_path = Path(input_path)
    profile_name = normalize_profile(profile)
    if profile_name == "legacy":
        profile_name = "balanced"
    config = PROFILES[profile_name]
    image = Image.open(input_path).convert("YCbCr")
    y_array = np.asarray(image.getchannel("Y"), dtype=np.float32)
    candidates = _candidate_tiles(y_array, None, config, scan_offsets=False, max_tiles=48)
    if profile_name in {"balanced", "durable", "benchmark"}:
        seen = {(tile.x, tile.y, tile.size) for tile in candidates}
        for tile in _feature_tiles(
            y_array,
            None,
            config.tile_size,
            max(24, config.max_tiles * 2),
            min_distance=max(24, config.tile_size // 2),
        ):
            key = (tile.x, tile.y, tile.size)
            if key not in seen:
                seen.add(key)
                candidates.append(tile)
    if not candidates:
        raise PayloadError("No candidate IWM2 tiles were available for recovery.")

    packet_modes: list[tuple[int, bool]] = []
    if profile_name in {"durable", "benchmark"}:
        packet_modes.append((MICRO_AUTH_PACKET_LEN, True))
    packet_modes.append((AUTH_PACKET_LEN, False))

    def scan_tiles(
        scan_candidates: list[Tile],
        packet_len: int,
        micro_packet: bool,
        stop_after_verified: int = 0,
    ) -> tuple[dict[str, tuple[Payload, int, int]], int, list[Tile]]:
        groups: dict[str, tuple[Payload, int, int]] = {}
        checked = 0
        verified_tiles: list[Tile] = []
        encoded_count = _encoded_bit_count(packet_len)
        for tile in scan_candidates:
            try:
                tile_data = y_array[tile.y : tile.y + tile.size, tile.x : tile.x + tile.size]
                scale_options = config.scales if config.method == "qim" else (1.0,)
                for scale in scale_options:
                    try:
                        bits = _read_bits_from_tile(
                            tile_data,
                            encoded_count,
                            password,
                            config.delta * scale,
                            config.method,
                            True,
                        )
                        packet, corrections = _decode_auth_bits(bits, password, packet_len)
                        payload = (
                            parse_micro_auth_packet_bytes(packet, password)
                            if micro_packet
                            else parse_auth_packet_bytes(packet, password)
                        )
                    except Exception:
                        continue
                    current = groups.get(payload.watermark_id)
                    if current:
                        groups[payload.watermark_id] = (payload, current[1] + 1, current[2] + corrections)
                    else:
                        groups[payload.watermark_id] = (payload, 1, corrections)
                    verified_tiles.append(tile)
                    break
                if groups and stop_after_verified and max(group[1] for group in groups.values()) >= stop_after_verified:
                    checked += 1
                    break
            except Exception:
                pass
            finally:
                if not (stop_after_verified and groups and max(group[1] for group in groups.values()) >= stop_after_verified):
                    checked += 1
        return groups, checked, verified_tiles

    def merge_groups(
        target: dict[str, tuple[Payload, int, int]],
        source: dict[str, tuple[Payload, int, int]],
    ) -> None:
        for watermark_id, (payload, verified, corrections) in source.items():
            current = target.get(watermark_id)
            if current:
                target[watermark_id] = (payload, current[1] + verified, current[2] + corrections)
            else:
                target[watermark_id] = (payload, verified, corrections)

    def scan_dark_tiles(
        scan_candidates: list[Tile],
        stop_after_verified: int = 0,
    ) -> tuple[dict[str, tuple[Payload, int, int]], int, list[Tile]]:
        groups: dict[str, tuple[Payload, int, int]] = {}
        checked = 0
        verified_tiles: list[Tile] = []
        packet_len = MICRO_AUTH_PACKET_LEN
        encoded_count = _encoded_bit_count(packet_len)
        for tile in scan_candidates:
            try:
                tile_data = y_array[tile.y : tile.y + tile.size, tile.x : tile.x + tile.size]
                bits = _read_dark_bits_from_tile(
                    tile_data,
                    encoded_count,
                    password,
                    f"{tile.x},{tile.y},{tile.size}",
                )
                packet, corrections = _decode_auth_bits(bits, password, packet_len)
                payload = parse_micro_auth_packet_bytes(packet, password)
                current = groups.get(payload.watermark_id)
                if current:
                    groups[payload.watermark_id] = (payload, current[1] + 1, current[2] + corrections)
                else:
                    groups[payload.watermark_id] = (payload, 1, corrections)
                verified_tiles.append(tile)
                if stop_after_verified and max(group[1] for group in groups.values()) >= stop_after_verified:
                    checked += 1
                    break
            except Exception:
                pass
            finally:
                if not (stop_after_verified and groups and max(group[1] for group in groups.values()) >= stop_after_verified):
                    checked += 1
        return groups, checked, verified_tiles

    groups: dict[str, tuple[Payload, int, int]] = {}
    checked = 0
    verified_tiles: list[Tile] = []
    used_packet_len = packet_modes[0][0]
    used_micro_packet = packet_modes[0][1]
    for packet_len, micro_packet in packet_modes:
        groups, checked, verified_tiles = scan_tiles(candidates, packet_len, micro_packet, stop_after_verified=2)
        if groups:
            used_packet_len = packet_len
            used_micro_packet = micro_packet
            break
    if not groups and deep_scan:
        candidates = _candidate_tiles(y_array, None, config, scan_offsets=True, max_tiles=9000)
        for packet_len, micro_packet in packet_modes:
            groups, checked, verified_tiles = scan_tiles(candidates, packet_len, micro_packet, stop_after_verified=1)
            if groups:
                used_packet_len = packet_len
                used_micro_packet = micro_packet
                break
    if not groups and profile_name in {"durable", "benchmark"}:
        groups, checked, verified_tiles = scan_dark_tiles(candidates, stop_after_verified=2)
        if groups:
            used_packet_len = MICRO_AUTH_PACKET_LEN
            used_micro_packet = True
    if groups and deep_scan and verified_tiles:
        sync_candidates = _synchronized_grid_tiles(
            y_array,
            None,
            config,
            verified_tiles,
            max_tiles=max(80, config.max_tiles * 8),
        )
        sync_groups, sync_checked, sync_verified_tiles = scan_tiles(
            sync_candidates,
            used_packet_len,
            used_micro_packet,
            stop_after_verified=max(config.min_tiles, 4),
        )
        merge_groups(groups, sync_groups)
        checked += sync_checked
        verified_tiles.extend(sync_verified_tiles)
        if sync_candidates:
            candidates = candidates + sync_candidates

    if not groups:
        raise PayloadError("No authenticated IWM2 local robust watermark could be recovered.")
    payload, verified, corrections = max(groups.values(), key=lambda item: item[1])
    confidence = min(1.0, 0.70 + 0.10 * min(verified, 4))
    bit_error_estimate = min(1.0, corrections / max(1, verified * _encoded_bit_count(used_packet_len)))
    return RobustExtractResult(
        payload=payload,
        profile=profile_name,
        tiles_total=len(candidates),
        tiles_checked=checked,
        tiles_verified=verified,
        bit_error_estimate=bit_error_estimate,
        confidence=confidence,
    )
