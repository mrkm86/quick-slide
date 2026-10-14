"""既存の PPTX の取り込み（docs/adr/0011-import-pptx.md）。**取り込みのデッキの違いをここに閉じ込める**（`motion.py` と同じ考え）。

- 取り込みは「プレゼン」（html-slide）のデッキになる。投入は `POST /api/imports`（本文は PPTX のバイト列そのまま）
- 台帳: 取り込み（intakes）の出所は `file`、原稿は空。payload の `import` に元のファイルの名前・大きさ・sha256
- 仕事: 台本係の代わりに、エンジンの `import` で台本を起こす（`run_import`）。ページの id は `p-001` …、画像は台本の
  フォルダからの相対 `imported/media/…`。台本の版の書き手は `import`
- 描く回の引数に `imported: true`。ブランドのロゴ・フッタ・ページ番号は足さない（元の PPTX にもう入っている）。
  テーマは PPTX のテーマ（あとで型のページを足すとき、元のページと色をそろえる）
- 元の PPTX は `decks/<デッキ>/source/source.pptx` に残す（画面にも書き出しにも出さない）
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .paths import DataRoot

log = logging.getLogger("quick_slide.importing")

# 受ける PPTX の大きさの上限（写真の多いデッキは 200 MB を超える。エンジンは展開して 2 GiB まで読む）
MAX_PPTX_BYTES = 512 * 1024 * 1024
# エンジンの import の時間の上限（116 枚・200 MB で約 20 秒。SVG を描く Chrome の分も見る）
IMPORT_TIMEOUT_SECONDS = 900
PPTX_SUFFIXES = (".pptx", ".pptm", ".ppsx", ".potx")
ZIP_MAGIC = b"PK\x03\x04"
MAX_FILENAME_CHARS = 200
MEDIA_PREFIX = "imported/"


class ImportRefused(Exception):
    """受けられない PPTX（形・大きさ）。サーバは 400 にする。"""


def clean_filename(raw: str | None) -> str:
    """元のファイルの名前（見出しの仮の題に使う）。パスの区切り・制御文字を落とす。"""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:MAX_FILENAME_CHARS] or "slides.pptx"


# デッキの名前の上限（画面の名前を変える口と同じ `deck_routes.TITLE_MAX_LENGTH`）
MAX_TITLE_CHARS = 200


def title_of(filename: str) -> str:
    """デッキの名前: 元のファイル名から拡張子（.pptx など）を外したもの（利用者と決めた・2026-10-14）。"""
    stem = filename[: -len(filename.rsplit(".", 1)[1]) - 1] if filename.lower().endswith(PPTX_SUFFIXES) else filename
    return stem.strip()[:MAX_TITLE_CHARS] or "取り込んだスライド"


def check_upload(path: Path, filename: str) -> dict[str, Any]:
    """受けた PPTX の形を確かめ、控えに残す情報（名前・大きさ・sha256）を返す。だめなら ImportRefused。"""
    if not filename.lower().endswith(PPTX_SUFFIXES):
        raise ImportRefused("PowerPoint のファイル（.pptx）を選んでください。")
    size = path.stat().st_size
    if size == 0:
        raise ImportRefused("ファイルが空です。")
    if size > MAX_PPTX_BYTES:
        raise ImportRefused(f"ファイルが大きすぎます（上限 {MAX_PPTX_BYTES // (1024 * 1024)} MB）。")
    with path.open("rb") as f:
        if f.read(4) != ZIP_MAGIC:
            raise ImportRefused("PowerPoint のファイル（.pptx）として読めません。")
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {"name": filename, "bytes": size, "sha256": h.hexdigest()}


def is_import(intake: dict[str, Any] | None) -> bool:
    if not intake or intake.get("origin_kind") != "file":
        return False
    payload = intake.get("payload") or {}
    return isinstance(payload.get("import"), dict)


def is_imported_run(args: dict[str, Any]) -> bool:
    return bool(args.get("imported"))


def keep_source(root: DataRoot, deck_id: str, upload: Path) -> Path:
    """受けた PPTX をデッキの置き場へ移す。"""
    dest = root.import_source_path(deck_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(upload), dest)
    return dest


def to_script(raw: dict[str, Any]) -> dict[str, Any]:
    """エンジンの import の台本 → Quick Slide の台本の版。ページに id を振り、画像を台本のフォルダからの相対に直す。"""
    slides = []
    for i, s in enumerate(raw.get("slides") or [], 1):
        if not isinstance(s, dict):
            continue
        page = {"id": f"p-{i:03d}", **{k: v for k, v in s.items() if k != "id"}}
        if isinstance(page.get("html"), str):
            page["html"] = page["html"].replace('src="media/', f'src="{MEDIA_PREFIX}media/')
        slides.append(page)
    out = {"title": raw.get("title") or "", "slides": slides}
    if raw.get("theme"):
        out["theme"] = f"{MEDIA_PREFIX}{raw['theme']}"
    return out


def run_engine(python: str, skills_dir: Path, source: Path, out: Path, *, env: dict[str, str],
               timeout: float = IMPORT_TIMEOUT_SECONDS, log_path: Path | None = None) -> tuple[int, str]:
    """エンジンの `import` を流す。(終了コード, stderr の最後) を返す。"""
    entry = skills_dir / "html-slide" / "scripts" / "html_slide.py"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run([python, str(entry), "import", "--pptx", str(source), "--out", str(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 1, "取り込みが時間内に終わりませんでした。"
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(r.stdout + "\n--- stderr ---\n" + r.stderr, encoding="utf-8")
    tail = "\n".join(line for line in r.stderr.splitlines() if line.startswith("NG:"))[-500:]
    return r.returncode, tail


def report_of(out: Path) -> dict[str, Any]:
    try:
        return json.loads((out / "import-report.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def report_lines(rep: dict[str, Any], limit: int = 40) -> list[str]:
    """取り込みの報告（写せなかったもの・近似したもの）を、画面と記録に出す 1 行ずつの文に。"""
    out = []
    for it in rep.get("items") or []:
        where = f"{it.get('slide')}枚目" if it.get("slide") else "全体"
        n = it.get("count") or 1
        out.append(f"{where}: {it.get('what')} — {it.get('how')}" + (f"（{n} か所）" if n > 1 else ""))
    return out[:limit]


# ---- 取り込んだページの文字を、見た目を保ったまま直す（docs/adr/0014-imported-text-edits.md）----
# ページの HTML のタグの間の文字（見出し・各行・箇条書きの印）に番号を振って台本係に渡し、番号ごとの直した文字だけを
# 受け取って差し替える。位置・色・書式・アニメーションは変えない。HTML はエンジンの import が書いたもの（スクリプトも
# コメントも無い）なので、タグの間の文字はこの形で拾える
_TEXT_NODE = re.compile(r">([^<>]*\S[^<>]*)<")
# 箇条書きの印だけの文字（台本係に「直さない」と言う）
_BULLETS = set("•・◦▪▫■□●○◆◇▶►‣⁃-–—*※✓✔")


def text_segments(html: str) -> list[dict[str, Any]]:
    """ページの文字の並び（{n, text, bullet}。n は 1 から）。"""
    import html as htmllib

    out = []
    for i, m in enumerate(_TEXT_NODE.finditer(html or ""), 1):
        text = htmllib.unescape(m.group(1)).strip()
        out.append({"n": i, "text": text, "bullet": bool(text) and all(c in _BULLETS for c in text)})
    return out


def replace_texts(html: str, changes: dict[int, str]) -> str:
    """番号の文字だけを差し替えた HTML（前後の空白は元のまま残す）。"""
    import html as htmllib

    count = 0

    def sub(m: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        if count not in changes:
            return m.group(0)
        raw = m.group(1)
        lead = raw[:len(raw) - len(raw.lstrip())]
        tail = raw[len(raw.rstrip()):]
        return ">" + lead + htmllib.escape(changes[count], quote=False) + tail + "<"

    return _TEXT_NODE.sub(sub, html or "")
