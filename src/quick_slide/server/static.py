"""fail-closed の静的配信と asset ID 配信（I1 / I8。HTTP は任意パスを受け取らず、許可した asset ID だけ配信する）。

性質として守ること:
- **同梱 `web/app/` の外側の 1 バイトも配信しない。** 拡張子と Content-Type は許可リスト方式。
- URL は 1 回だけ percent デコードし、その後は ASCII の狭い文字集合 `[A-Za-z0-9_-]` と `.` `/` 以外を拒否する。
  `..` / 先頭ドット（`.git` 等）/ 末尾ドット・空白 / バックスラッシュ / NUL / 非 ASCII（NFC/NFD の揺れごと）/
  二重エンコード（デコード後に `%` が残る）は、この `decode_path` の段階で落ちる。
  先頭の連続スラッシュ（`//static/x` 等）はここには現れない。要求行の正規化で 1 本に吸収されるため
  （実測: `GET //static/app.js` は 200 で `app.js` を返す。配信されるバイト列は許可リストのファイルそのもの
  で、逸脱は起きていない）。一方、パス内部の空セグメント（`/static//x` の `//`、`decode_path` を通った後の
  文字列に残る）はここではなく次の段（`split_segments`。空文字列は `_SEGMENT_RE` に一致しない）で落ちる
  （実測: `GET /static//app.js` は 404）。
- ディレクトリを 1 段ずつ `os.listdir` と**文字列一致**で降りる（macOS の大文字小文字非区別を潰す）。
  途中に symlink があれば拒否（外を指すかどうかに関わらず。判定を要らなくする）。
- 最後に `os.path.realpath` がルート配下であることを**コード側で強制**する（上の全段が破れても最終防衛線が残る）。
- 開くのは `O_NOFOLLOW` で、`fstat` の inode が `lstat` と一致する通常ファイルだけ。

asset 配信は `assets` 行の `relative_path` を同じ手順でデータ置き場の `assets/` 配下に閉じ込め、
`media_type` が拡張子の許可リストと一致するものだけ返す（`config/` や `db/` を指す行は行が存在しても 404）。
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes

from ..ids import is_uuid

# 同梱 UI の許可リスト（拡張子 → Content-Type）。ここに無いものは配信しない。
STATIC_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}
# asset ID 配信の許可リスト。行の media_type と一致しなければ配信しない。
ASSET_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    # 書き出し（PPTX）。許可リストであって任意パス配信ではない
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
STATIC_PREFIX = "/static/"
MAX_SEGMENTS = 8
MAX_SEGMENT_CHARS = 128
MAX_FILE_BYTES = 64 * 1024 * 1024

# 1 セグメントに許す形: 英数・`_`・`-` で始まり、ドットは内部区切りにだけ使える（先頭・末尾・連続は不可）
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")


@dataclass(frozen=True)
class ResolvedFile:
    path: Path
    content_type: str
    size: int
    inode: tuple[int, int]  # (st_dev, st_ino)。open 後に fstat と突き合わせる

    def read(self) -> bytes | None:
        """symlink を辿らずに開き、lstat と同じ通常ファイルであることを確かめてから読む。違えば None。"""
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(self.path, flags)
        except OSError:
            return None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != self.inode or st.st_size > MAX_FILE_BYTES:
                return None
            chunks = []
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        except OSError:
            return None
        finally:
            os.close(fd)


def decode_path(raw: str) -> str | None:
    """URL のパス部を 1 回だけデコードして返す。許可文字集合の外なら None（fail-closed）。"""
    if not isinstance(raw, str) or not raw.startswith("/"):
        return None
    if any(ord(c) < 0x21 or ord(c) > 0x7E for c in raw):
        return None  # 制御文字・空白・非 ASCII（生の UTF-8、NFD/NFC どちらも）は最初から拒否
    try:
        decoded = unquote_to_bytes(raw).decode("ascii")
    except UnicodeDecodeError:
        return None
    if "%" in decoded or "\\" in decoded or "\x00" in decoded:
        return None  # 二重エンコード・バックスラッシュ・NUL
    if any(ord(c) < 0x21 or ord(c) > 0x7E for c in decoded):
        return None
    return decoded


def split_segments(rel: str) -> list[str] | None:
    """`a/b/c.ext` を検証済みのセグメント列にする。空・`.`・`..`・先頭ドット・末尾ドット・長すぎは None。"""
    if not rel:
        return None
    segments = rel.split("/")
    if len(segments) > MAX_SEGMENTS:
        return None
    for seg in segments:
        if not seg or len(seg) > MAX_SEGMENT_CHARS or not _SEGMENT_RE.match(seg):
            return None
    return segments


def confine(path: Path, root: Path) -> bool:
    """最終防衛線: 実体の realpath がルート（realpath 済み）の**配下**にあること。ルート自身は不可。"""
    real = os.path.realpath(path)
    root_s = str(root)
    if real == root_s:
        return False
    try:
        return os.path.commonpath([real, root_s]) == root_s
    except ValueError:
        return False


def walk_no_symlinks(base: Path, segments: list[str]) -> Path | None:
    """`base` から 1 段ずつ、`os.listdir` の文字列一致で降りる。symlink・不在・ディレクトリでない中間は None。"""
    cur = base
    for i, seg in enumerate(segments):
        try:
            entries = os.listdir(cur)
        except OSError:
            return None
        if seg not in entries:
            return None
        cur = cur / seg
        try:
            st = os.lstat(cur)
        except OSError:
            return None
        if stat.S_ISLNK(st.st_mode):
            return None
        last = i == len(segments) - 1
        if last and not stat.S_ISREG(st.st_mode):
            return None
        if not last and not stat.S_ISDIR(st.st_mode):
            return None
    return cur


def _resolved(path: Path, root: Path, content_type: str) -> ResolvedFile | None:
    if not confine(path, root):
        return None
    if os.path.realpath(path) != str(path):
        return None  # 経路上のどこかに symlink があった（walk が見落としても realpath が違う）
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        return None
    return ResolvedFile(path=path, content_type=content_type, size=st.st_size, inode=(st.st_dev, st.st_ino))


class StaticRoot:
    """同梱 UI のルート。`resolve("/static/app.js")` → ResolvedFile か None。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(os.path.realpath(root))

    @property
    def available(self) -> bool:
        return self.root.is_dir()

    def resolve(self, url_path: str) -> ResolvedFile | None:
        decoded = decode_path(url_path)
        if decoded is None or not decoded.startswith(STATIC_PREFIX):
            return None
        segments = split_segments(decoded[len(STATIC_PREFIX):])
        if segments is None:
            return None
        ext = os.path.splitext(segments[-1])[1]
        content_type = STATIC_TYPES.get(ext)  # 大文字拡張子はここで落ちる（キーは小文字のみ）
        if content_type is None or not self.available:
            return None
        path = walk_no_symlinks(self.root, segments)
        if path is None:
            return None
        return _resolved(path, self.root, content_type)

    def index(self) -> ResolvedFile | None:
        return self.resolve(STATIC_PREFIX + "index.html")


class AssetFiles:
    """`assets` 行 → データ置き場 `assets/` 配下のファイル。行があっても許可リスト外・外側・symlink なら None。"""

    def __init__(self, data_root: Path | str, assets_dir: Path | str, trash_dir: Path | str | None = None) -> None:
        self.data_root = Path(os.path.realpath(data_root))
        self.assets_dir = Path(os.path.realpath(assets_dir))
        self.trash_dir = None if trash_dir is None else Path(os.path.realpath(trash_dir))

    def resolve(self, row: dict[str, Any]) -> ResolvedFile | None:
        return self._resolve_under(row, "ready", self.data_root, self.assets_dir)

    def resolve_trashed(self, row: dict[str, Any], deletion_task_id: str) -> ResolvedFile | None:
        """ごみ箱へ退避した画像。**`resolve` との違いは根と求める state だけ。**

        退避は `.trash/<deletion_task_id>/<relative_path>` への rename なので、根をそこへ差し替えれば
        同じ検査がそのまま効く。`deletion_task_id` が違えばファイルがそこに無いので降りられない。
        """
        if self.trash_dir is None or not is_uuid(deletion_task_id) or deletion_task_id != deletion_task_id.lower():
            return None
        base = self.trash_dir / deletion_task_id
        return self._resolve_under(row, "trashed", base, base)

    def _resolve_under(self, row: dict[str, Any], want_state: str, root: Path, confine_to: Path) -> ResolvedFile | None:
        if not isinstance(row, dict) or row.get("state") != want_state:
            return None
        rel = row.get("relative_path")
        if not isinstance(rel, str) or rel.startswith("/") or "\\" in rel or "\x00" in rel:
            return None
        if any(ord(c) < 0x21 or ord(c) > 0x7E for c in rel):
            return None
        segments = split_segments(rel)
        if segments is None or len(segments) < 2 or segments[0] != "assets":
            return None
        ext = os.path.splitext(segments[-1])[1]
        content_type = ASSET_TYPES.get(ext)
        if content_type is None or row.get("media_type") != content_type:
            return None
        path = walk_no_symlinks(root, segments)
        if path is None:
            return None
        resolved = _resolved(path, confine_to, content_type)
        if resolved is None:
            return None
        declared = row.get("bytes")
        if isinstance(declared, int) and not isinstance(declared, bool) and declared != resolved.size:
            return None  # 行と現物が食い違う asset は配信しない（I5: 測っていないものを出さない）
        return resolved
