from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from watermark_app.image_watermark import embed_image, extract_image


TMP = ROOT / "tmp" / "robust"


def make_fixture(path: Path) -> None:
    from PIL import ImageDraw

    image = Image.new("RGB", (960, 720), "white")
    draw = ImageDraw.Draw(image)
    for y in range(0, 720, 24):
        draw.line((0, y, 960, y), fill=(220, 225, 232))
    for x in range(0, 960, 24):
        draw.line((x, 0, x, 720), fill=(235, 238, 242))
    draw.rectangle((120, 120, 840, 600), outline=(70, 100, 140), width=4)
    draw.text((160, 180), "IWM2 robust regression fixture", fill=(40, 50, 70))
    image.save(path)


def main() -> int:
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True)
    source = TMP / "source.png"
    make_fixture(source)
    password = "test-password"

    durable = embed_image(source, TMP, "Durable regression", password, profile="durable")
    recovered = extract_image(durable.output_path, password)
    assert recovered.payload.watermark_id == durable.watermark_id
    assert recovered.confidence > 0

    attacked = TMP / "source_wm_q85.jpg"
    Image.open(durable.output_path).convert("RGB").save(attacked, quality=85)
    attacked_recovered = extract_image(attacked, password)
    assert attacked_recovered.payload.watermark_id == durable.watermark_id

    cropped = TMP / "source_wm_crop10.png"
    with Image.open(durable.output_path) as image:
        width, height = image.size
        image.crop((int(width * 0.1), int(height * 0.1), width, height)).save(cropped)
    cropped_recovered = extract_image(cropped, password, deep_scan=True)
    assert cropped_recovered.payload.watermark_id == durable.watermark_id
    assert cropped_recovered.confidence >= 0.80

    try:
        extract_image(durable.output_path, "wrong-password")
    except Exception:
        pass
    else:
        raise AssertionError("Wrong password unexpectedly verified IWM2 watermark.")

    legacy = embed_image(source, TMP / "legacy", "Legacy regression", password, profile="legacy")
    legacy_recovered = extract_image(legacy.output_path, password)
    assert legacy_recovered.payload.core_text == "Legacy regression"

    second = embed_image(source, TMP, "Durable regression 2", password, profile="durable")
    assert second.output_path != durable.output_path

    print("Robust regression tests passed.")
    print(f"IWM2: {durable.output_path}")
    print(f"JPEG q85: {attacked}")
    print(f"Legacy: {legacy.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
