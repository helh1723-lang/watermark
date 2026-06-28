from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import struct
import time
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


MAGIC = b"IWM1"
AUTH_MAGIC = b"IWM2"
MICRO_AUTH_MAGIC = b"IW2S"
APP_SALT = b"invisible-watermark-local-v1"
MAX_CORE_CHARS = 200
AUTH_PACKET_LEN = 30
MICRO_AUTH_PACKET_LEN = 18


class PayloadError(ValueError):
    pass


@dataclass
class Payload:
    watermark_id: str
    created_at: int
    core_text: str
    full_text_sha256: str
    source_sha256: str
    signature: str

    @property
    def signature_valid(self) -> bool:
        return self.signature == "valid"


@lru_cache(maxsize=256)
def derive_key(password: str, purpose: bytes = b"payload") -> bytes:
    if not password:
        raise PayloadError("Password cannot be empty.")
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        APP_SALT + b":" + purpose,
        180_000,
        dklen=32,
    )


def position_seed(password: str) -> int:
    key = derive_key(password, b"positions")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def build_payload_bytes(text: str, password: str, source_sha256: str = "") -> tuple[bytes, dict[str, Any]]:
    core_text = text[:MAX_CORE_CHARS]
    watermark_id = secrets.token_hex(8)
    base: dict[str, Any] = {
        "v": 1,
        "id": watermark_id,
        "created_at": int(time.time()),
        "core_text": core_text,
        "full_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_sha256": source_sha256,
    }
    sig = hmac.new(derive_key(password, b"hmac"), _canonical_json(base), hashlib.sha256).digest()
    base["hmac"] = base64.urlsafe_b64encode(sig).decode("ascii")
    packed_body = zlib.compress(_canonical_json(base), 9)
    if len(packed_body) > 65535:
        raise PayloadError("Watermark text is too large after compression.")
    header = MAGIC + struct.pack(">H", len(packed_body))
    crc = struct.pack(">I", binascii.crc32(header + packed_body) & 0xFFFFFFFF)
    return header + packed_body + crc, base


def _short_digest_hex(value: str, size: int = 8) -> bytes:
    if value:
        try:
            raw = bytes.fromhex(value)
        except ValueError:
            raw = hashlib.sha256(value.encode("utf-8")).digest()
    else:
        raw = b""
    if len(raw) < size:
        raw = hashlib.sha256(value.encode("utf-8")).digest()
    return raw[:size]


def build_auth_packet_bytes(
    text: str,
    password: str,
    source_sha256: str = "",
    watermark_id: str | None = None,
    created_at: int | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Build compact authenticated IWM2 packet for robust carrier embedding.

    The packet intentionally does not include the core text. It is a durable
    authentication beacon; full text remains in retained-format layers or records.
    """

    created = int(time.time()) if created_at is None else int(created_at)
    wm_id = watermark_id or secrets.token_hex(8)
    id_bytes = bytes.fromhex(wm_id)
    text_digest = hashlib.sha256(text.encode("utf-8")).digest()[:2]
    prefix = AUTH_MAGIC + bytes([2, 0]) + struct.pack(">I", created) + id_bytes + text_digest
    tag = hmac.new(derive_key(password, b"auth2"), prefix, hashlib.sha256).digest()[:6]
    packet_no_crc = prefix + tag
    crc = struct.pack(">I", binascii.crc32(packet_no_crc) & 0xFFFFFFFF)
    packet = packet_no_crc + crc
    meta: dict[str, Any] = {
        "v": 2,
        "id": wm_id,
        "created_at": created,
        "core_text": "",
        "full_text_sha256": text_digest.hex(),
        "source_sha256": _short_digest_hex(source_sha256, 2).hex(),
    }
    return packet, meta


def build_micro_auth_packet_bytes(watermark_id: str, password: str) -> bytes:
    id_bytes = bytes.fromhex(watermark_id)
    prefix = MICRO_AUTH_MAGIC + id_bytes
    tag = hmac.new(derive_key(password, b"auth2-short"), prefix, hashlib.sha256).digest()[:6]
    return prefix + tag


def parse_micro_auth_packet_bytes(raw: bytes, password: str) -> Payload:
    if len(raw) < MICRO_AUTH_PACKET_LEN or raw[:4] != MICRO_AUTH_MAGIC:
        raise PayloadError("No valid IWM2 short authentication header was found.")
    packet = raw[:MICRO_AUTH_PACKET_LEN]
    expected_tag = hmac.new(derive_key(password, b"auth2-short"), packet[:12], hashlib.sha256).digest()[:6]
    if not hmac.compare_digest(packet[12:18], expected_tag):
        raise PayloadError("IWM2 short authentication verification failed. Check the password.")
    return Payload(
        watermark_id=packet[4:12].hex(),
        created_at=0,
        core_text="",
        full_text_sha256="",
        source_sha256="",
        signature="valid",
    )


def parse_auth_packet_bytes(raw: bytes, password: str) -> Payload:
    if len(raw) < AUTH_PACKET_LEN or raw[:4] != AUTH_MAGIC:
        raise PayloadError("No valid IWM2 authentication header was found.")
    packet = raw[:AUTH_PACKET_LEN]
    expected_crc = struct.unpack(">I", packet[-4:])[0]
    actual_crc = binascii.crc32(packet[:-4]) & 0xFFFFFFFF
    if expected_crc != actual_crc:
        raise PayloadError("The recovered IWM2 authentication packet failed CRC validation.")
    version = packet[4]
    if version != 2:
        raise PayloadError("Unsupported IWM2 authentication packet version.")
    supplied_tag = packet[20:26]
    expected_tag = hmac.new(derive_key(password, b"auth2"), packet[:20], hashlib.sha256).digest()[:6]
    if not hmac.compare_digest(supplied_tag, expected_tag):
        raise PayloadError("IWM2 authentication verification failed. Check the password.")
    created_at = struct.unpack(">I", packet[6:10])[0]
    return Payload(
        watermark_id=packet[10:18].hex(),
        created_at=created_at,
        core_text="",
        full_text_sha256=packet[18:20].hex(),
        source_sha256="",
        signature="valid",
    )


def parse_payload_bytes(raw: bytes, password: str) -> Payload:
    if raw[:4] == AUTH_MAGIC:
        return parse_auth_packet_bytes(raw, password)
    if len(raw) < 10 or raw[:4] != MAGIC:
        raise PayloadError("No valid watermark header was found.")
    body_len = struct.unpack(">H", raw[4:6])[0]
    expected_len = 4 + 2 + body_len + 4
    if len(raw) < expected_len:
        raise PayloadError("The recovered watermark is incomplete.")
    packet = raw[:expected_len]
    expected_crc = struct.unpack(">I", packet[-4:])[0]
    actual_crc = binascii.crc32(packet[:-4]) & 0xFFFFFFFF
    if expected_crc != actual_crc:
        raise PayloadError("The recovered watermark failed CRC validation.")
    try:
        data = json.loads(zlib.decompress(packet[6:-4]).decode("utf-8"))
    except Exception as exc:  # pragma: no cover - exact decompressor error varies
        raise PayloadError("The recovered watermark payload is not readable.") from exc

    supplied = data.pop("hmac", "")
    expected = hmac.new(derive_key(password, b"hmac"), _canonical_json(data), hashlib.sha256).digest()
    supplied_bytes = base64.urlsafe_b64decode(supplied.encode("ascii"))
    if not hmac.compare_digest(supplied_bytes, expected):
        raise PayloadError("Watermark signature verification failed. Check the password.")

    return Payload(
        watermark_id=str(data.get("id", "")),
        created_at=int(data.get("created_at", 0)),
        core_text=str(data.get("core_text", "")),
        full_text_sha256=str(data.get("full_text_sha256", "")),
        source_sha256=str(data.get("source_sha256", "")),
        signature="valid",
    )


def bytes_to_bits(data: bytes) -> list[int]:
    return [(byte >> shift) & 1 for byte in data for shift in range(7, -1, -1)]


def bits_to_bytes(bits: list[int]) -> bytes:
    out = bytearray()
    for index in range(0, len(bits), 8):
        chunk = bits[index : index + 8]
        if len(chunk) < 8:
            break
        value = 0
        for bit in chunk:
            value = (value << 1) | int(bit)
        out.append(value)
    return bytes(out)
