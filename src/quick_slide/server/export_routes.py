"""書き出しの配布（P5）。**画面に出ている回（見せる回）のファイルをそのまま配る**（エンジンの出力。作り直さない）。

- `POST /api/decks/<id>/exports` {format: pptx|pdf|images}  → `{"export": {id, format, run_id, filename, bytes, sha256,
  download_url, created_at}}`（v1 の画面と同じ形。画面は `export.download_url` を開く）
- `GET /api/exports/<id>/file`  → そのファイル（`Content-Disposition: attachment`。名前はスライドの題＋拡張子）

決まり:
- 配るのは見せる回（`store.shown_run`。描き終えた一番新しい回）の控え `decks/<デッキ>/runs/<回>/deck.pptx`・`deck.pdf`。
  非表示のページはエンジンに渡す台本から抜いてあるので、入っていない
- `images` はページの画像（`slides/slide-NN.png`）の ZIP（`runs/<回>/exports/pages.zip`。時刻を固定して作るので、
  同じ回なら同じバイト）
- 書き出しは記録する（どの回・どの形式・中身の sha256・名前）。配るときに sha256 を照らし、違えば配らない（同じ URL は同じバイト）
- **見せる回にその形式が無ければ**（エンジンがその出力だけ作れなかった・まだ 1 回も描き終えていない）、いまの台本を
  いまの設定で描き直す仕事（`render`）を作り、終わるまで待ってから配る（`EXPORT_WAIT_SECONDS` まで。超えたら 202 で
  「作っています」と答え、画面はもう一度押してもらう）
"""

from __future__ import annotations

import hashlib
import re
import time
import unicodedata
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..enums import EXPORT_FORMATS
from ..errors import NotFoundError
from .deck_routes import NO_PAGES_REASON, _deck_not_found, _json, _live_deck_row
from .errors import ApiError

if TYPE_CHECKING:
    from .app import QuickSlideServer, Request, Response

EXPORT_WAIT_SECONDS = 900.0
POLL_SECONDS = 0.5
FILES = {"pptx": "deck.pptx", "pdf": "deck.pdf", "images": "exports/pages.zip"}
EXTENSIONS = {"pptx": ".pptx", "pdf": ".pdf", "images": ".zip"}
MEDIA_TYPES = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
    "images": "application/zip",
}
FORMAT_REASON = "書き出しの形式は PDF・PPTX・ページの画像（ZIP）のどれかです。"
BUILDING_MESSAGE = "書き出すファイルを作っています。少し待ってから、もう一度押してください。"
FAILED_MESSAGE = "書き出すファイルを作れませんでした。"
_UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')
ZIP_TIME = (2020, 1, 1, 0, 0, 0)


def safe_filename(title: str, fmt: str) -> str:
    """スライドの題 → ファイル名（OS で使えない文字・制御文字を「_」に。長さは拡張子込みで 120 文字まで）。"""
    name = unicodedata.normalize("NFC", title or "").strip()
    name = _UNSAFE.sub("_", name).strip(" .")
    name = name[:120 - len(EXTENSIONS[fmt])].rstrip(" .") or "スライド"
    return name + EXTENSIONS[fmt]


def content_disposition(filename: str) -> str:
    """`attachment`。日本語の名前は RFC 5987 の `filename*`、古い読み手には ASCII の代わりの名前。"""
    ascii_name = filename.encode("ascii", "ignore").decode("ascii").strip() or "slides" + Path(filename).suffix
    ascii_name = ascii_name.replace('"', "_").replace("\\", "_")
    if ascii_name.startswith("."):
        ascii_name = "slides" + ascii_name
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pages_zip(run_dir: Path) -> Path | None:
    """ページの画像の ZIP（無ければ作る。時刻を固定して、同じ回なら同じバイト）。ページの画像が無ければ None。"""
    target = run_dir / FILES["images"]
    if target.is_file():
        return target
    pngs = sorted((run_dir / "slides").glob("slide-*.png"))
    if not pngs:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as z:
        for p in pngs:
            info = zipfile.ZipInfo(p.name, date_time=ZIP_TIME)
            info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes())
    tmp.replace(target)
    return target


def _run_file(server: "QuickSlideServer", run: dict[str, Any] | None, fmt: str) -> Path | None:
    if run is None:
        return None
    run_dir = server.root.run_dir(run["deck_id"], run["id"])
    if fmt == "images":
        return _pages_zip(run_dir)
    path = run_dir / FILES[fmt]
    return path if path.is_file() and path.stat().st_size > 0 else None


def _wait_for_job(server: "QuickSlideServer", deck_id: str, job_id: str, seconds: float) -> dict[str, Any] | None:
    """仕事が終わるまで待つ（終わった仕事の行。時間切れは None）。"""
    deadline = time.monotonic() + seconds
    while True:
        with server.store_lock:
            job = server.store.job(job_id)
        if job["state"] in ("done", "failed"):
            return job
        if time.monotonic() >= deadline:
            return None
        time.sleep(POLL_SECONDS)


def export_public(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "format": row["format"], "run_id": row["run_id"], "filename": row["filename"],
            "bytes": row["bytes"], "sha256": row["sha256"], "created_at": row["created_at"], "state": "ready",
            "download_url": f"/api/exports/{row['id']}/file"}


def route_create_export(server: "QuickSlideServer", req: "Request") -> "Response":
    deck_id = req.params["deck_id"]
    body = req.json_object()
    fmt = body.get("format")
    if fmt not in EXPORT_FORMATS:
        raise ApiError(400, "export_format", FORMAT_REASON)
    with server.store_lock:
        store = server.store
        row = _live_deck_row(store, deck_id)
        shown = store.shown_run(deck_id)
        head = store.head_script(deck_id)
    path = _run_file(server, shown, fmt)
    produced = False
    if path is None:
        # 見せる回にその形式が無い: いまの台本をいまの設定で描き直してから配る
        if head is None:
            raise ApiError(409, "no_pages", NO_PAGES_REASON)
        with server.store_lock:
            result = server.store.create_edit(deck_id, {"op": "render", "source": "screen", "reason": "export",
                                                        "format": fmt})
        server.wake_worker()
        job = _wait_for_job(server, deck_id, result["job"]["id"], min(EXPORT_WAIT_SECONDS,
                                                                       server.settings.worker.render_timeout_seconds + 60))
        if job is None:
            return _json(202, {"export": {"id": None, "format": fmt, "state": "building", "download_url": None,
                                          "job_id": result["job"]["id"]}, "message": BUILDING_MESSAGE})
        with server.store_lock:
            run = server.store.run(result["run"]["id"])
            row = _live_deck_row(server.store, deck_id)
        path = _run_file(server, run if run["state"] == "done" else None, fmt)
        if path is None:
            reason = run.get("message") or ""
            raise ApiError(409, "export_unavailable", f"{FAILED_MESSAGE}{reason}".strip())
        shown = run
        produced = True
    assert shown is not None
    sha = _sha256(path)
    rel = path.relative_to(server.root.root).as_posix()
    with server.store_lock:
        rec = server.store.record_export(deck_id, run_id=shown["id"], fmt=fmt, path=rel, sha256=sha,
                                         size=path.stat().st_size, filename=safe_filename(row["title"], fmt))
    return _json(201, {"export": export_public(rec), "produced": produced, "message": None})


def route_export_file(server: "QuickSlideServer", req: "Request") -> "Response":
    from .app import Response

    with server.store_lock:
        store = server.store
        try:
            rec = store.export(req.params["export_id"])
        except NotFoundError:
            raise ApiError(404, "export_not_found", "その書き出しは見つかりません。もう一度書き出してください。") from None
        try:
            _live_deck_row(store, rec["deck_id"])
        except ApiError:
            raise _deck_not_found() from None
    path = (server.root.root / rec["path"]).resolve()
    try:
        path.relative_to(server.root.decks_dir.resolve())
        data = path.read_bytes()
    except (ValueError, OSError):
        raise ApiError(404, "export_not_found", "書き出したファイルが見つかりません。もう一度書き出してください。") from None
    if hashlib.sha256(data).hexdigest() != rec["sha256"]:
        raise ApiError(409, "export_changed", "書き出したときとファイルの中身が違うため、配りません。もう一度書き出してください。")
    return Response(200, data, content_type=MEDIA_TYPES[rec["format"]],
                    headers=(("Content-Disposition", content_disposition(rec["filename"])),))
