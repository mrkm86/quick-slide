"""世代のフォルダの控えファイル（一時名へ書き、検証してから置き換える。決まった書き方で、ハッシュが安定する）。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .. import oscompat


def dumps(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def write_json(path: Path, obj: Any) -> str:
    """一時名へ書いてから置き換える。書いた中身の sha256 を返す。"""
    data = dumps(obj)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.part-{os.getpid()}")
    try:
        tmp.write_bytes(data)
        oscompat.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return sha256_bytes(data)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify(folder: Path, files: dict[str, str]) -> bool:
    """記録したハッシュと、いまのファイルの中身が全部合うか。"""
    return all(sha256_file(folder / name) == sha for name, sha in files.items())
