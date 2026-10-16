#!/usr/bin/env python3
"""GUI と同じ処理でテンプレートの指示を組み立てる。素材選択や描画は実行しない。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quick_slide.design_templates import compile_document


def main():
    # 呼ぶ側は UTF-8 で読む。Windows の既定（cp932）で書かない。
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--brand", type=Path, required=True)
    parser.add_argument("--mode", choices=("presentation", "pitch"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        d = json.loads(args.template.read_text(encoding="utf-8"))
        brand = json.loads(args.brand.read_text(encoding="utf-8"))
        compiled = compile_document(d, brand, mode=args.mode)
        args.out.mkdir(parents=True, exist_ok=True)
        for name, content in compiled["files"].items():
            if content:
                (args.out / name).write_text(content, encoding="utf-8")
        (args.out / "compilation.json").write_text(json.dumps({k: v for k, v in compiled.items() if k != "files"}, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps({"stage": compiled["stage"], "render_ready": False, "can_prepare": compiled["can_prepare"],
                      "contract_sha256": compiled["contract_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
