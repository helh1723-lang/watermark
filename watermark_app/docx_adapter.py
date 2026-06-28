from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from .paths import unique_output_path
from .payload import Payload, build_payload_bytes, file_sha256, parse_payload_bytes
from .pdf_adapter import PdfResult, embed_pdf_strong


CUSTOM_XML_PATH = "customXml/invisibleWatermark.xml"
CONTENT_TYPES = "[Content_Types].xml"
CONTENT_TYPE = "application/xml"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"


@dataclass
class DocxResult:
    output_path: Path
    watermark_id: str
    core_text: str
    mode: str


def _payload_xml(packet: bytes, watermark_id: str) -> bytes:
    encoded = base64.b64encode(packet).decode("ascii")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<iwm:watermark xmlns:iwm="urn:local:invisible-watermark:v1" '
        f'id="{watermark_id}" payload="{encoded}" />'
    ).encode("utf-8")


def _update_content_types(xml_bytes: bytes) -> bytes:
    ET.register_namespace("", NS_CT)
    root = ET.fromstring(xml_bytes)
    part_name = "/" + CUSTOM_XML_PATH
    for override in root.findall(f"{{{NS_CT}}}Override"):
        if override.attrib.get("PartName") == part_name:
            override.attrib["ContentType"] = CONTENT_TYPE
            return ET.tostring(root, encoding="utf-8", xml_declaration=True)
    ET.SubElement(root, f"{{{NS_CT}}}Override", {"PartName": part_name, "ContentType": CONTENT_TYPE})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def embed_docx_keep(input_path: str | Path, output_dir: str | Path, text: str, password: str) -> DocxResult:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    packet, meta = build_payload_bytes(text, password, source_sha256=file_sha256(input_path))
    output_path = unique_output_path(output_dir / f"{input_path.stem}_wm_keep.docx")

    with zipfile.ZipFile(input_path, "r") as src, zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename == CUSTOM_XML_PATH:
                continue
            content = src.read(info.filename)
            if info.filename == CONTENT_TYPES:
                content = _update_content_types(content)
            dst.writestr(info, content)
        if CONTENT_TYPES not in src.namelist():
            raise ValueError("Invalid DOCX package: missing [Content_Types].xml")
        dst.writestr(CUSTOM_XML_PATH, _payload_xml(packet, str(meta["id"])))

    return DocxResult(output_path, str(meta["id"]), str(meta["core_text"]), "docx-keep")


def extract_docx_keep(input_path: str | Path, password: str) -> Payload:
    with zipfile.ZipFile(input_path, "r") as archive:
        if CUSTOM_XML_PATH not in archive.namelist():
            raise ValueError("This DOCX does not contain a retained-format invisible watermark payload.")
        root = ET.fromstring(archive.read(CUSTOM_XML_PATH))
        raw = root.attrib.get("payload")
        if not raw:
            raise ValueError("The DOCX watermark payload is empty.")
        return parse_payload_bytes(base64.b64decode(raw), password)


def _find_converter() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def _convert_office_file(input_path: Path, output_dir: Path, target: str) -> Path:
    converter = _find_converter()
    if not converter:
        raise RuntimeError(
            "No Office converter was found. Install LibreOffice, or export the document to DOCX/PDF manually."
        )
    subprocess.run(
        [
            converter,
            "--headless",
            "--convert-to",
            target,
            "--outdir",
            str(output_dir),
            str(input_path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    suffix = "." + target.split(":", 1)[0].lower()
    output_path = output_dir / f"{input_path.stem}{suffix}"
    if not output_path.exists():
        produced = sorted(output_dir.glob(f"{input_path.stem}.*"))
        if produced:
            return produced[0]
        raise RuntimeError(f"Office conversion completed but no {suffix} file was produced.")
    return output_path


def _convert_docx_to_pdf(input_path: Path, output_dir: Path) -> Path:
    return _convert_office_file(input_path, output_dir, "pdf")


def _convert_doc_to_docx(input_path: Path, output_dir: Path) -> Path:
    return _convert_office_file(input_path, output_dir, "docx")


def embed_doc_keep(input_path: str | Path, output_dir: str | Path, text: str, password: str) -> DocxResult:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="iwm_doc_") as tmp_name:
        docx_path = _convert_doc_to_docx(input_path, Path(tmp_name))
        result = embed_docx_keep(docx_path, output_dir, text, password)
    desired_path = output_dir / f"{input_path.stem}_wm_keep.docx"
    strong_path = unique_output_path(desired_path) if result.output_path != desired_path else desired_path
    if result.output_path != strong_path:
        result.output_path.replace(strong_path)
        result.output_path = strong_path
    result.mode = "doc-keep-converted-docx"
    return result


def embed_doc_strong(
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
    with tempfile.TemporaryDirectory(prefix="iwm_doc_") as tmp_name:
        pdf_path = _convert_docx_to_pdf(input_path, Path(tmp_name))
        result = embed_pdf_strong(pdf_path, output_dir, text, password, strength, profile)
    desired_path = output_dir / f"{input_path.stem}_wm_strong.pdf"
    strong_path = unique_output_path(desired_path) if result.output_path != desired_path else desired_path
    if result.output_path != strong_path:
        result.output_path.replace(strong_path)
        result.output_path = strong_path
    result.mode = result.mode.replace("pdf-strong", "doc-strong-converted-pdf")
    return result


def embed_docx_strong(
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
    with tempfile.TemporaryDirectory(prefix="iwm_docx_") as tmp_name:
        tmp_dir = Path(tmp_name)
        pdf_path = _convert_docx_to_pdf(input_path, tmp_dir)
        result = embed_pdf_strong(pdf_path, output_dir, text, password, strength, profile)
    desired_path = output_dir / f"{input_path.stem}_wm_strong.pdf"
    strong_path = unique_output_path(desired_path) if result.output_path != desired_path else desired_path
    if result.output_path != strong_path:
        result.output_path.replace(strong_path)
        result.output_path = strong_path
    return result
