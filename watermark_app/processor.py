from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .docx_adapter import embed_doc_keep, embed_doc_strong, embed_docx_keep, embed_docx_strong, extract_docx_keep
from .image_watermark import embed_image, extract_image
from .payload import Payload, file_sha256
from .pdf_adapter import embed_pdf_metadata, embed_pdf_strong, extract_pdf_metadata, extract_pdf_strong
from .records import RecordStore
from .video_adapter import VIDEO_EXTS, embed_video, extract_video


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
PDF_EXTS = {".pdf"}
DOCX_EXTS = {".docx"}
DOC_EXTS = {".doc"}
SUPPORTED_EXTS = IMAGE_EXTS | PDF_EXTS | DOCX_EXTS | DOC_EXTS | VIDEO_EXTS


@dataclass
class ProcessResult:
    input_path: str
    status: str
    message: str
    output_path: str = ""
    watermark_id: str = ""
    core_text: str = ""
    mode: str = ""
    quality_psnr: float = 0.0
    quality_ssim: float = 0.0
    paper_diff: float = 0.0
    tiles_total: int = 0
    tiles_used: int = 0
    frames_total: int = 0
    frames_marked: int = 0


@dataclass
class ReadResult:
    input_path: str
    status: str
    message: str
    watermark_id: str = ""
    core_text: str = ""
    created_at: int = 0
    mode: str = ""
    tiles_total: int = 0
    tiles_checked: int = 0
    tiles_verified: int = 0
    bit_error_estimate: float = 0.0
    confidence: float = 0.0
    frames_checked: int = 0
    frames_verified: int = 0


def collect_inputs(path: str | Path, recursive: bool = True) -> list[Path]:
    path = Path(path)
    if path.is_file():
        return [path]
    pattern = "**/*" if recursive else "*"
    return [item for item in path.glob(pattern) if item.is_file() and item.suffix.lower() in SUPPORTED_EXTS]


def embed_file(
    input_path: str | Path,
    output_dir: str | Path,
    text: str,
    password: str,
    strength: str = "balanced",
    profile: str = "balanced",
    pdf_mode: str = "both",
    docx_mode: str = "both",
    record_store: RecordStore | None = None,
) -> list[ProcessResult]:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    suffix = input_path.suffix.lower()
    results: list[ProcessResult] = []
    store = record_store or RecordStore(output_dir / "watermark_records.json")

    def add_record(result: ProcessResult) -> None:
        if result.status != "ok":
            return
        store.add(
            {
                "watermark_id": result.watermark_id,
                "core_text": result.core_text,
                "full_text": text,
                "created_at": int(time.time()),
                "source_path": str(input_path),
                "source_sha256": file_sha256(input_path),
                "output_path": result.output_path,
                "mode": result.mode,
            }
        )

    try:
        if suffix in IMAGE_EXTS:
            embedded = embed_image(input_path, output_dir, text, password, strength, profile=profile)
            result = ProcessResult(
                str(input_path),
                "ok",
                "Image watermark embedded.",
                str(embedded.output_path),
                embedded.watermark_id,
                embedded.core_text,
                embedded.algorithm,
                embedded.quality_psnr,
                embedded.quality_ssim,
                embedded.paper_diff,
                embedded.tiles_total,
                embedded.tiles_used,
            )
            add_record(result)
            return [result]

        if suffix in PDF_EXTS:
            modes = ["keep", "strong"] if pdf_mode == "both" else [pdf_mode]
            for mode in modes:
                try:
                    pdf_result = (
                        embed_pdf_metadata(input_path, output_dir, text, password)
                        if mode == "keep"
                        else embed_pdf_strong(input_path, output_dir, text, password, strength, profile)
                    )
                    result = ProcessResult(
                        str(input_path),
                        "ok",
                        f"PDF {mode} watermark embedded.",
                        str(pdf_result.output_path),
                        pdf_result.watermark_id,
                        pdf_result.core_text,
                        pdf_result.mode,
                        pdf_result.quality_psnr,
                        pdf_result.quality_ssim,
                        pdf_result.paper_diff,
                        pdf_result.tiles_total,
                        pdf_result.tiles_used,
                    )
                    add_record(result)
                    results.append(result)
                except Exception as exc:
                    results.append(ProcessResult(str(input_path), "error", f"PDF {mode} failed: {exc}", mode=mode))
            return results

        if suffix in DOCX_EXTS:
            modes = ["keep", "strong"] if docx_mode == "both" else [docx_mode]
            for mode in modes:
                try:
                    doc_result = (
                        embed_docx_keep(input_path, output_dir, text, password)
                        if mode == "keep"
                        else embed_docx_strong(input_path, output_dir, text, password, strength, profile)
                    )
                    result = ProcessResult(
                        str(input_path),
                        "ok",
                        f"DOCX {mode} watermark embedded.",
                        str(doc_result.output_path),
                        doc_result.watermark_id,
                        doc_result.core_text,
                        doc_result.mode,
                        getattr(doc_result, "quality_psnr", 0.0),
                        getattr(doc_result, "quality_ssim", 0.0),
                        getattr(doc_result, "paper_diff", 0.0),
                        getattr(doc_result, "tiles_total", 0),
                        getattr(doc_result, "tiles_used", 0),
                    )
                    add_record(result)
                    results.append(result)
                except Exception as exc:
                    results.append(ProcessResult(str(input_path), "error", f"DOCX {mode} failed: {exc}", mode=mode))
            return results

        if suffix in DOC_EXTS:
            modes = ["keep", "strong"] if docx_mode == "both" else [docx_mode]
            for mode in modes:
                try:
                    doc_result = (
                        embed_doc_keep(input_path, output_dir, text, password)
                        if mode == "keep"
                        else embed_doc_strong(input_path, output_dir, text, password, strength, profile)
                    )
                    result = ProcessResult(
                        str(input_path),
                        "ok",
                        f"DOC {mode} watermark embedded after LibreOffice conversion.",
                        str(doc_result.output_path),
                        doc_result.watermark_id,
                        doc_result.core_text,
                        doc_result.mode,
                        getattr(doc_result, "quality_psnr", 0.0),
                        getattr(doc_result, "quality_ssim", 0.0),
                        getattr(doc_result, "paper_diff", 0.0),
                        getattr(doc_result, "tiles_total", 0),
                        getattr(doc_result, "tiles_used", 0),
                    )
                    add_record(result)
                    results.append(result)
                except Exception as exc:
                    results.append(ProcessResult(str(input_path), "error", f"DOC {mode} failed: {exc}", mode=mode))
            return results

        if suffix in VIDEO_EXTS:
            video_result = embed_video(input_path, output_dir, text, password, strength=strength, profile=profile)
            result = ProcessResult(
                str(input_path),
                "ok",
                "Video frame-level invisible watermark embedded.",
                str(video_result.output_path),
                video_result.watermark_id,
                video_result.core_text,
                video_result.mode,
                video_result.quality_psnr,
                video_result.quality_ssim,
                video_result.paper_diff,
                video_result.tiles_total,
                video_result.tiles_used,
                video_result.frames_total,
                video_result.frames_marked,
            )
            add_record(result)
            return [result]
        return [ProcessResult(str(input_path), "skipped", "Unsupported file type.")]
    except Exception as exc:
        return [ProcessResult(str(input_path), "error", str(exc))]


def embed_many(
    inputs: Iterable[Path],
    output_dir: str | Path,
    text: str,
    password: str,
    strength: str = "balanced",
    profile: str = "balanced",
    pdf_mode: str = "both",
    docx_mode: str = "both",
) -> list[ProcessResult]:
    store = RecordStore(Path(output_dir) / "watermark_records.json")
    all_results: list[ProcessResult] = []
    for input_path in inputs:
        all_results.extend(
            embed_file(input_path, output_dir, text, password, strength, profile, pdf_mode, docx_mode, store)
        )
    return all_results


def read_file(input_path: str | Path, password: str, deep_scan: bool = False) -> ReadResult:
    input_path = Path(input_path)
    suffix = input_path.suffix.lower()
    try:
        payload: Payload
        mode: str
        if suffix in IMAGE_EXTS:
            extracted = extract_image(input_path, password, deep_scan=deep_scan)
            payload = extracted.payload
            mode = (
                f"image-iwm2-local confidence={extracted.confidence:.2f}"
                if extracted.algorithm == "image-iwm2-local"
                else f"image-dct repeat={extracted.repeat}"
            )
        elif suffix in PDF_EXTS:
            try:
                payload = extract_pdf_metadata(input_path, password)
                mode = "pdf-metadata"
            except Exception:
                payload = extract_pdf_strong(input_path, password)
                mode = "pdf-strong"
        elif suffix in DOCX_EXTS:
            payload = extract_docx_keep(input_path, password)
            mode = "docx-keep"
        elif suffix in VIDEO_EXTS:
            extracted_video = extract_video(input_path, password, deep_scan=deep_scan)
            payload = extracted_video.payload
            mode = extracted_video.mode
        else:
            if suffix in DOC_EXTS:
                return ReadResult(
                    str(input_path),
                    "skipped",
                    "Legacy .doc reading is not available. The watermark output is DOCX/PDF; read that converted file.",
                )
            return ReadResult(str(input_path), "skipped", "Unsupported file type.")
        core_text = payload.core_text
        if not core_text and payload.watermark_id:
            record = RecordStore(input_path.parent / "watermark_records.json").find(payload.watermark_id)
            if record:
                core_text = str(record.get("core_text") or record.get("full_text") or "")
        return ReadResult(
            str(input_path),
            "ok",
            "Watermark verified.",
            payload.watermark_id,
            core_text,
            payload.created_at,
            mode,
            extracted.tiles_total if suffix in IMAGE_EXTS else 0,
            extracted.tiles_checked if suffix in IMAGE_EXTS else 0,
            extracted.tiles_verified if suffix in IMAGE_EXTS else 0,
            extracted.bit_error_estimate if suffix in IMAGE_EXTS else 0.0,
            extracted.confidence if suffix in IMAGE_EXTS else (extracted_video.confidence if suffix in VIDEO_EXTS else 0.0),
            extracted_video.frames_checked if suffix in VIDEO_EXTS else 0,
            extracted_video.frames_verified if suffix in VIDEO_EXTS else 0,
        )
    except Exception as exc:
        return ReadResult(str(input_path), "error", str(exc))


def results_as_dicts(results: Iterable[ProcessResult | ReadResult]) -> list[dict]:
    return [asdict(result) for result in results]
