from __future__ import annotations

import argparse
import html
import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from .image_watermark import embed_image, extract_image_legacy
from .pdf_adapter import embed_pdf_metadata, embed_pdf_strong
from .processor import read_file
from .robust_watermark import extract_iwm2_image, image_quality_metrics


PASSWORD = "benchmark-password"
TEXT = "Benchmark authorization watermark WM-2026. Local robust authentication test."


@dataclass
class AttackResult:
    attack: str
    success: bool
    confidence: float
    tiles_verified: int
    message: str


@dataclass
class AlgorithmResult:
    fixture: str
    algorithm: str
    output_path: str
    psnr: float
    ssim: float
    paper_diff: float
    tiles_used: int
    tiles_total: int
    attacks: list[AttackResult]
    recovery_rate: float
    invisibility_score: float
    durability_score: float
    total_score: float


def _ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _make_photo(path: Path) -> None:
    width, height = 960, 720
    x = np.linspace(0, 1, width)
    y = np.linspace(0, 1, height)
    xx, yy = np.meshgrid(x, y)
    base = np.zeros((height, width, 3), dtype=np.uint8)
    base[..., 0] = np.clip(80 + 120 * xx + 25 * np.sin(yy * 24), 0, 255)
    base[..., 1] = np.clip(110 + 80 * yy + 40 * np.sin(xx * 18), 0, 255)
    base[..., 2] = np.clip(130 + 60 * (1 - xx) + 35 * np.cos((xx + yy) * 12), 0, 255)
    rng = np.random.default_rng(2026)
    noise = rng.normal(0, 9, base.shape)
    image = Image.fromarray(np.clip(base + noise, 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(image)
    for index in range(12):
        x0 = int(70 + index * 70)
        y0 = int(90 + 60 * np.sin(index))
        draw.ellipse((x0, y0, x0 + 110, y0 + 80), outline=(245, 245, 230), width=2)
    image.save(path)


def _make_document(path: Path) -> None:
    image = Image.new("RGB", (960, 720), "white")
    draw = ImageDraw.Draw(image)
    for y in range(70, 650, 34):
        draw.line((90, y, 850, y), fill=(222, 226, 232), width=1)
        draw.text((110, y - 20), f"Confidential benchmark paragraph line {y // 34:02d}", fill=(38, 44, 56))
    draw.rectangle((90, 70, 850, 650), outline=(80, 100, 130), width=3)
    draw.rectangle((620, 110, 820, 230), outline=(90, 130, 160), width=2)
    image.save(path)


def _make_poster(path: Path) -> None:
    image = Image.new("RGB", (960, 720), (244, 246, 242))
    draw = ImageDraw.Draw(image)
    colors = [(43, 86, 132), (210, 73, 52), (246, 184, 75), (46, 139, 105)]
    for index, color in enumerate(colors):
        draw.rectangle((index * 240, 0, index * 240 + 240, 720), fill=color)
    draw.rectangle((90, 100, 870, 620), fill=(248, 248, 244), outline=(25, 28, 35), width=4)
    draw.text((150, 170), "POSTER BENCHMARK", fill=(25, 28, 35))
    for y in range(250, 560, 48):
        draw.rectangle((150, y, 810, y + 18), fill=(75, 86, 96))
    image.save(path)


def build_fixtures(fixture_dir: Path, full: bool) -> list[Path]:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixtures = [
        fixture_dir / "photo_texture.png",
        fixture_dir / "document_page.png",
    ]
    _make_photo(fixtures[0])
    _make_document(fixtures[1])
    if full:
        poster = fixture_dir / "poster_blocks.png"
        _make_poster(poster)
        fixtures.append(poster)
    user_dir = Path("tests") / "fixtures" / "benchmark_inputs"
    if user_dir.exists():
        for source in sorted(user_dir.iterdir()):
            if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
                target = fixture_dir / f"user_{source.name}"
                shutil.copy2(source, target)
                fixtures.append(target)
    return fixtures


def attack_original(image: Image.Image, path: Path) -> Path:
    image.save(path)
    return path


def attack_jpeg(image: Image.Image, path: Path, quality: int) -> Path:
    target = path.with_suffix(".jpg")
    image.convert("RGB").save(target, quality=quality)
    return target


def attack_crop(image: Image.Image, path: Path, pct: float) -> Path:
    width, height = image.size
    dx = int(width * pct)
    dy = int(height * pct)
    image.crop((dx, dy, width - dx, height - dy)).save(path)
    return path


def attack_resize(image: Image.Image, path: Path, scale: float) -> Path:
    width, height = image.size
    small = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.LANCZOS)
    small.resize((width, height), Image.Resampling.LANCZOS).save(path)
    return path


def attack_brightness(image: Image.Image, path: Path) -> Path:
    ImageEnhance.Brightness(image).enhance(1.12).save(path)
    return path


def attack_blur(image: Image.Image, path: Path) -> Path:
    image.filter(ImageFilter.GaussianBlur(radius=0.7)).save(path)
    return path


def attack_occlusion(image: Image.Image, path: Path) -> Path:
    attacked = image.copy().convert("RGB")
    draw = ImageDraw.Draw(attacked)
    width, height = attacked.size
    draw.rectangle((int(width * 0.68), int(height * 0.1), int(width * 0.88), int(height * 0.3)), fill=(248, 248, 248))
    attacked.save(path)
    return path


def attack_noise(image: Image.Image, path: Path) -> Path:
    arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    rng = np.random.default_rng(901)
    arr = np.clip(arr + rng.normal(0, 4, arr.shape), 0, 255).astype(np.uint8)
    Image.fromarray(arr, "RGB").save(path)
    return path


def attack_sharpen(image: Image.Image, path: Path) -> Path:
    image.filter(ImageFilter.SHARPEN).save(path)
    return path


def _attacks(full: bool) -> list[tuple[str, Callable[[Image.Image, Path], Path]]]:
    attacks: list[tuple[str, Callable[[Image.Image, Path], Path]]] = [
        ("original", attack_original),
        ("jpeg_q85", lambda image, path: attack_jpeg(image, path, 85)),
        ("jpeg_q75", lambda image, path: attack_jpeg(image, path, 75)),
        ("crop_10pct", lambda image, path: attack_crop(image, path, 0.05)),
        ("resize_75pct", lambda image, path: attack_resize(image, path, 0.75)),
        ("brightness", attack_brightness),
        ("blur", attack_blur),
        ("occlusion", attack_occlusion),
    ]
    if full:
        attacks.extend(
            [
                ("jpeg_q95", lambda image, path: attack_jpeg(image, path, 95)),
                ("jpeg_q60", lambda image, path: attack_jpeg(image, path, 60)),
                ("crop_25pct", lambda image, path: attack_crop(image, path, 0.125)),
                ("resize_50pct", lambda image, path: attack_resize(image, path, 0.50)),
                ("noise", attack_noise),
                ("sharpen", attack_sharpen),
            ]
        )
    return attacks


def _algorithm_cases(full: bool) -> list[tuple[str, str]]:
    cases = [
        ("legacy-dct", "legacy"),
        ("iwm2-invisible", "invisible"),
        ("iwm2-balanced", "balanced"),
        ("iwm2-durable", "durable"),
    ]
    if not full:
        return [cases[0], cases[2], cases[3]]
    return cases


def _invisibility_score(psnr: float, ssim: float, paper_diff: float) -> float:
    psnr_score = min(1.0, max(0.0, (psnr - 35.0) / 15.0))
    ssim_score = min(1.0, max(0.0, (ssim - 0.94) / 0.06))
    paper_penalty = min(0.25, paper_diff / 12.0)
    return max(0.0, (psnr_score + ssim_score) / 2.0 - paper_penalty)


def _score(recovery_rate: float, invisibility: float, durability: float) -> float:
    return 0.45 * recovery_rate + 0.35 * invisibility + 0.20 * durability


def _extract_known(path: Path, profile: str, crop_attack: bool):
    if profile == "legacy":
        return extract_image_legacy(path, PASSWORD)
    return extract_iwm2_image(path, PASSWORD, profile=profile, deep_scan=crop_attack)


def evaluate_image_fixture(fixture: Path, work_dir: Path, full: bool) -> list[AlgorithmResult]:
    results: list[AlgorithmResult] = []
    attacks = _attacks(full)
    for algorithm_name, profile in _algorithm_cases(full):
        print(f"[benchmark] fixture={fixture.name} algorithm={algorithm_name}", flush=True)
        case_dir = work_dir / fixture.stem / algorithm_name
        case_dir.mkdir(parents=True, exist_ok=True)
        try:
            embedded = embed_image(fixture, case_dir, TEXT, PASSWORD, profile=profile)
        except Exception as exc:
            results.append(
                AlgorithmResult(
                    fixture.name,
                    algorithm_name,
                    "",
                    0.0,
                    0.0,
                    0.0,
                    0,
                    0,
                    [AttackResult("embed", False, 0.0, 0, str(exc))],
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                )
            )
            continue

        original = Image.open(fixture)
        marked = Image.open(embedded.output_path)
        metrics = image_quality_metrics(original, marked)
        attack_results: list[AttackResult] = []
        for attack_name, attack_fn in attacks:
            attacked_path = case_dir / f"attack_{attack_name}.png"
            attacked_source = Image.open(embedded.output_path)
            try:
                attacked = attack_fn(attacked_source, attacked_path)
                extracted = _extract_known(Path(attacked), profile, crop_attack=("crop" in attack_name))
                success = extracted.payload.watermark_id == embedded.watermark_id
                attack_results.append(
                    AttackResult(
                        attack_name,
                        success,
                        getattr(extracted, "confidence", 0.0),
                        getattr(extracted, "tiles_verified", 0),
                        "ok" if success else "wrong watermark id",
                    )
                )
            except Exception as exc:
                attack_results.append(AttackResult(attack_name, False, 0.0, 0, str(exc)))

        recovery_rate = sum(1 for item in attack_results if item.success) / max(1, len(attack_results))
        destructive = [item for item in attack_results if item.attack != "original"]
        durability = sum(1 for item in destructive if item.success) / max(1, len(destructive))
        invisibility = _invisibility_score(metrics["psnr"], metrics["ssim"], metrics["paper_diff"])
        results.append(
            AlgorithmResult(
                fixture.name,
                algorithm_name,
                str(embedded.output_path),
                metrics["psnr"],
                metrics["ssim"],
                metrics["paper_diff"],
                embedded.tiles_used,
                embedded.tiles_total,
                attack_results,
                recovery_rate,
                invisibility,
                durability,
                _score(recovery_rate, invisibility, durability),
            )
        )
    return results


def _make_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path))
    c.drawString(72, 720, "Watermark benchmark PDF page")
    c.drawString(72, 690, "This page checks metadata stripping and rasterized strong recovery.")
    c.rect(72, 560, 360, 80)
    c.save()


def _strip_pdf_metadata(input_path: Path, output_path: Path) -> None:
    reader = PdfReader(str(input_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    with open(output_path, "wb") as handle:
        writer.write(handle)


def evaluate_pdf_smoke(work_dir: Path) -> dict[str, object]:
    pdf_dir = work_dir / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    source = pdf_dir / "source.pdf"
    _make_pdf(source)
    result: dict[str, object] = {"available": True, "cases": []}
    try:
        keep = embed_pdf_metadata(source, pdf_dir, TEXT, PASSWORD)
        keep_read = read_file(keep.output_path, PASSWORD)
        result["cases"].append({"case": "pdf_keep", "success": keep_read.status == "ok", "mode": keep_read.mode})
    except Exception as exc:
        result["cases"].append({"case": "pdf_keep", "success": False, "message": str(exc)})
    try:
        strong = embed_pdf_strong(source, pdf_dir, TEXT, PASSWORD, profile="balanced")
        stripped = pdf_dir / "strong_no_metadata.pdf"
        _strip_pdf_metadata(strong.output_path, stripped)
        strong_read = read_file(stripped, PASSWORD)
        result["cases"].append(
            {"case": "pdf_strong_metadata_stripped", "success": strong_read.status == "ok", "mode": strong_read.mode}
        )
    except Exception as exc:
        result["cases"].append({"case": "pdf_strong_metadata_stripped", "success": False, "message": str(exc)})
    return result


def _summaries(results: list[AlgorithmResult]) -> list[dict[str, object]]:
    grouped: dict[str, list[AlgorithmResult]] = {}
    for result in results:
        grouped.setdefault(result.algorithm, []).append(result)
    rows: list[dict[str, object]] = []
    for algorithm, items in grouped.items():
        rows.append(
            {
                "algorithm": algorithm,
                "cases": len(items),
                "avg_total_score": sum(item.total_score for item in items) / len(items),
                "avg_recovery_rate": sum(item.recovery_rate for item in items) / len(items),
                "avg_invisibility_score": sum(item.invisibility_score for item in items) / len(items),
                "avg_psnr": sum(item.psnr for item in items) / len(items),
                "avg_ssim": sum(item.ssim for item in items) / len(items),
            }
        )
    return sorted(rows, key=lambda row: float(row["avg_total_score"]), reverse=True)


def write_reports(output_dir: Path, results: list[AlgorithmResult], pdf_result: dict[str, object], started_at: float, full: bool) -> None:
    summaries = _summaries(results)
    payload = {
        "mode": "full" if full else "standard",
        "duration_seconds": time.time() - started_at,
        "summary": summaries,
        "results": [
            {
                **{key: value for key, value in asdict(result).items() if key != "attacks"},
                "attacks": [asdict(attack) for attack in result.attacks],
            }
            for result in results
        ],
        "pdf": pdf_result,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 数字水印评测报告",
        "",
        f"模式：{'full' if full else 'standard'}",
        f"耗时：{payload['duration_seconds']:.1f} 秒",
        "",
        "## 总览",
        "",
        "| 排名 | 算法 | 综合分 | 恢复率 | 无感分 | PSNR | SSIM |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for index, row in enumerate(summaries, start=1):
        lines.append(
            f"| {index} | {row['algorithm']} | {float(row['avg_total_score']):.3f} | "
            f"{float(row['avg_recovery_rate']):.3f} | {float(row['avg_invisibility_score']):.3f} | "
            f"{float(row['avg_psnr']):.2f} | {float(row['avg_ssim']):.4f} |"
        )

    failures = [
        (result, attack)
        for result in results
        for attack in result.attacks
        if not attack.success
    ]
    lines.extend(["", "## 失败样本", ""])
    if failures:
        for result, attack in failures:
            lines.append(f"- `{result.algorithm}` / `{result.fixture}` / `{attack.attack}`：{attack.message}")
    else:
        lines.append("- 无")
    lines.extend(["", "## PDF 检查", ""])
    for case in pdf_result.get("cases", []):
        lines.append(f"- {case}")
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")

    html_lines = [
        "<!doctype html><meta charset='utf-8'><title>数字水印评测报告</title>",
        "<style>body{font-family:Arial,'Microsoft YaHei',sans-serif;margin:32px;line-height:1.5}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px 10px}th{background:#f3f5f7}code{background:#f6f8fa;padding:1px 4px}</style>",
        "<h1>数字水印评测报告</h1>",
        f"<p>模式：{'full' if full else 'standard'}，耗时：{payload['duration_seconds']:.1f} 秒</p>",
        "<h2>总览</h2><table><tr><th>排名</th><th>算法</th><th>综合分</th><th>恢复率</th><th>无感分</th><th>PSNR</th><th>SSIM</th></tr>",
    ]
    for index, row in enumerate(summaries, start=1):
        html_lines.append(
            f"<tr><td>{index}</td><td>{html.escape(str(row['algorithm']))}</td>"
            f"<td>{float(row['avg_total_score']):.3f}</td>"
            f"<td>{float(row['avg_recovery_rate']):.3f}</td>"
            f"<td>{float(row['avg_invisibility_score']):.3f}</td>"
            f"<td>{float(row['avg_psnr']):.2f}</td>"
            f"<td>{float(row['avg_ssim']):.4f}</td></tr>"
        )
    html_lines.append("</table><h2>失败样本</h2><ul>")
    if failures:
        for result, attack in failures:
            html_lines.append(
                f"<li><code>{html.escape(result.algorithm)}</code> / "
                f"<code>{html.escape(result.fixture)}</code> / "
                f"<code>{html.escape(attack.attack)}</code>: {html.escape(attack.message)}</li>"
            )
    else:
        html_lines.append("<li>无</li>")
    html_lines.append("</ul><h2>PDF 检查</h2><ul>")
    for case in pdf_result.get("cases", []):
        html_lines.append(f"<li>{html.escape(str(case))}</li>")
    html_lines.append("</ul>")
    (output_dir / "report.html").write_text("\n".join(html_lines), encoding="utf-8")


def run_benchmark(output_dir: Path, full: bool) -> None:
    started_at = time.time()
    _ensure_clean_dir(output_dir)
    fixture_dir = output_dir / "fixtures"
    work_dir = output_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    fixtures = build_fixtures(fixture_dir, full)
    print(f"[benchmark] mode={'full' if full else 'standard'} fixtures={len(fixtures)}", flush=True)
    all_results: list[AlgorithmResult] = []
    for fixture in fixtures:
        all_results.extend(evaluate_image_fixture(fixture, work_dir, full))
    print("[benchmark] pdf smoke", flush=True)
    pdf_result = evaluate_pdf_smoke(work_dir)
    write_reports(output_dir, all_results, pdf_result, started_at, full)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run invisible watermark benchmark")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run benchmark and write reports")
    run.add_argument("--output", default="output/benchmark", help="Output directory")
    run.add_argument("--full", action="store_true", help="Run the larger attack matrix")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        run_benchmark(Path(args.output), args.full)
        print(f"Benchmark report written to {Path(args.output).resolve()}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
