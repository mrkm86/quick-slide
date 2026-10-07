"""描いた絵の置き場（キャッシュ）。image-slide の ADR 0015・html-slide の ADR 0014。

Codex で描いた絵は、出力フォルダや台本のそばではなく、OS のキャッシュの置き場に区画を切って置く。
区画は持ち主のフォルダ（image-slide は出力フォルダ・html-slide は台本のフォルダ）ごと。
合計が上限を超えたら、最後に使った時刻の古い区画から丸ごと消す。今の区画と、少し前に使われた区画（並行して走っている実行）は消さない。

置き場の根っこは places.py（macOS は ~/Library/Caches/quick-slide/<スキル>/。ほかの OS はロードマップ）。
環境変数 <スキル>_CACHE（例: IMAGE_SLIDE_CACHE）か設定の cache_dir で変えられる。上限は設定の cache_max_mb（0 は消さない）。
html-slide と同じファイル（テストがバイト単位で見比べる）。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import time

import places as PL

DEFAULT_MAX_MB = 1024     # 描いた絵は1枚約 2 MB。直す前の絵も入るので約500枚ぶん
RECENT = 600              # この秒数のうちに使われた区画は消さない（並行して走っている実行の分）
STAMP = "owner.json"      # 区画の印。持ち主のフォルダと最後に使った時刻。これが無いフォルダは消さない


def root(skill: str, cfg: dict) -> pathlib.Path:
    return PL.cache_root(skill, cfg)


def area_path(base: pathlib.Path, owner: pathlib.Path) -> pathlib.Path:
    """持ち主のフォルダの区画（作らない）。名前は「フォルダ名-絶対パスのハッシュ」。名前は人が見分けるためだけ。"""
    owner = pathlib.Path(owner).expanduser().resolve()
    name = re.sub(r"[^\w.-]", "_", owner.name)[:40] or "root"
    return base / f"{name}-{hashlib.sha256(str(owner).encode()).hexdigest()[:12]}"


def area(skill: str, cfg: dict, owner: pathlib.Path) -> pathlib.Path:
    """持ち主のフォルダの区画を作り、使った時刻を書く。"""
    d = area_path(root(skill, cfg), owner)
    d.mkdir(parents=True, exist_ok=True)
    (d / STAMP).write_text(json.dumps({"owner": str(pathlib.Path(owner).expanduser().resolve()),
                                       "used": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    return d


def size_of(d: pathlib.Path) -> int:
    total = 0
    for top, _, files in os.walk(d):
        for f in files:
            try:
                total += os.lstat(os.path.join(top, f)).st_size
            except OSError:
                pass
    return total


def cap_bytes(cfg: dict) -> int:
    v = cfg.get("cache_max_mb")
    mb = DEFAULT_MAX_MB if v is None else int(v)
    return max(0, mb) * 1024 * 1024


def trim(skill: str, cfg: dict, keep: pathlib.Path, log=None) -> list[str]:
    """合計が上限を超えていたら、最後に使った時刻の古い区画から消す。消した区画の持ち主の一覧を返す。"""
    cap = cap_bytes(cfg)
    base = root(skill, cfg)
    if cap == 0 or not base.is_dir():
        return []
    rows = []
    for d in base.iterdir():
        stamp = d / STAMP
        if d.is_dir() and not d.is_symlink() and stamp.is_file():
            try:
                owner = json.loads(stamp.read_text(encoding="utf-8")).get("owner", "")
            except (OSError, ValueError):
                owner = ""
            rows.append((stamp.stat().st_mtime, d, owner, size_of(d)))
    total = sum(r[3] for r in rows)
    removed, now = [], time.time()
    for used, d, owner, size in sorted(rows, key=lambda r: r[0]):
        if total <= cap:
            break
        if d.resolve() == pathlib.Path(keep).resolve() or now - used < RECENT:
            continue
        shutil.rmtree(d, ignore_errors=True)
        total -= size
        removed.append(owner or d.name)
        if log:
            log(f"キャッシュの上限（{cap // (1024 * 1024)} MB）を超えたので、古い区画を消した: {owner or d.name}（{size / 1e6:.0f} MB）")
    return removed
