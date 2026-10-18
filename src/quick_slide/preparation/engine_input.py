"""確定台本 → エンジンに渡す台本（engine view）の最小の変換器（§4.1。6a は IMAGE だけ）。

台本の `_references`・`_preparation` を取り除き、準備済みの参照（コピー済みのファイル）をサーバが作る値に変える:
- ピッチ（image-slide）: character → ページの `_characters: [{name, image, pose}]`（image はコピー先の絶対パス）、
  image → ページの `image: {"path": コピー先}`（生成の枠の指定を、持ち込んだ絵そのものに置き換える）
- プレゼン（html-slide）: 参照を持つページがあれば「まだ使えない」で止める（HTML の変換は 7b）。参照を持たないページは除くだけ
- 構図の見本・全ページ共通の参照は、この PR では変換しない（カタログの段で役割から外している）

台本係が書いた `_characters` は検査で断っているので、ここに来たら止める（サーバ以外が作った値をエンジンに通さない）。
検査（`check_candidate`）と描画・直しが同じこの結果を使う（検査のときだけ別の参照を使わない）。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .errors import EngineInputUnsupported
from .references import PREP_KEY, REF_KEY

HTML_UNSUPPORTED = ("プレゼン（HTML）では、テンプレートの素材をページに置く変換がまだ使えないため、止めました"
                    "（素材を使わない台本なら、そのまま進められます）。")


def materialize_engine_input(data: dict[str, Any], *, mode: str, gen_dir: Path | None,
                             assets: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    out = copy.deepcopy(data)
    out.pop(PREP_KEY, None)
    for slide in out.get("slides") or []:
        if not isinstance(slide, dict):
            continue
        if "_characters" in slide:
            raise EngineInputUnsupported("台本に手書きの _characters があります（サーバが作る値だけをエンジンに渡します）。")
        refs = slide.pop(REF_KEY, None)
        if not refs:
            continue
        if mode != "pitch":
            raise EngineInputUnsupported(HTML_UNSUPPORTED)
        chars: list[dict[str, str]] = []
        placed: set[str] = set()
        for e in refs:
            files = ((assets or {}).get(e["id"]) or {}).get("files")
            if not files or gen_dir is None:
                raise EngineInputUnsupported(f"素材 {e['id']} がまだコピーされていません。")
            path = str(gen_dir / files[0]["path"])
            if e["role"] == "character":
                chars.append({"name": e["name"].strip(), "image": path, "pose": (e.get("pose") or "").strip()})
            elif e["role"] == "image":
                if "image" in placed:
                    raise EngineInputUnsupported("1 ページに持込画像を 2 つ以上置く変換はまだ使えません。")
                placed.add("image")
                slide["image"] = {"path": path}
            else:
                raise EngineInputUnsupported(f"役割 {e['role']} は、まだエンジンの入力に変換できません。")
        if chars:
            slide["_characters"] = chars
    return out
