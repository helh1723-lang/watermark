from __future__ import annotations

import argparse
import json
from pathlib import Path

from .processor import collect_inputs, embed_many, read_file, results_as_dicts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Invisible digital watermark toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    embed = sub.add_parser("embed", help="Embed invisible watermarks")
    embed.add_argument("input", help="Input file or folder")
    embed.add_argument("-o", "--output", default="output", help="Output directory")
    embed.add_argument("-t", "--text", required=True, help="Watermark text")
    embed.add_argument("-p", "--password", required=True, help="Watermark password")
    embed.add_argument("--strength", choices=["subtle", "balanced", "strong"], default="balanced")
    embed.add_argument(
        "--profile",
        choices=["invisible", "balanced", "durable", "benchmark", "legacy"],
        default="balanced",
        help="IWM2 embedding profile. Use legacy for the original IWM1 DCT carrier.",
    )
    embed.add_argument("--pdf-mode", choices=["keep", "strong", "both"], default="both")
    embed.add_argument("--docx-mode", choices=["keep", "strong", "both"], default="both")
    embed.add_argument("--no-recursive", action="store_true")

    read = sub.add_parser("read", help="Read and verify an invisible watermark")
    read.add_argument("input", help="Input file")
    read.add_argument("-p", "--password", required=True, help="Watermark password")
    read.add_argument("--deep-scan", action="store_true", help="Use slower crop/offset recovery for edited images")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "embed":
        inputs = collect_inputs(args.input, recursive=not args.no_recursive)
        if not inputs:
            print(
                json.dumps(
                    [
                        {
                            "input_path": args.input,
                            "status": "error",
                            "message": "Input path was not found or contains no supported files.",
                        }
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        results = embed_many(
            inputs,
            Path(args.output),
            args.text,
            args.password,
            strength=args.strength,
            profile=args.profile,
            pdf_mode=args.pdf_mode,
            docx_mode=args.docx_mode,
        )
        print(json.dumps(results_as_dicts(results), ensure_ascii=False, indent=2))
        return 0 if all(result.status in {"ok", "skipped"} for result in results) else 1

    result = read_file(args.input, args.password, deep_scan=args.deep_scan)
    print(json.dumps(results_as_dicts([result])[0], ensure_ascii=False, indent=2))
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
