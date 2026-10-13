"""`POST /api/imports` — 既存の PPTX の取り込み（docs/adr/0011-import-pptx.md）。本文は PPTX のバイト列そのまま。

- 名前は `X-Quick-Slide-Filename`（URL エンコード）か、問い合わせの `name`。出所の実行環境は問い合わせの `runtime`（既定 browser）
- 本文はサーバが読む前に、宛先・オリジン・セッションを確かめ、ディスクへ流して受ける（`QuickSlideServer._read_upload`。
  上限は `importing.MAX_PPTX_BYTES`。ふつうの口の 16 MiB とは別）
- デッキはプレゼン（html-slide）。プレゼンをこの機械でいま描けないなら 409 `engine_unavailable`
- 答えは `POST /api/intakes` と同じ形（201 `{deck_id, url（起動のトークンつき）, deck_url, …}`）
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote

from .. import importing
from ..enums import ORIGIN_RUNTIMES
from .errors import ApiError

if TYPE_CHECKING:
    from .app import QuickSlideServer, Request, Response

FILENAME_HEADER = "x-quick-slide-filename"


def route_import(server: "QuickSlideServer", req: "Request") -> "Response":
    from .app import Response

    q = {k: v[-1] for k, v in parse_qs(req.query or "", keep_blank_values=True).items()}
    filename = importing.clean_filename(unquote(req.header(FILENAME_HEADER) or q.get("name") or ""))
    runtime = q.get("runtime") or "browser"
    if runtime not in ORIGIN_RUNTIMES:
        raise ApiError(400, "validation_error", f"runtime は {' / '.join(ORIGIN_RUNTIMES)} のどれかです。")
    if req.upload is None:
        raise ApiError(400, "validation_error", "取り込む PPTX を本文で送ってください。")
    try:
        info = importing.check_upload(req.upload, filename)
    except importing.ImportRefused as exc:
        raise ApiError(400, "import_refused", str(exc)) from None
    refusal = server.kind_refusals().get("presentation")
    if refusal:
        raise ApiError(409, "engine_unavailable", refusal, details={"mode": "presentation"})
    with server.store_lock:
        assert server.store is not None
        deck_id = server.store.new_id()
    source = importing.keep_source(server.root, deck_id, req.upload)
    try:
        with server.store_lock:
            result = server.store.create_import(filename=filename, info=info, runtime=runtime, deck_id=deck_id)
    except BaseException:
        import shutil
        shutil.rmtree(source.parent, ignore_errors=True)   # デッキを作れなければ、置いた PPTX も残さない
        raise
    server.wake_worker()
    token = result.pop("launch_token", None)
    url = server.launch_url(result["deck_id"], result["launch_id"], token)
    del token
    return Response.json(201, {
        "intake_id": result["intake_id"], "deck_id": result["deck_id"], "deck_title": result["deck_title"],
        "mode": result["mode"], "style": result["style"], "launch_id": result["launch_id"],
        "job_id": result["job_id"], "run_id": result["run_id"], "url": url, "deck_url": f"/slides/{result['deck_id']}",
        "resolution": result["resolution"], "appended": False, "resolution_reason": None,
        "import": {"name": info["name"], "bytes": info["bytes"]},
    })
