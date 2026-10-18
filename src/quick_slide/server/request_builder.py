"""会話へ渡す依頼文の目録と選択条件。生成の受付や通常設定の再読込は行わない。"""
from __future__ import annotations

import json
import os
import tempfile

from .. import oscompat
from ..store.intakes import MAX_PAGE_COUNT
from .app import Response
from .errors import ApiError

MODES = ("presentation", "pitch", "motion")
LOOKS = ("auto", "images", "diagrams")
WORKFLOWS = ("outline", "direct")
FIELDS = {"mode", "brand_id", "template_id", "quantity", "look", "workflow"}


def _catalog(app):
    brands = [{"id": b["id"], "name": b["name"]} for b in app.store.list_brands()]
    templates = [t for b in brands for t in app.store.list_design_templates(b["id"])]
    return brands, templates


def _default(app):
    return {"mode": "presentation", "brand_id": app.store.default_brand_id(), "template_id": None,
            "quantity": None, "look": "auto", "workflow": "outline"}


def _validate(value, brands, templates):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError
    if value["mode"] not in MODES or value["look"] not in LOOKS or value["workflow"] not in WORKFLOWS:
        raise ValueError
    if not isinstance(value["brand_id"], str) or not any(b["id"] == value["brand_id"] for b in brands):
        raise ValueError
    if value["mode"] == "motion" and value["look"] == "images":
        raise ValueError
    q = value["quantity"]
    if q is not None and (type(q) is not int or not 1 <= q <= MAX_PAGE_COUNT):
        raise ValueError
    tid = value["template_id"]
    if tid is not None and (not isinstance(tid, str) or value["mode"] == "motion" or not any(
        t["template_id"] == tid and t["brand_id"] == value["brand_id"] and value["mode"] in t["modes"] for t in templates
    )):
        raise ValueError
    return dict(value)


def catalog(app, req):
    with app.store_lock:
        brands, templates = _catalog(app)
        preferences, warning = _default(app), ""
        try:
            document = json.loads(app.root.request_builder_path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or document.get("schema_version") != 1:
                raise ValueError
            saved = document.get("preferences")
            if not isinstance(saved, dict) or set(saved) != FIELDS:
                raise ValueError
            candidate = dict(saved)
            if not any(b["id"] == candidate["brand_id"] for b in brands):
                candidate["brand_id"] = preferences["brand_id"]
                candidate["template_id"] = None
            if candidate["template_id"] is not None and not any(
                t["template_id"] == candidate["template_id"] and t["brand_id"] == candidate["brand_id"]
                and candidate["mode"] in t["modes"] and candidate["mode"] != "motion" for t in templates
            ):
                candidate["template_id"] = None
            if candidate["mode"] == "motion" and candidate["look"] == "images":
                candidate["look"] = "auto"
            preferences = _validate(candidate, brands, templates)
            if preferences != saved:
                warning = "前回の条件に使えない候補があるため、選択を戻しました。"
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            warning = "前回の条件を読み込めませんでした。条件を選び直してください。"
        return Response.json(200, {"brands": brands, "templates": templates, "preferences": preferences,
                                   "warning": warning, "max_quantity": MAX_PAGE_COUNT}, headers=(("Cache-Control", "no-store"),))


def save(app, req):
    with app.store_lock:
        brands, templates = _catalog(app)
        try:
            preferences = _validate(req.json_object(), brands, templates)
        except (ValueError, TypeError):
            raise ApiError(400, "request_conditions_changed", "条件が正しくないか、選んだ候補が変わりました。画面を開き直して選び直してください。") from None
        path = app.root.request_builder_path
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".request-builder-", suffix=".tmp", dir=path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"schema_version": 1, "preferences": preferences}, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            oscompat.replace(temporary, path)
        except OSError:
            raise ApiError(500, "request_conditions_unsaved", "選択条件を記憶できませんでした。") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        return Response.json(200, {"saved": True}, headers=(("Cache-Control", "no-store"),))
