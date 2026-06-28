from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .processor import embed_file, read_file, results_as_dicts


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ".runtime" / "web"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
OUTPUT_DIR = RUNTIME_DIR / "outputs"
DOWNLOADS: dict[str, Path] = {}
JOBS: dict[str, dict[str, object]] = {}
JOB_LOCK = threading.Lock()


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _set_job(job_key: str, **updates: object) -> None:
    with JOB_LOCK:
        job = JOBS.setdefault(job_key, {})
        job.update(updates)
        job["updated_at"] = time.time()


def _get_job(job_key: str) -> dict[str, object] | None:
    with JOB_LOCK:
        job = JOBS.get(job_key)
        return dict(job) if job else None


class WebHandler(BaseHTTPRequestHandler):
    server_version = "WatermarkWeb/0.2"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, status: int, payload: object) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "File not found."})
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{path.name.encode("ascii", "ignore").decode("ascii") or "watermarked"}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path.startswith("/api/download/"):
            token = unquote(parsed.path.rsplit("/", 1)[-1])
            path = DOWNLOADS.get(token)
            self._send_file(path) if path else self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown download."})
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = unquote(parsed.path.rsplit("/", 1)[-1])
            job = _get_job(job_id)
            self._send_json(HTTPStatus.OK, job) if job else self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown job."})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint."})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/watermark":
            try:
                response = self._handle_watermark()
                self._send_json(HTTPStatus.OK, response)
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if parsed.path == "/api/watermark/jobs":
            try:
                response = self._handle_watermark_job()
                self._send_json(HTTPStatus.ACCEPTED, response)
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if parsed.path == "/api/read":
            try:
                response = self._handle_read()
                self._send_json(HTTPStatus.OK, response)
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint."})

    def _read_multipart(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("Expected multipart/form-data.")

        import cgi

        return cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )

    def _handle_watermark(self) -> dict[str, object]:
        form = self._read_multipart()
        upload = form["file"] if "file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            raise ValueError("No file was uploaded.")
        text = str(form.getfirst("text", "")).strip()
        password = str(form.getfirst("password", "")).strip()
        profile = str(form.getfirst("profile", "balanced")).strip() or "balanced"
        pdf_mode = str(form.getfirst("pdfMode", "both")).strip() or "both"
        docx_mode = str(form.getfirst("docxMode", "both")).strip() or "both"
        if not text:
            raise ValueError("Watermark text cannot be empty.")
        if not password:
            raise ValueError("Password cannot be empty.")

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        job_id = uuid.uuid4().hex
        safe_name = Path(str(upload.filename)).name
        input_path = UPLOAD_DIR / f"{job_id}_{safe_name}"
        with open(input_path, "wb") as handle:
            shutil.copyfileobj(upload.file, handle)

        output_dir = OUTPUT_DIR / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        results = embed_file(
            input_path,
            output_dir,
            text,
            password,
            profile=profile,
            pdf_mode=pdf_mode,
            docx_mode=docx_mode,
        )
        payload = results_as_dicts(results)
        for item in payload:
            output_path = item.get("output_path")
            if output_path:
                token = uuid.uuid4().hex
                DOWNLOADS[token] = Path(str(output_path))
                item["download_url"] = f"/api/download/{token}"
        return {"job_id": job_id, "results": payload}

    def _handle_watermark_job(self) -> dict[str, object]:
        form = self._read_multipart()
        upload = form["file"] if "file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            raise ValueError("No file was uploaded.")
        text = str(form.getfirst("text", "")).strip()
        password = str(form.getfirst("password", "")).strip()
        profile = str(form.getfirst("profile", "balanced")).strip() or "balanced"
        pdf_mode = str(form.getfirst("pdfMode", "both")).strip() or "both"
        docx_mode = str(form.getfirst("docxMode", "both")).strip() or "both"
        if not text:
            raise ValueError("Watermark text cannot be empty.")
        if not password:
            raise ValueError("Password cannot be empty.")

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        job_id = uuid.uuid4().hex
        safe_name = Path(str(upload.filename)).name
        input_path = UPLOAD_DIR / f"{job_id}_{safe_name}"
        with open(input_path, "wb") as handle:
            shutil.copyfileobj(upload.file, handle)

        output_dir = OUTPUT_DIR / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        _set_job(
            job_id,
            job_id=job_id,
            status="queued",
            progress=1,
            message="已排队",
            results=[],
            error="",
            created_at=time.time(),
        )

        def progress(percent: float, message: str) -> None:
            _set_job(job_id, status="running", progress=percent, message=message)

        def worker() -> None:
            try:
                _set_job(job_id, status="running", progress=3, message="开始水印任务")
                results = embed_file(
                    input_path,
                    output_dir,
                    text,
                    password,
                    profile=profile,
                    pdf_mode=pdf_mode,
                    docx_mode=docx_mode,
                    progress_callback=progress,
                )
                payload = results_as_dicts(results)
                for item in payload:
                    output_path = item.get("output_path")
                    if output_path:
                        token = uuid.uuid4().hex
                        DOWNLOADS[token] = Path(str(output_path))
                        item["download_url"] = f"/api/download/{token}"
                failed = [item for item in payload if item.get("status") == "error"]
                _set_job(
                    job_id,
                    status="error" if failed and len(failed) == len(payload) else "done",
                    progress=100,
                    message="水印任务完成" if not failed else "水印任务完成，但部分文件失败",
                    results=payload,
                    error="",
                )
            except Exception as exc:
                _set_job(job_id, status="error", progress=100, message="水印任务失败", error=str(exc), results=[])

        threading.Thread(target=worker, daemon=True).start()
        return {"job_id": job_id, "status": "queued", "progress": 1, "message": "已排队"}

    def _handle_read(self) -> dict[str, object]:
        form = self._read_multipart()
        upload = form["file"] if "file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            raise ValueError("No file was uploaded.")
        password = str(form.getfirst("password", "")).strip()
        deep_scan = str(form.getfirst("deepScan", "false")).lower() in {"1", "true", "yes", "on"}
        if not password:
            raise ValueError("Password cannot be empty.")

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        job_id = uuid.uuid4().hex
        safe_name = Path(str(upload.filename)).name
        input_path = UPLOAD_DIR / f"{job_id}_{safe_name}"
        with open(input_path, "wb") as handle:
            shutil.copyfileobj(upload.file, handle)

        result = read_file(input_path, password, deep_scan=deep_scan)
        return {"job_id": job_id, "result": results_as_dicts([result])[0]}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run local web API for the watermark app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="iwm_web_"):
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        server = ThreadingHTTPServer((args.host, args.port), WebHandler)
        print(f"Watermark web API running at http://{args.host}:{args.port}", flush=True)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
