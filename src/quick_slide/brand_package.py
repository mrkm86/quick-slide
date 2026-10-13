"""ブランドのエクスポート・インポートの ZIP（チームや組織で同じブランドを配るため）。標準ライブラリだけ。

中身（形式の版 1）:

    brand.json        {"format": "quick-slide-brand", "version": 1, "id", "name", "exported_at", "config",
                       "assets": [{"role", "label", "file", "sha256", "media_type"}]}
    assets/<役割>.png  ロゴの画像（PNG か JPEG。役割は enums.LOGO_ROLES）

- 入れるのは設定（色・フッター・ウォーターマーク・ロゴの位置・字体の名前）とロゴの画像だけ。字体のファイルは入れない
  （配布の権利が絡む）。ロゴでない役割の画像（差し絵など）も入れない
- 読むときは、他人から受け取った ZIP として扱う。ディスクへ展開せず、決まった名前のファイルだけを読む。
  個数・展開後の大きさ・圧縮率・名前の形・ハッシュ値を確かめ、どれかが外れたら PackageError（中身を推測で直さない）
- 設定の中身の検査（色の形・ロゴの指定）と画像の中身の検査は、呼ぶ側が保存と同じ道で行う（settings_routes）
- 書き出すファイル名は「qs-ブランド-<名前>.zip」（filename）。人が見分けるための名前で、読むときは見ない
  （受け渡しで改名されても読める。種類を見分けるのは brand.json の format）
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from typing import Any

from .enums import LOGO_ROLES

FORMAT = "quick-slide-brand"
VERSION = 1
MANIFEST = "brand.json"
MAX_ENTRIES = 1 + len(LOGO_ROLES)          # brand.json と、役割ごとに 1 枚まで
MAX_MANIFEST_BYTES = 256 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024          # 画像 1 枚（設定画面の画像の上限と同じ）
MAX_TOTAL_BYTES = 24 * 1024 * 1024         # 展開後の合計
MAX_RATIO = 200                            # 圧縮率の上限（展開すると膨れる ZIP を断る）
MEDIA_EXT = {"image/png": ".png", "image/jpeg": ".jpg"}
_ASSET_NAME = re.compile(r"^assets/(%s)\.(png|jpg)$" % "|".join(LOGO_ROLES))
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CONFIG_KEYS = {"schema_version", "colors", "fonts", "logo", "footer", "format"}
FILE_PREFIX = "qs-ブランド-"
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


class PackageError(ValueError):
    """ZIP がブランドのエクスポートの形ではない・壊れている・大きすぎる。message は画面に出してよい文。"""


def filename(name: str) -> str:
    """書き出す ZIP のファイル名。OS がファイル名に使えない文字は「_」にする。"""
    return FILE_PREFIX + (_UNSAFE_FILENAME.sub("_", name).strip() or "名称未設定") + ".zip"


def build(*, brand_id: str, name: str, config: dict[str, Any], assets: list[dict[str, Any]], exported_at: str) -> bytes:
    """ブランド 1 つを ZIP にする。assets は [{role, label, media_type, data}]（ロゴの役割だけを渡す）。"""
    files = []
    entries = []
    for a in assets:
        if a["role"] not in LOGO_ROLES or a["media_type"] not in MEDIA_EXT:
            continue
        path = "assets/" + a["role"] + MEDIA_EXT[a["media_type"]]
        files.append((path, a["data"]))
        entries.append({"role": a["role"], "label": a.get("label", ""), "file": path,
                        "sha256": hashlib.sha256(a["data"]).hexdigest(), "media_type": a["media_type"]})
    manifest = {"format": FORMAT, "version": VERSION, "id": brand_id, "name": name, "exported_at": exported_at,
                "config": {k: v for k, v in config.items() if k in _CONFIG_KEYS}, "assets": entries}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        for path, data in files:
            z.writestr(path, data, compress_type=zipfile.ZIP_STORED)   # 画像は圧縮済み
    return buf.getvalue()


def read(data: bytes) -> dict[str, Any]:
    """ZIP を読み、{id, name, exported_at, config, assets: [{role, label, media_type, data}]} を返す。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError):
        raise PackageError("ZIP ファイルとして読めません。エクスポートした ZIP をそのまま選んでください。") from None
    with z:
        infos = z.infolist()
        if not infos or len(infos) > MAX_ENTRIES:
            raise PackageError("ブランドのエクスポートの ZIP ではありません（中のファイルの数が合いません）。")
        names = set()
        total = 0
        for info in infos:
            name = info.filename
            if info.is_dir() or name in names or (name != MANIFEST and not _ASSET_NAME.match(name)):
                raise PackageError("ブランドのエクスポートの ZIP ではありません（知らないファイルが入っています）。")
            if info.flag_bits & 0x1:
                raise PackageError("暗号化された ZIP は読めません。")
            limit = MAX_MANIFEST_BYTES if name == MANIFEST else MAX_FILE_BYTES
            if info.file_size > limit:
                raise PackageError("ZIP の中のファイルが大きすぎます。")
            if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                raise PackageError("ZIP の中身が不自然に膨らみます。エクスポートした ZIP をそのまま選んでください。")
            total += info.file_size
            names.add(name)
        if total > MAX_TOTAL_BYTES:
            raise PackageError("ZIP の中身が大きすぎます。")
        if MANIFEST not in names:
            raise PackageError("ブランドのエクスポートの ZIP ではありません（brand.json がありません）。")
        try:
            manifest = json.loads(_read(z, MANIFEST, MAX_MANIFEST_BYTES).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PackageError("brand.json を読めません。") from None
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise PackageError("ブランドのエクスポートの ZIP ではありません。")
        if manifest.get("version") != VERSION:
            raise PackageError("この版の Quick Slide では読めない形式です。エクスポートした側と同じ版でエクスポートし直してください。")
        bid, name, config = manifest.get("id"), manifest.get("name"), manifest.get("config")
        if not isinstance(bid, str) or not _UUID.match(bid):
            raise PackageError("brand.json のブランドの ID が正しくありません。")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise PackageError("brand.json のブランドの名前が正しくありません。")
        if not isinstance(config, dict) or set(config) - _CONFIG_KEYS:
            raise PackageError("brand.json の設定の形が正しくありません。")
        listed = manifest.get("assets")
        if not isinstance(listed, list):
            raise PackageError("brand.json の画像の一覧が正しくありません。")
        assets, roles, used = [], set(), set()
        for a in listed:
            if not isinstance(a, dict) or a.get("role") not in LOGO_ROLES or a["role"] in roles:
                raise PackageError("brand.json の画像の一覧が正しくありません。")
            path, media = a.get("file"), a.get("media_type")
            if media not in MEDIA_EXT or path != "assets/" + a["role"] + MEDIA_EXT[media] or path not in names:
                raise PackageError("brand.json の画像の一覧と、ZIP の中のファイルが合いません。")
            blob = _read(z, path, MAX_FILE_BYTES)
            if hashlib.sha256(blob).hexdigest() != a.get("sha256"):
                raise PackageError("ZIP の中の画像が、エクスポートしたときと違います。エクスポートした ZIP をそのまま選んでください。")
            label = a.get("label") if isinstance(a.get("label"), str) else ""
            assets.append({"role": a["role"], "label": label[:200], "media_type": media, "data": blob})
            roles.add(a["role"])
            used.add(path)
        if names - used - {MANIFEST}:
            raise PackageError("ブランドのエクスポートの ZIP ではありません（一覧に無いファイルが入っています）。")
        exported = manifest.get("exported_at") if isinstance(manifest.get("exported_at"), str) else None
        return {"id": bid, "name": name.strip(), "exported_at": exported, "config": config, "assets": assets}


def _read(z: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """宣言された大きさを信じず、limit を超えて読まない。"""
    try:
        with z.open(name) as f:
            blob = f.read(limit + 1)
    except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError, EOFError):
        raise PackageError("ZIP の中のファイルが壊れていて読めません。") from None
    if len(blob) > limit:
        raise PackageError("ZIP の中のファイルが大きすぎます。")
    return blob
