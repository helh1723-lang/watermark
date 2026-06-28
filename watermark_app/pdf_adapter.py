from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .image_watermark import embed_packet_into_image, extract_image
from .paths import unique_output_path
from .payload import Payload, build_auth_packet_bytes, build_payload_bytes, file_sha256, parse_payload_bytes
from .robust_watermark import embed_iwm2_packet_into_image, normalize_profile


PDF_META_KEY = "/InvisibleWatermarkPayload"
PDF_META_ID_KEY = "/InvisibleWatermarkId"
PDF_RENDER_DPI = 150


@dataclass
class PdfResult:
    output_path: Path
    watermark_id: str
    core_text: str
    mode: str
    quality_psnr: float = 0.0
    quality_ssim: float = 0.0
    paper_diff: float = 0.0
    tiles_total: int = 0
    tiles_used: int = 0


def _pdftoppm_path() -> str | None:
    candidates: list[Path] = []
    for command in ("pdftoppm.exe", "pdftoppm", "pdftoppm.cmd"):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))
    for candidate in list(candidates):
        candidates.extend(
            [
                candidate.parent.parent / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe",
                candidate.parent.parent / "poppler" / "Library" / "bin" / "pdftoppm.exe",
                candidate.parent.parent / "Library" / "bin" / "pdftoppm.exe",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def embed_pdf_metadata(
    input_path: str | Path,
    output_dir: str | Path,
    text: str,
    password: str,
) -> PdfResult:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    packet, meta = build_payload_bytes(text, password, source_sha256=file_sha256(input_path))
    reader = PdfReader(str(input_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    metadata = dict(reader.metadata or {})
    metadata[PDF_META_KEY] = base64.b64encode(packet).decode("ascii")
    metadata[PDF_META_ID_KEY] = str(meta["id"])
    writer.add_metadata(metadata)
    output_path = unique_output_path(output_dir / f"{input_path.stem}_wm_keep.pdf")
    with open(output_path, "wb") as handle:
        writer.write(handle)
    return PdfResult(output_path, str(meta["id"]), str(meta["core_text"]), "pdf-metadata")


def extract_pdf_metadata(input_path: str | Path, password: str) -> Payload:
    reader = PdfReader(str(input_path))
    metadata = reader.metadata or {}
    raw = metadata.get(PDF_META_KEY)
    if not raw:
        raise ValueError("This PDF does not contain a retained-format invisible watermark payload.")
    return parse_payload_bytes(base64.b64decode(str(raw)), password)


def _render_pdf_to_images(input_path: Path, output_dir: Path) -> list[Path]:
    pdftoppm = _pdftoppm_path()
    if not pdftoppm:
        raise RuntimeError("pdftoppm was not found. Install Poppler or use retained-format PDF mode.")
    prefix = output_dir / "page"
    completed = subprocess.run(
        [pdftoppm, "-r", str(PDF_RENDER_DPI), "-png", str(input_path), str(prefix)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"pdftoppm failed with exit code {completed.returncode}: {detail}")
    return sorted(output_dir.glob("page-*.png"))


def _images_to_pdf(images: list[Path], output_path: Path) -> None:
    if not images:
        raise RuntimeError("PDF rendering produced no page images.")
    c = canvas.Canvas(str(output_path))
    for image_path in images:
        with Image.open(image_path) as image:
            width_px, height_px = image.size
        width_pt = width_px * 72.0 / PDF_RENDER_DPI
        height_pt = height_px * 72.0 / PDF_RENDER_DPI
        c.setPageSize((width_pt, height_pt))
        c.drawImage(ImageReader(str(image_path)), 0, 0, width=width_pt, height=height_pt)
        c.showPage()
    c.save()


def embed_pdf_strong(
    input_path: str | Path,
    output_dir: str | Path,
    text: str,
    password: str,
    strength: str = "balanced",
    profile: str | None = None,
) -> PdfResult:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    full_packet, meta = build_payload_bytes(text, password, source_sha256=file_sha256(input_path))
    auth_packet, _ = build_auth_packet_bytes(
        text,
        password,
        source_sha256=file_sha256(input_path),
        watermark_id=str(meta["id"]),
        created_at=int(meta["created_at"]),
    )
    profile_name = normalize_profile(profile, strength)
    carrier_profile = "durable" if profile_name == "balanced" else profile_name
    output_path = unique_output_path(output_dir / f"{input_path.stem}_wm_strong.pdf")
    quality_psnr = 0.0
    quality_ssim = 0.0
    paper_diff = 0.0
    tiles_total = 0
    tiles_used = 0
    with tempfile.TemporaryDirectory(prefix="iwm_pdf_") as tmp_name:
        tmp_dir = Path(tmp_name)
        rendered = _render_pdf_to_images(input_path, tmp_dir)
        marked: list[Path] = []
        for index, page_image in enumerate(rendered, start=1):
            if carrier_profile == "legacy":
                marked_path, _ = embed_packet_into_image(
                    page_image,
                    tmp_dir,
                    full_packet,
                    password,
                    strength=strength,
                    output_name=f"marked-{index:04d}.png",
                )
            else:
                robust = embed_iwm2_packet_into_image(
                    page_image,
                    tmp_dir,
                    auth_packet,
                    password,
                    profile=carrier_profile,
                    output_name=f"marked-{index:04d}.png",
                    watermark_id=str(meta["id"]),
                    core_text="",
                )
                marked_path = robust.output_path
                quality_psnr += robust.quality_psnr
                quality_ssim += robust.quality_ssim
                paper_diff += robust.paper_diff
                tiles_total += robust.tiles_total
                tiles_used += robust.tiles_used
            marked.append(marked_path)
        _images_to_pdf(marked, output_path)

    reader = PdfReader(str(output_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata(
        {
            PDF_META_KEY: base64.b64encode(full_packet).decode("ascii"),
            PDF_META_ID_KEY: str(meta["id"]),
        }
    )
    with open(output_path, "wb") as handle:
        writer.write(handle)
    page_count = max(1, len(rendered))
    return PdfResult(
        output_path,
        str(meta["id"]),
        str(meta["core_text"]),
        "pdf-strong-legacy" if profile_name == "legacy" else f"pdf-strong-iwm2-{profile_name}-carrier-{carrier_profile}",
        quality_psnr / page_count if quality_psnr else 0.0,
        quality_ssim / page_count if quality_ssim else 0.0,
        paper_diff / page_count if paper_diff else 0.0,
        tiles_total,
        tiles_used,
    )


def extract_pdf_strong(input_path: str | Path, password: str) -> Payload:
    input_path = Path(input_path)
    with tempfile.TemporaryDirectory(prefix="iwm_pdf_read_") as tmp_name:
        tmp_dir = Path(tmp_name)
        pages = _render_pdf_to_images(input_path, tmp_dir)
        last_error: Exception | None = None
        for page in pages:
            try:
                return extract_image(page, password).payload
            except Exception as exc:
                last_error = exc
        if last_error:
            raise ValueError(f"No strong PDF watermark was recovered: {last_error}") from last_error
    raise ValueError("No strong PDF watermark was recovered.")
