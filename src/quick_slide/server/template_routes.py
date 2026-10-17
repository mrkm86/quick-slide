"""デザインテンプレートの API（設定 › ブランド › テンプレート）。仕様は docs/design-template-implementation.md §4。

- 一覧・取得・版の一覧・Markdown の書き出し・検証用のコンパイル（JSON と ZIP）は GET
- 作成・保存・複製・削除・取り込み（確かめる → 保存する）・計画（dry-run）・リソースのパス選びは POST
  （状態を変える要求と同じ Host・Origin・セッショントークンの検査を通る。`app._check_mutation`）
- 保存の判断は `template_service` が持つ。ここは入力の形を確かめて呼び、HTTP の形にするだけ
- 本文・パスはログに出さない。エラーは他の設定の API と同じ JSON の形（`{"error": {code, message}}`）
"""
from __future__ import annotations

import io
import ipaddress
import json
import subprocess
import sys
import urllib.parse
import zipfile
from typing import TYPE_CHECKING, Any

from .. import design_templates as dt
from . import template_service as service
from .app import Response
from .errors import ApiError

if TYPE_CHECKING:
    from .app import QuickSlideServer, Request

CHOICE_CHANGED = "インポート先の状況が変わりました。もう一度ファイルを選んでください。"
SAVE_FIELDS = ("document", "markdown", "mode", "expected_revision_id", "source_revision_id", "origin_runtime", "conversation_key", "overwrite")


def _invalid(exc: ValueError) -> ApiError:
    # dt の検証の文は日本語で、入力の中身（本文・パス）は含まない
    return ApiError(400, "invalid_template", str(exc))


def _fields(req: "Request", allowed: tuple[str, ...]) -> dict[str, Any]:
    body = req.json_object()
    if set(body) - set(allowed):
        raise ApiError(400, "unknown_field", "対応していない項目が含まれています。画面を開き直して入力し直してください。")
    return body


def _query(req: "Request") -> dict[str, str]:
    return {k: v[0] for k, v in urllib.parse.parse_qs(req.query or "").items()}


def _revision_number(req: "Request") -> int | None:
    raw = _query(req).get("revision")
    if raw is None:
        return None
    if not raw.isdecimal() or int(raw) < 1:
        raise ApiError(400, "invalid_revision", "版の番号を確認してください。")
    return int(raw)


def _json(obj: Any, status: int = 200) -> Response:
    return Response.json(status, obj, headers=(("Cache-Control", "no-store"),))


def _locked(fn):
    """store の鍵を取って呼ぶ。dt の検証（ValueError）は 400 にする。ストアの例外はサーバの外殻が 404・400・409 にする。"""
    def wrapped(app, req):
        try:
            with app.store_lock:
                return fn(app, req)
        except ValueError as exc:
            raise _invalid(exc) from None
    wrapped.__name__ = fn.__name__
    return wrapped


# --- 読み取り ---
@_locked
def list_templates(app, req):
    brand_id = _query(req).get("brand_id")
    if not brand_id:
        raise ApiError(400, "brand_required", "ブランドを選んでください。")
    app.store.brand(brand_id)   # 無ければ 404。削除済みのブランドは、空の一覧（子は隠す）
    return _json({"brand_id": brand_id, "templates": app.store.list_design_templates(brand_id)})


@_locked
def get_template(app, req):
    return _json(app.store.get_design_template(req.params["template_id"], revision=_revision_number(req)))


@_locked
def list_revisions(app, req):
    tid = req.params["template_id"]
    return _json({"template_id": tid, "revisions": app.store.design_template_revisions(tid)})


@_locked
def export_markdown(app, req):
    view = app.store.get_design_template(req.params["template_id"], revision=_revision_number(req))
    try:
        data = dt.to_markdown(view["document"]).encode("utf-8")
    except ValueError as exc:
        raise ApiError(409, "not_exportable", str(exc)) from None
    name = urllib.parse.quote(f"qs-テンプレート-{view['document']['name']}.md", safe="")
    return Response(200, data, content_type="text/markdown; charset=utf-8",
                    headers=(("Content-Disposition", f"attachment; filename=\"qs-template.md\"; filename*=UTF-8''{name}"),
                             ("Cache-Control", "no-store")))


def _compile_inputs(app, req) -> tuple[dict[str, Any], dict[str, Any], str]:
    """保存した版と、そのとき控えたブランドの版を読む（いまのブランドではない）。鍵の中で読むのはここまで。"""
    with app.store_lock:
        view = app.store.get_design_template(req.params["template_id"], revision=_revision_number(req))
        doc = view["document"]
        brev = app.store.brand_revision(view["brand_revision_id"])
    brand = {"id": doc["brand_id"], "current_revision_id": brev["id"], "revision": brev["revision"], "config": brev["config"],
             "assets": [{k: a[k] for k in ("asset_id", "role", "label", "sha256", "width", "height")} for a in brev["assets"]]}
    return view, brand, _query(req).get("mode") or doc["modes"][0]


def _compile(app, req) -> tuple[dict[str, Any], dict[str, Any]]:
    view, brand, mode = _compile_inputs(app, req)
    try:
        # ファイルの中身を読む（上限つき）ので、store の鍵の外で作る
        return view, dt.compile_document(view["document"], brand, mode=mode)
    except ValueError as exc:
        raise _invalid(exc) from None


def compiled_json(app, req):
    return _json(_compile(app, req)[1])


def compiled_zip(app, req):
    view, compiled = _compile(app, req)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in compiled["files"].items():
            if content:
                archive.writestr(name, content)
        archive.writestr("compilation.json", json.dumps({k: v for k, v in compiled.items() if k != "files"},
                                                        ensure_ascii=False, indent=2))
    return Response(200, stream.getvalue(), content_type="application/zip",
                    headers=(("Content-Disposition", f"attachment; filename=\"{view['template_id']}-instructions.zip\""),
                             ("Cache-Control", "no-store")))


# --- 書き込み ---
@_locked
def create_template(app, req):
    b = _fields(req, ("brand_id", "document", "markdown", "origin_runtime", "conversation_key"))
    out = service.create(app.store, b.get("brand_id"), b.get("document"), origin_runtime=b.get("origin_runtime"),
                         conversation_key=b.get("conversation_key"), markdown=b.get("markdown"))
    return _json(out, 201)


@_locked
def save_template(app, req):
    b = _fields(req, SAVE_FIELDS)
    return _json(service.save(
        app.store, req.params["template_id"], b.get("document"), mode=b.get("mode"),
        expected_revision_id=b.get("expected_revision_id"), source_revision_id=b.get("source_revision_id"),
        origin_runtime=b.get("origin_runtime"), conversation_key=b.get("conversation_key"), overwrite=b.get("overwrite", False), markdown=b.get("markdown")))


@_locked
def copy_template(app, req):
    b = _fields(req, ("expected_revision_id", "name"))
    return _json(service.duplicate(app.store, req.params["template_id"], expected_revision_id=b.get("expected_revision_id"),
                                   name=b.get("name")), 201)


@_locked
def delete_template(app, req):
    b = _fields(req, ("expected_revision_id",))
    return _json(service.delete(app.store, req.params["template_id"], expected_revision_id=b.get("expected_revision_id")))


@_locked
def plan_save(app, req):
    return _json(service.plan(app.store, _fields(req, ("template_id", "brand_id") + SAVE_FIELDS)))


# --- 取り込み（確かめる → 保存する。ブランドの取り込みと同じ 2 段）---
def _file_id(text: str) -> str | None:
    """交換ファイルの前付けにある ID（無ければ None）。`from_markdown` が前付けを検査したあとにだけ呼ぶ。"""
    import yaml
    lines = text.replace("\r\n", "\n").split("\n")
    end = 1 + [line.strip() for line in lines[1:]].index("---")
    value = (yaml.safe_load("\n".join(lines[1:end])) or {}).get("id")
    return value if isinstance(value, str) and value else None


def _read_import(app, b: dict[str, Any]):
    brand_id = b.get("brand_id")
    service._live_brand(app.store, brand_id)
    text = b.get("text")
    # ID はあとで決める。ファイルに書かれた ID は「同じテンプレートか」を見分けるだけに使う
    doc = dt.from_markdown(text, brand_id=brand_id, template_id="import-check")
    file_id = _file_id(text)
    existing, choices = service.import_mode(app.store, brand_id, doc, file_id)
    return brand_id, doc, file_id, existing, choices


def check_import(app, req):
    b = _fields(req, ("text", "brand_id"))
    try:
        with app.store_lock:
            _, doc, file_id, existing, choices = _read_import(app, b)
    except ValueError as exc:
        raise _invalid(exc) from None
    # 有無は stat だけで見る（中身は読まない）。鍵の外で
    present = dt.resource_presence(doc["resources"])
    return _json({"name": doc["name"], "id": file_id, "modes": doc["modes"], "existing": existing, "choices": choices,
                  "resources": [{"name": r["name"], "kind": r["kind"], "path": r["path"], "exists": ok}
                                for r, ok in zip(doc["resources"], present)]})


@_locked
def import_template(app, req):
    b = _fields(req, ("text", "brand_id", "mode", "expected_revision_id"))
    brand_id, doc, file_id, existing, choices = _read_import(app, b)
    mode = b.get("mode")
    if mode not in ("create", "new_version", "add") or mode not in choices:
        raise ApiError(409, "import_choice_changed", CHOICE_CHANGED)
    store = app.store
    if mode == "create":
        # ファイルの ID は「同じテンプレートか」を見分けるためだけに使う。作るときは必ずサーバが採番する
        return _json(service.create(store, brand_id, doc), 201)
    if mode == "new_version":
        if b.get("expected_revision_id") != existing["current_revision_id"]:
            raise ApiError(409, "setting_changed", "上書きするテンプレートが別の画面で更新されています。もう一度ファイルを選んでください。")
        return _json(service.save(store, existing["template_id"], doc, mode="new_version",
                                  expected_revision_id=existing["current_revision_id"]))
    doc["name"] = doc["name"] + service.IMPORT_SUFFIX
    return _json(service.create(store, brand_id, doc), 201)


# --- リソースのパス選び ---
def _address(value: str):
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])   # IPv6 のスコープ（fe80::1%en0）を除く
    except ValueError:
        return None
    return getattr(ip, "ipv4_mapped", None) or ip


def same_machine(client: str, listen: str) -> bool:
    """要求がこのパソコン自身から来たか。接続元のアドレスが、ループバック（IPv4・IPv6 のどの書き方でも・
    IPv4 に写した形も）か、待ち受けているアドレス自身のときだけ。転送ヘッダ（X-Forwarded-For など）は見ない。
    リバースプロキシの後ろでは、接続元がプロキシ自身になるので、このダイアログは使わないこと。"""
    c = _address(client or "")
    if c is None:
        return False
    if c.is_loopback:
        return True
    host = _address(listen or "")
    return host is not None and c == host


def pick_path(kind: str) -> str | None:
    """サーバを動かしているパソコンで、ファイル・フォルダを選ぶダイアログを開く。取り消しは None。
    いまは macOS だけ（Windows のネイティブのダイアログはあとで）。ほかは手入力を案内する。"""
    if sys.platform != "darwin":
        raise ApiError(400, "pick_unsupported", "このパソコンではダイアログを開けません。パスを入力してください。")
    target = "folder" if kind == "directory" else "file"
    label = "フォルダ" if kind == "directory" else "ファイル"
    script = f'POSIX path of (choose {target} with prompt "リソースにする{label}を選んでください")'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=600)
    if result.returncode != 0:
        return None
    path = result.stdout.strip()
    return path.rstrip("/") if kind == "directory" and path != "/" else path


def pick_resource_path(app, req):
    # ダイアログはサーバのパソコンの画面に出る。ほかの端末（スマホなど）からは開かない
    if not same_machine(req.client, app.host):
        raise ApiError(403, "pick_not_local", "ダイアログはサーバを動かしているパソコンでだけ開けます。パスを入力してください。")
    kind = _query(req).get("kind") or (req.json_object().get("kind") if req.body else "")
    if kind not in ("file", "directory"):
        raise ApiError(400, "invalid_kind", "ファイルかフォルダかを選んでください。")
    try:
        picked = pick_path(kind)
    except subprocess.TimeoutExpired:
        raise ApiError(400, "pick_timeout", "ダイアログの操作が時間切れになりました。もう一度選んでください。") from None
    except OSError:
        raise ApiError(400, "pick_unsupported", "ダイアログを開けませんでした。パスを入力してください。") from None
    return _json({"path": picked, "cancelled": picked is None})
