"""デッキの作業フォルダ（エンジンの `--out`）と、描いた回の控え（P3）。

**なぜ作業フォルダを 1 つにするか。** 描いた絵はエンジンのキャッシュの区画にあり、区画は持ち主のフォルダで引く
（image-slide は `--out`・html-slide は台本のフォルダ）。P2 は回ごとに新しい `runs/<回>/` へ描いていたので、
ピッチでは回ごとに新しい区画ができ、直すたびに全ページを描き直していた（枠の無駄・revise の土台も無い）。
P3 は**どの回もデッキの `work/` に描き**（台本は `scripts/engine.json`）、終わった回の出力を `runs/<回>/` へ
控える。回は不変の控えで、画面と書き出しは控えを読む。

控え方（`snapshot`）:

- ページの画像・PPTX・PDF・サムネイルは**ハードリンク**（同じ中身を二重に持たない）。エンジンは出力を
  上書きで書くことがある（PIL の save・Chrome の撮影）ので、**エンジンを呼ぶ前に作業フォルダの出力を消す**
  （`clear_outputs`）。消してから書かせれば、控えの側の中身は変わらない
- 記録（`build.json`・`deck-info.json`・`inspect.md`）は写す（小さい）
- **絵の控え**（`pictures/`）: 版スタックから戻すときに、エンジンが読む場所へ絵を戻すための控え
  - ピッチ: キャッシュの区画の `slide-NN.png`（ロゴ・透かしの前の絵）と鍵の `slide-NN.json` → `pictures/<ページの id>.png|json`
  - プレゼン: 生成の枠の絵 `gen-<鍵>.png`（と `.json`）→ `pictures/gen-<鍵>.png|json`
  - 区画の絵はエンジンが名前の付け替え（rename）で入れ替えるので、ハードリンクの控えは書き換わらない
- `pictures/index.json`: ページの id → 何枚目だったか・枠（プレゼン）

**ピッチの区画の並べ直し**（`arrange_pitch_cache`）: image-slide は区画の絵を**何枚目か**（`slide-NN`）で持ち、
鍵（描く指示の sha。何枚目かは入らない）が同じなら使い回す。ページを差し込む・隠す・並べ替えると番号がずれて
全部描き直しになるので、エンジンを呼ぶ前に、前に描いたときの並び（`work/layout.json`）から区画の絵を新しい並びへ
付け替える。鍵が合わなければエンジンが描き直すだけなので、外れた絵を置いても壊れない。
区画の場所はエンジンの記録（`cache`）から読む。まだ無いときは、前に知った区画と同じ根っこに、エンジンと同じ
名前の付け方（フォルダ名-絶対パスの sha256 の先頭 12 桁）で求める（`pitch_area_for`）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote

OUTPUT_FILES = ("deck.pptx", "deck.pdf", "build.json", "deck-info.json", "inspect.md", "deck.html")
RECORD_FILES = ("build.json", "deck-info.json", "inspect.md")
LAYOUT = "layout.json"
STAGE = ".quick-slide-stage"
# 発表の画面（html-slide の段ごとの再生）が読む、回ごとの HTML と画像・字体の置き場（回のフォルダの中）
PRESENT = "present"
PRESENT_FONT_TYPES = frozenset({".ttf", ".otf", ".woff", ".woff2"})
_FONT_URL = re.compile(r'url\("file://([^"]+)"\)')
_ASSET_SRC = re.compile(r'\bsrc="assets/([^"/]+)"')


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _link(src: Path, dst: Path) -> None:
    """dst に src を張る（ハードリンク。別のボリュームなら写す）。dst は先に消す（前の張りに書き込まない）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        os.link(src, tmp)
    except OSError:
        shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _copy(src: Path, dst: Path) -> None:
    """写す（新しい中身。ハードリンクにしない）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_engine_script(path: Path, data: dict[str, Any]) -> Path:
    """エンジンに渡す台本（`scriptdoc.engine_view` 済み）を書く。"""
    from ..scriptdoc import dumps

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(dumps(data))
    os.replace(tmp, path)
    return path


def clear_outputs(work: Path) -> None:
    """エンジンを呼ぶ前に、作業フォルダの出力を消す（控えとハードリンクでつながった中身を上書きさせない）。"""
    for name in OUTPUT_FILES:
        (work / name).unlink(missing_ok=True)
    for p in work.glob("thumbs*.png"):
        p.unlink(missing_ok=True)
    slides = work / "slides"
    if slides.is_dir():
        for p in slides.glob("slide-*.png"):
            p.unlink(missing_ok=True)


def record_name(mode: str) -> str:
    return "build.json" if mode == "pitch" else "deck-info.json"


def engine_record(mode: str, folder: Path) -> dict[str, Any]:
    return read_json(folder / record_name(mode))


# --- 控え ---------------------------------------------------------------------------------------------

def snapshot(mode: str, work: Path, run_dir: Path, pages: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """作業フォルダの出力を描いた回のフォルダへ控える。戻り値は {番号: {"file": 控えの絵か None, "picture_sha"}}。

    `pages` は `scriptdoc.output_pages`（番号・ページの id）。ページの画像が無い番号は file が None。
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in ("deck.pptx", "deck.pdf"):
        if (work / name).is_file():
            _link(work / name, run_dir / name)
    for p in sorted(work.glob("thumbs*.png")):
        _link(p, run_dir / p.name)
    for name in RECORD_FILES:
        if (work / name).is_file():
            _copy(work / name, run_dir / name)
    rec = engine_record(mode, work)
    pictures = run_dir / "pictures"
    index: dict[str, Any] = {"mode": mode, "cache": rec.get("cache"), "pages": {}}
    out: dict[int, dict[str, Any]] = {}
    images = rec.get("images") if isinstance(rec.get("images"), list) else []
    area = Path(rec["cache"]) if mode == "pitch" and isinstance(rec.get("cache"), str) else None
    for p in pages:
        no, key = p["no"], p["key"]
        png = work / "slides" / f"slide-{no:02d}.png"
        shot = None
        if png.is_file():
            shot = run_dir / "slides" / png.name
            _link(png, shot)
        entry: dict[str, Any] = {"no": no}
        picture_sha = None
        if mode == "pitch":
            raw = area / f"slide-{no:02d}.png" if area else None
            if raw is not None and raw.is_file():
                _link(raw, pictures / f"{key}.png")
                meta = raw.with_suffix(".json")
                if meta.is_file():
                    _copy(meta, pictures / f"{key}.json")
                picture_sha = file_sha(pictures / f"{key}.png")
                entry["picture"] = f"{key}.png"
        else:
            parts, slots = [], []
            for x in images:
                if not isinstance(x, dict) or x.get("slide") != no:
                    continue
                src = Path(str(x.get("path") or ""))
                row = {"slot": x.get("slot"), "origin": x.get("origin"), "key": x.get("key")}
                if x.get("exists") and src.is_file():
                    sha = file_sha(src)
                    row["sha256"] = sha
                    if x.get("origin") == "generated" and x.get("key"):
                        _link(src, pictures / f"gen-{x['key']}.png")
                        side = src.with_suffix(".json")
                        if side.is_file():
                            _copy(side, pictures / f"gen-{x['key']}.json")
                        row["file"] = f"gen-{x['key']}.png"
                    parts.append(f"{x.get('slot')}:{sha}")
                else:
                    parts.append(f"{x.get('slot')}:-")
                slots.append(row)
            entry["slots"] = slots
            picture_sha = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
        index["pages"][key] = entry
        out[no] = {"file": str(shot) if shot else None, "picture_sha": picture_sha}
    write_json(pictures / "index.json", index)
    return out


def present_font_name(path: str) -> str:
    """字体ファイルの、発表の画面での名前（元のパスの sha256 の先頭 16 桁＋拡張子）。配る側も同じ式で引く。"""
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16] + Path(path).suffix.lower()


def snapshot_present(work: Path, run_dir: Path) -> bool:
    """発表の画面（html-slide の段ごとの再生）のために、deck.html とそれが読む画像・字体を回のフォルダへ控える。

    - `deck.html` は写す（小さい。エンジンは次の回の前に作業フォルダの出力を消す）
    - 画像（`assets/`）と字体は**ハードリンク**（別のボリュームなら写す）。エンジンは `assets/` に書く前に
      同じ名前を消す（deck.py）ので、控えの側の中身は変わらない
    - 控えるのは deck.html が読むものだけ。字体は拡張子が許可リストのものだけ（元のパスは名前の式に使うだけで、配信には出さない）
    deck.html が無い（エンジンが書かなかった）ときは何もせず False。
    """
    src = work / "deck.html"
    if not src.is_file():
        return False
    html = src.read_text(encoding="utf-8")
    dest = run_dir / PRESENT
    _copy(src, dest / "deck.html")
    for m in _ASSET_SRC.finditer(html):
        asset = work / "assets" / m.group(1)
        if asset.is_file():
            _link(asset, dest / "assets" / m.group(1))
    for m in _FONT_URL.finditer(html):
        path = unquote(m.group(1))
        font = Path(path)
        if font.suffix.lower() in PRESENT_FONT_TYPES and font.is_file():
            _link(font, dest / "fonts" / present_font_name(path))
    return True


def snapshot_index(run_dir: Path) -> dict[str, Any]:
    return read_json(run_dir / "pictures" / "index.json")


# --- ピッチ: 区画の絵を新しい並びへ付け替える -------------------------------------------------------------

def pitch_area_for(work: Path, known_area: str | Path) -> Path:
    """作業フォルダの区画（image-slide の `cache.area_path` と同じ名前の付け方）。根っこは知っている区画と同じ。"""
    owner = Path(work).expanduser().resolve()
    name = re.sub(r"[^\w.-]", "_", owner.name)[:40] or "root"
    return Path(known_area).parent / f"{name}-{hashlib.sha256(str(owner).encode()).hexdigest()[:12]}"


def read_layout(work: Path) -> dict[str, Any]:
    return read_json(work / LAYOUT)


def write_layout(work: Path, keys: list[str], cache: str | None) -> None:
    write_json(work / LAYOUT, {"ids": keys, "cache": cache})


def arrange_pitch_cache(work: Path, keys: list[str], *, area: Path | None,
                        seeds: dict[str, tuple[Path, Path | None]] | None = None,
                        aliases: dict[str, str] | None = None,
                        fallback: Callable[[str], tuple[Path, Path | None] | None] | None = None) -> dict[str, str]:
    """区画の `slide-NN.png|json` を、新しい並び `keys`（ページの id）へ付け替える。戻り値は id → 何を置いたか。

    置く絵の選び方（上から）: `seeds`（版から戻す絵）→ 区画にある、そのページの前の絵 → 複製元（`aliases`）の絵 →
    控えの絵（`fallback`）→ 置かない（エンジンが描く）。鍵が合わない絵はエンジンが描き直すので、外れても壊れない。
    """
    seeds = seeds or {}
    aliases = aliases or {}
    layout = read_layout(work)
    old = layout.get("ids") if isinstance(layout.get("ids"), list) else None
    placed: dict[str, str] = {}
    if area is None:
        write_layout(work, keys, None)
        return placed
    area.mkdir(parents=True, exist_ok=True)
    stage = area / STAGE
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    if old is not None:
        for m, q in enumerate(old, 1):
            for ext in (".png", ".json"):
                f = area / f"slide-{m:02d}{ext}"
                if f.is_file() and not (stage / f"{q}{ext}").exists():
                    os.replace(f, stage / f"{q}{ext}")
    for n, k in enumerate(keys, 1):
        dst_png, dst_json = area / f"slide-{n:02d}.png", area / f"slide-{n:02d}.json"
        src: tuple[Path, Path | None] | None = None
        how = ""
        if k in seeds:
            src, how = seeds[k], "seed"
        elif (stage / f"{k}.png").is_file():
            src, how = (stage / f"{k}.png", stage / f"{k}.json"), "moved"
        elif k in aliases and (stage / f"{aliases[k]}.png").is_file():
            src, how = (stage / f"{aliases[k]}.png", stage / f"{aliases[k]}.json"), "alias"
        elif fallback is not None:
            got = fallback(k) or (fallback(aliases[k]) if k in aliases else None)
            if got is not None:
                src, how = got, "snapshot"
        if src is None and old is None:
            continue  # 前の並びが分からない: 区画の絵には触らない（鍵が合えば使われ、合わなければ描き直す）
        dst_png.unlink(missing_ok=True)
        dst_json.unlink(missing_ok=True)
        if src is None or not src[0].is_file():
            continue
        _link(src[0], dst_png)
        if src[1] is not None and src[1].is_file():
            _link(src[1], dst_json)
        placed[k] = how
    shutil.rmtree(stage, ignore_errors=True)
    write_layout(work, keys, str(area))
    return placed


def pitch_picture(run_dir: Path, key: str, legacy_no: int | None = None) -> tuple[Path, Path | None] | None:
    """描いた回の控えの、そのページの絵（ピッチ）。P2 の回（控えが無い）は、その回の区画の何枚目かから。"""
    png = run_dir / "pictures" / f"{key}.png"
    if png.is_file():
        return png, run_dir / "pictures" / f"{key}.json"
    if legacy_no is None:
        return None
    area = engine_record("pitch", run_dir).get("cache")
    if not isinstance(area, str):
        return None
    raw = Path(area) / f"slide-{legacy_no:02d}.png"
    return (raw, raw.with_suffix(".json")) if raw.is_file() else None


# --- プレゼン: 生成の枠の絵を戻す ---------------------------------------------------------------------------

def restore_presentation_pictures(run_dir: Path, key: str) -> int:
    """控えの回のそのページの生成の絵を、html-slide が読む場所（区画の `gen-<鍵>.png`）へ戻す。戻した数。

    鍵は描く指示から作るので、ページの中身を同じに戻せば同じ鍵になり、エンジンは描かずにこの絵を使う。
    名前の付け替えで入れるので、ほかの回の控え（ハードリンク）は書き換わらない。
    """
    index = snapshot_index(run_dir)
    gen_dir = engine_record("presentation", run_dir).get("cache")
    entry = (index.get("pages") or {}).get(key) or {}
    if not isinstance(gen_dir, str):
        return 0
    n = 0
    for slot in entry.get("slots") or []:
        name = slot.get("file")
        if not name:
            continue
        src = run_dir / "pictures" / name
        if not src.is_file():
            continue
        _copy(src, Path(gen_dir) / name)
        side = src.with_suffix(".json")
        if side.is_file():
            _copy(side, Path(gen_dir) / side.name)
        n += 1
    return n


def presentation_slots(work: Path, no: int) -> list[dict[str, Any]]:
    """いまの作業フォルダの記録から、そのページ（何枚目か）の画像の枠（名前・出どころ・絵があるか）。"""
    rec = engine_record("presentation", work)
    out = []
    for x in rec.get("images") or []:
        if isinstance(x, dict) and x.get("slide") == no:
            out.append({"slot": x.get("slot"), "origin": x.get("origin"), "exists": bool(x.get("exists")),
                        "prompt": x.get("prompt") or ""})
    return out
