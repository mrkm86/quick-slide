"""通常のファイル・フォルダの限定読取（§3.1・§3.3 の安全な取得）。**子プロセスを起こさない**。

読むのは UTF-8 の Markdown・テキスト・安全な JSON / YAML・PNG / JPEG（ヘッダ）だけ。PDF などは専用の抽出器が無いので
未対応として止める（パスだけを LLM に渡して読んだ扱いにしない）。フォルダはまず名前・形式・大きさの目録を作る。
ファイルは登録フォルダの下に閉じていることを `realpath` で確かめ、no-follow で開き、開いた後の `fstat` で通常ファイルと
大きさ・同一性を確かめる。読んでいる間に変わったら止める（前後の `fstat` と、コピーしたもののハッシュで見る）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import oscompat
from .errors import PreparationStopped
from .policy import DEFAULT_POLICY, Policy

SKILL_MARKERS = ("quick-slide-read.json", "SKILL.md")
# 通常のフォルダの説明書き（SKILL.md は skill の印なので、ここには入れない）
GUIDE_NAMES = ("README.md", "README.txt")
TEXT_EXTS = frozenset({".md", ".markdown", ".txt"})
JSON_EXTS = frozenset({".json"})
YAML_EXTS = frozenset({".yaml", ".yml"})
IMAGE_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
SUPPORTED_WORDS = "Markdown・テキスト・JSON・YAML・PNG・JPEG"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SOF_MARKERS = frozenset(set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC})


@dataclass(frozen=True)
class FileIdentity:
    size: int
    mtime_ns: int
    ino: int
    dev: int

    @classmethod
    def of(cls, st: os.stat_result) -> "FileIdentity":
        return cls(st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev)


@dataclass(frozen=True)
class ImageInfo:
    media_type: str
    width: int
    height: int


@dataclass(frozen=True)
class Entry:
    rel: str
    kind: str   # file / dir / symlink / other
    size: int


def label(kind: str) -> str:
    return "フォルダ" if kind == "directory" else "ファイル"


_WIN_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", *(f"COM{i}" for i in range(1, 10)),
                           *(f"LPT{i}" for i in range(1, 10))})
_WIN_SHORT = re.compile(r"~\d")
_REPARSE = 0x400   # FILE_ATTRIBUTE_REPARSE_POINT（junction・symlink）


def _check_rel(rel: str, windows: bool | None = None) -> list[str]:
    windows = os.name == "nt" if windows is None else windows
    parts = rel.split("/")
    if not rel or any(p in ("", ".", "..") for p in parts) or "\\" in rel or _CONTROL.search(rel) or "\n" in rel:
        raise PreparationStopped("素材のパスの形が正しくありません。", "path_invalid")
    if windows:
        for p in parts:
            # 代替データストリーム・末尾の点や空白・デバイス名・短縮名（8.3。確実に判定できないので断る）は開かない
            if ":" in p or p.endswith((".", " ")) or p.split(".")[0].upper() in _WIN_RESERVED or _WIN_SHORT.search(p):
                raise PreparationStopped("Windows で安全に開けない名前（デバイス名・短縮名・ストリームなど）の素材は使えません。",
                                         "path_invalid")
    return parts


def is_reparse(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _final_path(fd: int) -> str | None:
    """開いたハンドルの実体パス（Windows。GetFinalPathNameByHandle）。取れなければ None。"""
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(fd)
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetFinalPathNameByHandleW(ctypes.c_void_p(handle), buf, len(buf), 0)  # type: ignore[attr-defined]
        if n == 0 or n >= len(buf):
            return None
        out = buf.value
        return out[4:] if out.startswith("\\\\?\\") else out
    except (ImportError, OSError, AttributeError, ValueError):
        return None


def decode_text(data: bytes, what: str) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise PreparationStopped(f"{what}が UTF-8 のテキストではありません。UTF-8 で保存し直してください。", "not_utf8") from None
    if text.startswith("﻿"):
        text = text[1:]
    if _CONTROL.search(text):
        raise PreparationStopped(f"{what}に制御文字が含まれています。", "control_chars")
    return text


def parse_json_safe(text: str, what: str) -> object:
    """重複キー・NaN・Infinity を断る JSON。"""
    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError("duplicate key")
            out[k] = v
        return out

    def refuse(_: str):
        raise ValueError("non-finite number")

    try:
        return json.loads(text, object_pairs_hook=unique, parse_constant=refuse)
    except (ValueError, RecursionError):
        raise PreparationStopped(f"{what}が正しい JSON ではありません（重複したキーや NaN も使えません）。", "bad_json") from None


def parse_yaml_safe(text: str, what: str) -> object:
    import yaml

    try:
        docs = list(yaml.safe_load_all(text))
    except (yaml.YAMLError, RecursionError, ValueError):
        raise PreparationStopped(f"{what}が正しい YAML ではありません。", "bad_yaml") from None
    if len(docs) > 1:
        raise PreparationStopped(f"{what}に複数の YAML 文書が入っています（1 つだけにしてください）。", "bad_yaml")
    return docs[0] if docs else None


def sniff_image(fd: int, size: int) -> ImageInfo:
    """ヘッダだけから形式と大きさ（画素）を読む。画像を開いて復号はしない。壊れていれば ValueError。"""
    def at(offset: int, n: int) -> bytes:
        os.lseek(fd, offset, os.SEEK_SET)
        return os.read(fd, n)

    head = at(0, 32)
    if head.startswith(_PNG_SIGNATURE):
        if head[12:16] != b"IHDR" or len(head) < 24:
            raise ValueError("PNG のヘッダが壊れている")
        w, h = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
        info = ImageInfo("image/png", w, h)
    elif head[:2] == b"\xff\xd8":
        pos = 2
        info = None
        for _ in range(2000):
            seg = at(pos, 4)
            if len(seg) < 2 or seg[0] != 0xFF:
                break
            marker = seg[1]
            if marker == 0xFF:
                pos += 1
                continue
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                pos += 2
                continue
            if marker in (0xD9, 0xDA) or len(seg) < 4:
                break
            length = int.from_bytes(seg[2:4], "big")
            if marker in _SOF_MARKERS:
                body = at(pos + 4, 5)
                if len(body) < 5:
                    break
                info = ImageInfo("image/jpeg", int.from_bytes(body[3:5], "big"), int.from_bytes(body[1:3], "big"))
                break
            if length < 2:
                break
            pos += 2 + length
        if info is None:
            raise ValueError("JPEG のヘッダが壊れている")
    else:
        raise ValueError("PNG・JPEG ではない")
    if info.width < 1 or info.height < 1:
        raise ValueError("画像の大きさが 0")
    os.lseek(fd, 0, os.SEEK_SET)
    return info


class Reader:
    """限定読取。`on_read(path)` は読み終えた直後に呼ぶ（試験が「読んでいる間の変更」を起こす口）。"""

    def __init__(self, policy: Policy = DEFAULT_POLICY, on_read: Callable[[Path], None] | None = None) -> None:
        self.policy = policy
        self.on_read = on_read

    # --- 開く ---
    def open(self, root: Path, rel: str) -> tuple[int, os.stat_result, Path]:
        """登録フォルダ（`root`）の下の通常ファイルを開く。範囲外・symlink・junction・特殊ファイルは断る。

        POSIX: フォルダの FD から、各成分を no-follow で順に開く（確認と開く間の差し替えが効かない）。
        Windows: 各成分の reparse point を断り、開いたハンドルの実体パスが期待どおりか確かめる。保証できなければ止める。"""
        parts = _check_rel(rel)
        path = root.joinpath(*parts)
        fd = self._open_nt(root, parts, path) if os.name == "nt" else self._open_posix(root, parts)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise PreparationStopped("通常のファイルではない素材は使えません。", "not_regular")
        except BaseException:
            os.close(fd)
            raise
        return fd, st, path

    @staticmethod
    def _open_posix(root: Path, parts: list[str]) -> int:
        nofollow, directory = getattr(os, "O_NOFOLLOW", None), getattr(os, "O_DIRECTORY", None)
        if nofollow is None or directory is None or os.open not in os.supports_dir_fd:
            raise PreparationStopped("この環境では、登録した場所の外に出ない開き方を保証できないため、素材を読めません。", "unsafe_platform")
        base = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
        cur = -1
        try:
            cur = os.open(os.path.realpath(root), base | directory | getattr(os, "O_BINARY", 0))
            for p in parts[:-1]:
                nxt = os.open(p, base | directory | nofollow | getattr(os, "O_BINARY", 0), dir_fd=cur)
                os.close(cur)
                cur = nxt
            return os.open(parts[-1], base | nofollow | getattr(os, "O_BINARY", 0), dir_fd=cur)
        except OSError:
            raise PreparationStopped("素材のファイルを開けませんでした（見つからない・読めない・登録した場所の外を指すリンクです）。",
                                     "unreadable") from None
        finally:
            if cur >= 0:
                os.close(cur)

    @staticmethod
    def _open_nt(root: Path, parts: list[str], path: Path) -> int:
        real_root = os.path.realpath(root)
        cur = real_root
        try:
            for p in parts:
                cur = os.path.join(cur, p)
                if is_reparse(os.lstat(cur)) or os.path.islink(cur):
                    raise PreparationStopped("登録した場所の外を指しうるリンク（junction・シンボリックリンク）は使えません。",
                                             "outside_root")
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except OSError:
            raise PreparationStopped("素材のファイルを開けませんでした（見つからないか、読めません）。", "unreadable") from None
        final = _final_path(fd)
        if final is None or os.path.normcase(final) != os.path.normcase(os.path.join(real_root, *parts)):
            os.close(fd)
            raise PreparationStopped("開いたファイルの実体が登録した場所の中にあると確かめられないため、使えません。", "outside_root")
        return fd

    def _unchanged(self, fd: int, path: Path, before: FileIdentity) -> None:
        try:
            now = FileIdentity.of(os.fstat(fd))
            on_disk = FileIdentity.of(os.stat(path, follow_symlinks=False))
        except OSError:
            raise PreparationStopped("素材を読んでいる間にファイルが消えました。もう一度頼んでください。", "changed_during_read") from None
        if now != before or on_disk != before:
            raise PreparationStopped("素材を読んでいる間にファイルが変わりました。変更が終わってから、もう一度頼んでください。",
                                     "changed_during_read")

    # --- 読む ---
    def read_bytes(self, root: Path, rel: str, limit: int, what: str) -> tuple[bytes, str]:
        """上限まで読む（超えたら止める。切り詰めない）。(中身, sha256)。"""
        fd, st, path = self.open(root, rel)
        try:
            before = FileIdentity.of(st)
            if st.st_size > limit:
                raise PreparationStopped(f"{what}が大きすぎます（{st.st_size} バイト。上限 {limit} バイト）。", "limit_exceeded",
                                         {"limit": limit, "what": what})
            chunks, total = [], 0
            while True:
                chunk = os.read(fd, min(65536, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    raise PreparationStopped(f"{what}が大きすぎます（上限 {limit} バイト）。", "limit_exceeded",
                                             {"limit": limit, "what": what})
            if self.on_read is not None:
                self.on_read(path)
            self._unchanged(fd, path, before)
            data = b"".join(chunks)
            return data, hashlib.sha256(data).hexdigest()
        finally:
            os.close(fd)

    def sniff(self, root: Path, rel: str) -> tuple[ImageInfo, int]:
        """画像のヘッダだけ読み、大きさ（バイト・画素）の上限を確かめる。上限を超えたら PreparationStopped(limit_exceeded)、
        画像として読めなければ PreparationStopped(bad_image)。"""
        fd, st, _ = self.open(root, rel)
        try:
            if st.st_size > self.policy.image_bytes:
                raise PreparationStopped("画像が大きすぎます。", "limit_exceeded", {"limit": self.policy.image_bytes})
            try:
                info = sniff_image(fd, st.st_size)
            except (ValueError, OSError):
                raise PreparationStopped("画像として読めません（PNG・JPEG のヘッダが正しくありません）。", "bad_image") from None
            if info.width * info.height > self.policy.image_pixels:
                raise PreparationStopped("画像の画素数が多すぎます。", "limit_exceeded", {"limit": self.policy.image_pixels})
            return info, st.st_size
        finally:
            os.close(fd)

    # --- フォルダの目録 ---
    def inventory(self, root: Path, name: str) -> list[Entry]:
        """フォルダの中の名前・種類・大きさ（再帰。symlink は辿らない）。上限を超えたら止める（先頭だけに切らない）。"""
        out: list[Entry] = []
        stack: list[tuple[Path, str]] = [(root, "")]
        while stack:
            folder, prefix = stack.pop()
            try:
                it = os.scandir(folder)
            except OSError:
                raise PreparationStopped(f"リソース「{name}」のフォルダの中を読めませんでした。", "unreadable") from None
            with it:
              # 列挙しながら上限を見る（全件をメモリへ取ってから数えない）
              for e in it:
                rel = prefix + e.name
                try:
                    if e.is_symlink():
                        out.append(Entry(rel, "symlink", 0))
                    elif e.is_dir(follow_symlinks=False):
                        out.append(Entry(rel, "dir", 0))
                        stack.append((Path(e.path), rel + "/"))
                    elif e.is_file(follow_symlinks=False):
                        out.append(Entry(rel, "file", e.stat(follow_symlinks=False).st_size))
                    else:
                        out.append(Entry(rel, "other", 0))
                except OSError:
                    out.append(Entry(rel, "other", 0))
                if len(out) > self.policy.listing_items:
                    raise PreparationStopped(
                        f"リソース「{name}」のフォルダにファイルが多すぎます（{self.policy.listing_items} 件まで）。"
                        "フォルダを分けるか、使うものだけのフォルダにしてください。", "limit_exceeded",
                        {"limit": self.policy.listing_items, "what": "listing"})
        return sorted(out, key=lambda e: e.rel)

    # --- コピー ---
    def copy_image(self, root: Path, rel: str, dest_dir: Path, *, expected: tuple[int | None, int | None, int | None] | None = None
                   ) -> tuple[ImageInfo, str, int]:
        """1 つのハンドルで、ヘッダ検査（形式・大きさ・画素の上限）→ 一覧との照合 → コピー → 前後の同一性の検査までを済ませ、
        内容のハッシュの名前（`<sha256>.png|jpg`）で `dest_dir` に置く。(画像情報, sha256, バイト数)。
        `expected` は一覧に出したときの (バイト数, 幅, 高さ)。違えば止める。読んでいる間の変更は前後の fstat と、
        コピーしたものから読み直したヘッダ・ハッシュで見て、止める。"""
        fd, st, path = self.open(root, rel)
        tmp = None
        try:
            before = FileIdentity.of(st)
            if st.st_size > self.policy.image_bytes:
                raise PreparationStopped("画像が大きすぎます。", "limit_exceeded", {"limit": self.policy.image_bytes})
            try:
                info = sniff_image(fd, st.st_size)
            except (ValueError, OSError):
                raise PreparationStopped("画像として読めません（PNG・JPEG のヘッダが正しくありません）。", "bad_image") from None
            if info.width * info.height > self.policy.image_pixels:
                raise PreparationStopped("画像の画素数が多すぎます。", "limit_exceeded", {"limit": self.policy.image_pixels})
            if expected is not None and any(e is not None and e != g for e, g in zip(expected, (st.st_size, info.width, info.height))):
                raise PreparationStopped("素材が、一覧に出したときと違う画像になっています。もう一度頼んでください。",
                                         "changed_since_listing")
            dest_dir.mkdir(parents=True, exist_ok=True)
            tmp = dest_dir / f".part-{os.getpid()}-{id(fd)}"
            digest, total = hashlib.sha256(), 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.policy.image_bytes:
                        raise PreparationStopped("画像が大きすぎます。", "limit_exceeded", {"limit": self.policy.image_bytes})
                    digest.update(chunk)
                    out.write(chunk)
            if self.on_read is not None:
                self.on_read(path)
            self._unchanged(fd, path, before)
            sha = digest.hexdigest()
            # コピーしたものを読み直して、検査したヘッダと同じ画像か・ハッシュが同じかを確かめる
            copied = os.open(tmp, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            try:
                again = sniff_image(copied, total)
            except (ValueError, OSError):
                again = None
            finally:
                os.close(copied)
            if again != info or total != before.size or hashlib.sha256(tmp.read_bytes()).hexdigest() != sha:
                raise PreparationStopped("素材のコピーが検査した画像と一致しません。もう一度頼んでください。", "changed_during_read")
            final = dest_dir / f"{sha}{'.png' if info.media_type == 'image/png' else '.jpg'}"
            oscompat.replace(tmp, final)
            tmp = None
            return info, sha, total
        finally:
            os.close(fd)
            if tmp is not None:
                tmp.unlink(missing_ok=True)
