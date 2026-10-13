"""取り込み（会話からの投入）。デッキ・取り込み・起動・描いた回・仕事を 1 つのトランザクションで作る。

payload の形は v1 と同じ（`schema_version`・`origin{kind,runtime,conversation_key}`・`manuscript`・`title_hint`・
`deck_attributes`・`attachments`・`append_to_deck_id`・`force_new_deck`）。v2 で足したのは
`deck_attributes` の `mode`（presentation / pitch / motion）・`style`（エンジンのスタイル）・`image_look`・`images`、P3 で
`outline_fixed`（原稿は会話で合意した構成。台本係はページ割りを守る）、P4 で `style_id`（設定のスタイル）。

ブランド・スタイルの決め方（P4）: `brand_id` → 設定の既定 → 利用者のブランド（最新）→ 既定。`style_id` → 会話が言った
`style` にあたる付属スタイル → モードの既定（プレゼン standard・ピッチ visual・モーション standard）。会話が `style`・`image_look`・
`style_note` を言ったら、それは描く回の `overrides` に入り、設定のスタイルより勝つ（`style` は `style_id` と一緒に
言ったときだけ。`style` だけなら、それにあたる付属スタイルを選ぶので overrides に入れない）。

秘匿の契約（v1 と同じ）:
- `origin.conversation_key` の生値はこのモジュールのローカル変数にだけ存在し、HMAC 化したら捨てる
- 原稿は `intakes.manuscript` にだけ置く。payload_json・events・ログに書かない

**原稿か依頼か**（docs/adr/0002-create-from-screen.md）: `manuscript` と `request` はちょうどどちらか 1 つ。
`request` は「依頼から作る」道で、出所は `origin.kind=request`（逆に kind が request なら `request` が要る）。依頼の文は
`intakes.request` に置き（`manuscript` は空）、ワーカーが台本係の前に下調べ係を呼んで、起こした原稿を `manuscript` に書く
（`set_researched`）。台本係には原稿（材料）と別に、依頼の文を利用者の指示として渡す。画面も会話（CLI）も同じ形で送り、
違いは `origin.runtime` だけ。依頼は原稿と同じく payload_json・events・ログに書かない。
モード・ブランド・スタイルは依頼の文から読まない（`deck_attributes` の `mode`・`brand_id`・`style` で、原稿の道と同じ）。

まだ受けないもの（`NotReadyError`。サーバは 400 `not_ready_in_v2` にする）: 既存のデッキへの追加
（`append_to_deck_id`）・会話抜粋・添付・書式。黙って捨てずに断る。

**送り直しは同じデッキを返す**（`RESEND_WINDOW_SECONDS` 以内に、同じ会話の識別子で、同じ原稿・題・属性が
もう一度届いたとき）。会話の側が応答を取りこぼして送り直しても、デッキが 2 冊にならない。返すのは前の仕事と、
そのデッキを開くための新しい起動（URL のトークンは 1 回しか使えないため）。`force_new_deck` なら必ず作る。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ..engines import ENGINES, IMAGE_LOOKS
from ..enums import ORIGIN_KINDS, ORIGIN_RUNTIMES, require
from ..errors import QuickSlideError, StateError, ValidationError
from ..ids import PRESET_BRAND_ID
from ..security.hmac_key import conversation_hash
from ..security.secret_scan import find_secret_like
from .base import StoreBase, assert_no_forbidden_keys, dump_json, load_json, reject_unknown_keys

log = logging.getLogger("quick_slide.store.intakes")

PAYLOAD_KEYS = {"schema_version", "origin", "title_hint", "manuscript", "request", "transcript_excerpt", "deck_attributes",
                "attachments", "append_to_deck_id", "force_new_deck"}
ORIGIN_KEYS = {"kind", "runtime", "conversation_key"}
DECK_ATTRIBUTE_KEYS = {"purpose", "audience", "style_note", "page_count", "brand_id", "mode", "style", "image_look",
                       "images", "outline_fixed", "style_id", "format_id", "format_values", "format_options"}
# 形は v1 のまま残し、v2 ではまだ受けない項目（値が入っていたら断る）。書式はブランドの中に持つ（P4）
NOT_READY_ATTRIBUTES = ("format_id", "format_values", "format_options")
MAX_PAGE_COUNT = 40
RESEND_WINDOW_SECONDS = 10 * 60
MAX_TEXT_ATTRIBUTE_CHARS = 2000
# 依頼の文の上限（画面の入力欄。画面は `/api/vocabulary` の `create_limits.request` を読んで送る前に数える）
MAX_REQUEST_CHARS = 2000
REQUEST_REQUIRED_REASON = "どんなスライドを作るかを書いてください。"
BOTH_OR_NEITHER_REASON = ("原稿（manuscript）か依頼（request）の、どちらか 1 つだけを入れてください。"
                          "材料を整えてあるなら原稿を、サーバに調べてほしいなら依頼を送ります")
REQUEST_TOO_LONG_REASON = f"依頼の文が長すぎます。{MAX_REQUEST_CHARS}文字以内にしてください。"
# 依頼から作るデッキの、下調べが終わるまでの仮の題（依頼の 1 行目をこの字数で切る）
PROVISIONAL_TITLE_CHARS = 40


class NotReadyError(QuickSlideError):
    """形は正しいが、v2 ではまだ受けない項目。サーバは 400 `not_ready_in_v2` にする。"""

    exit_code = 2


class IntakesMixin(StoreBase):
    # --- 投入 ---
    def create_intake(self, payload: dict[str, Any], *, launch_requested_via: str | None = "start") -> dict[str, Any]:
        """payload を検査し、デッキ（モードを固定）・取り込み・起動・描いた回・仕事を作る。

        戻り値: {intake_id, deck_id, deck_title, mode, style, launch_id, launch_token, job_id, run_id, resolution,
        appended, resolution_reason}。`launch_token` は平文で、戻り値以外に残らない。
        """
        p = self._validate_intake(payload)
        attrs = p["deck_attributes"]
        runtime = p["origin"]["runtime"]
        raw_key = p["origin"].get("conversation_key")  # 生値。この関数のローカルだけ
        conv_hash = conversation_hash(self._hmac_key, runtime, raw_key) if raw_key is not None else None
        del raw_key
        stored = {k: v for k, v in p.items() if k not in ("manuscript", "request", "transcript_excerpt")}
        stored["origin"] = {k: v for k, v in p["origin"].items() if k != "conversation_key"}
        assert_no_forbidden_keys(stored, "intakes.payload_json")
        asked = [p["manuscript"], p.get("title_hint"), attrs] + ([p["request"]] if p.get("request") else [])
        request_sha = hashlib.sha256(json.dumps(asked, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

        with self.transaction():
            now = self.now()
            if conv_hash is not None and not p["force_new_deck"]:
                again = self._resent_intake(conv_hash, request_sha, now)
                if again is not None:
                    return self._answer_resent(again, launch_requested_via, p["origin"]["kind"])
            title = ((p.get("title_hint") or "").strip() or provisional_title(p.get("request"))
                     or f"無題のスライド（{now[:10]} {now[11:16]} UTC）")
            deck = self.create_deck(
                title, mode=attrs["mode"],
                brand_revision_id=self._intake_brand_revision(attrs.get("brand_id")),
                style_revision_id=self._intake_style_revision(attrs),
                purpose=attrs.get("purpose") or "", audience=attrs.get("audience") or "",
            )
            deck_id = deck["id"]
            launch_id = launch_token = None
            if launch_requested_via is not None and p["origin"]["kind"] != "headless":
                launch = self.create_launch(requested_via=launch_requested_via, deck_id=deck_id)
                launch_id, launch_token = launch["id"], launch["token"]
            intake_id = self.new_id()
            self._exec(
                "INSERT INTO intakes(id,deck_id,origin_kind,origin_runtime,conversation_hash,request_sha256,title_hint,"
                " manuscript,request,payload_json,launch_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (intake_id, deck_id, p["origin"]["kind"], runtime, conv_hash, request_sha, p.get("title_hint"),
                 p["manuscript"], p.get("request"), dump_json(stored), launch_id, now))
            run = self.create_run(deck_id, args=self.run_args_from_attributes(attrs, origin_runtime=runtime))
            job = self.create_job(deck_id, run_id=run["id"], intake_id=intake_id)
            self.append_event("intake_created", {
                "intake_id": intake_id, "origin_kind": p["origin"]["kind"], "origin_runtime": runtime,
                "launch_id": launch_id, "job_id": job["id"], "run_id": run["id"], "mode": attrs["mode"],
                "style": attrs["style"], "manuscript_chars": len(p["manuscript"]),
                "request_chars": len(p["request"]) if p.get("request") else None,
                "has_conversation_key": conv_hash is not None,
            }, deck_id=deck_id)
        log.info("intake %s -> deck %s (%s/%s)", intake_id, deck_id, attrs["mode"], attrs["style"])
        return {"intake_id": intake_id, "deck_id": deck_id, "deck_title": title, "mode": attrs["mode"],
                "style": attrs["style"], "launch_id": launch_id, "launch_token": launch_token,
                "job_id": job["id"], "run_id": run["id"], "resolution": "new", "appended": False,
                "resolution_reason": None}

    def create_import(self, *, filename: str, info: dict[str, Any], runtime: str, deck_id: str | None = None,
                      launch_requested_via: str | None = "start") -> dict[str, Any]:
        """既存の PPTX の取り込み（docs/adr/0011-import-pptx.md）: プレゼンのデッキ・取り込み（出所は file・原稿は空）・
        起動・描いた回（`imported: true`）・仕事を作る。PPTX は呼ぶ側が先に `importing.keep_source` でデッキの置き場へ
        移しておく（`deck_id` を先に決めて渡す。仕事が拾われたときに、もう置いてあるように）。

        戻り値は `create_intake` と同じ形（`launch_token` は平文で、戻り値以外に残らない）。"""
        from .. import importing

        require(runtime, ORIGIN_RUNTIMES, "origin.runtime")
        attrs = {"mode": "presentation", "style": "standard", "images": False}
        title = importing.title_of(filename)
        payload = {"schema_version": 1, "origin": {"kind": "file", "runtime": runtime},
                   "import": {k: info[k] for k in ("name", "bytes", "sha256")}}
        request_sha = hashlib.sha256(json.dumps(["import", info["sha256"]], sort_keys=True).encode()).hexdigest()
        with self.transaction():
            now = self.now()
            deck = self.create_deck(title, mode="presentation", brand_revision_id=self._intake_brand_revision(None),
                                    style_revision_id=self._intake_style_revision(attrs), deck_id=deck_id)
            deck_id = deck["id"]
            launch_id = launch_token = None
            if launch_requested_via is not None:
                launch = self.create_launch(requested_via=launch_requested_via, deck_id=deck_id)
                launch_id, launch_token = launch["id"], launch["token"]
            intake_id = self.new_id()
            self._exec(
                "INSERT INTO intakes(id,deck_id,origin_kind,origin_runtime,conversation_hash,request_sha256,title_hint,"
                " manuscript,request,payload_json,launch_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (intake_id, deck_id, "file", runtime, None, request_sha, title, "", None, dump_json(payload),
                 launch_id, now))
            args = {**self.run_args_from_attributes(attrs, origin_runtime=runtime), "imported": True}
            run = self.create_run(deck_id, args=args)
            job = self.create_job(deck_id, run_id=run["id"], intake_id=intake_id)
            self.append_event("intake_created", {
                "intake_id": intake_id, "origin_kind": "file", "origin_runtime": runtime, "launch_id": launch_id,
                "job_id": job["id"], "run_id": run["id"], "mode": "presentation", "style": "standard",
                "import_bytes": info["bytes"], "manuscript_chars": 0, "request_chars": None, "has_conversation_key": False,
            }, deck_id=deck_id)
        log.info("import %s -> deck %s (%d bytes)", intake_id, deck_id, info["bytes"])
        return {"intake_id": intake_id, "deck_id": deck_id, "deck_title": title, "mode": "presentation",
                "style": "standard", "launch_id": launch_id, "launch_token": launch_token, "job_id": job["id"],
                "run_id": run["id"], "resolution": "new", "appended": False, "resolution_reason": None}

    def intake_mode(self, payload: dict[str, Any]) -> str:
        """payload を `create_intake` と同じに検査し（何も書かない）、作るデッキのモードを返す。
        呼ぶ側が「その形式をこの機械で描けるか」を見る前に、入力の誤りを先に言うため（`server/intake_routes.py`）。"""
        return self._validate_intake(payload)["deck_attributes"]["mode"]

    def _resent_intake(self, conv_hash: str, request_sha: str, now: str) -> dict[str, Any] | None:
        from ..clock import add_seconds

        since = add_seconds(now, -RESEND_WINDOW_SECONDS)
        return self._one(
            "SELECT i.id,i.deck_id,d.title FROM intakes i JOIN decks d ON d.id=i.deck_id"
            " WHERE i.conversation_hash=? AND i.request_sha256=? AND i.created_at>=? AND d.deleted_at IS NULL"
            " ORDER BY i.created_at DESC,i.rowid DESC LIMIT 1", (conv_hash, request_sha, since))

    def _answer_resent(self, again: dict[str, Any], launch_requested_via: str | None, kind: str) -> dict[str, Any]:
        self._require_tx()
        job = self._one("SELECT id,run_id FROM jobs WHERE intake_id=? ORDER BY created_at,rowid LIMIT 1", (again["id"],))
        run = self.run(job["run_id"]) if job else None
        launch_id = launch_token = None
        if launch_requested_via is not None and kind != "headless":
            launch = self.create_launch(requested_via=launch_requested_via, deck_id=again["deck_id"])
            launch_id, launch_token = launch["id"], launch["token"]
        log.info("intake resent -> deck %s", again["deck_id"])
        return {"intake_id": again["id"], "deck_id": again["deck_id"], "deck_title": again["title"],
                "mode": run["mode"] if run else None, "style": run["args"].get("style") if run else None,
                "launch_id": launch_id, "launch_token": launch_token, "job_id": job["id"] if job else None,
                "run_id": run["id"] if run else None, "resolution": "resent", "appended": False,
                "resolution_reason": None}

    @staticmethod
    def run_args_from_attributes(attrs: dict[str, Any], *, origin_runtime: str) -> dict[str, Any]:
        """描いた回に残す「何をどう頼まれたか」（台本係とエンジンに渡す引数の元）。原稿は入れない。"""
        overrides = {k: attrs[k] for k in ("image_look", "style_note") if attrs.get(k)}
        if attrs.get("style_id") and attrs.get("style_said"):
            overrides["style"] = attrs["style"]
        return {
            "mode": attrs["mode"], "style": attrs["style"], "image_look": attrs.get("image_look"),
            "images": attrs["images"], "page_count": attrs.get("page_count"),
            "purpose": attrs.get("purpose") or "", "audience": attrs.get("audience") or "",
            "style_note": attrs.get("style_note") or "", "origin_runtime": origin_runtime,
            "outline_fixed": bool(attrs.get("outline_fixed")), "overrides": overrides,
        }

    # --- 検査 ---
    def _validate_intake(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = self.settings.intake
        if not isinstance(payload, dict):
            raise ValidationError("intake の payload はオブジェクト")
        reject_unknown_keys(payload, PAYLOAD_KEYS, "intake")
        if payload.get("schema_version") != 1:
            raise ValidationError("intake.schema_version は 1")
        origin = payload.get("origin")
        if not isinstance(origin, dict):
            raise ValidationError("intake.origin は必須")
        reject_unknown_keys(origin, ORIGIN_KEYS, "intake.origin")
        require(origin.get("kind"), ORIGIN_KINDS, "intake.origin.kind")
        require(origin.get("runtime"), ORIGIN_RUNTIMES, "intake.origin.runtime")
        key = origin.get("conversation_key")
        if key is not None:
            if not isinstance(key, str):
                raise ValidationError("intake.origin.conversation_key は文字列か null")
            if len(key) < cfg.min_conversation_key_chars:
                # 値は決してメッセージに入れない
                raise ValidationError(f"intake.origin.conversation_key は {cfg.min_conversation_key_chars} 文字以上")
        title = payload.get("title_hint")
        if title is not None and not isinstance(title, str):
            raise ValidationError("intake.title_hint は文字列か null")
        if isinstance(title, str) and len(title) > 200:
            raise ValidationError("タイトルは 200 文字以内にしてください")
        has_manuscript, has_request = payload.get("manuscript") is not None, payload.get("request") is not None
        if has_manuscript == has_request:
            raise ValidationError(BOTH_OR_NEITHER_REASON)
        if has_request != (origin["kind"] == "request"):
            raise ValidationError("依頼（request）を送るときは origin.kind を request に、原稿を送るときは request 以外にしてください")
        if has_request:
            return self._validate_request_intake(payload)
        manuscript = payload.get("manuscript")
        if not isinstance(manuscript, str) or not manuscript.strip():
            raise ValidationError("原稿（manuscript）が空です。会話から整えた原稿を入れてください")
        if len(manuscript) > cfg.max_manuscript_chars:
            raise ValidationError(f"原稿が上限 {cfg.max_manuscript_chars} 字を超えています（黙って切りません）")
        if find_secret_like(manuscript, cfg.extra_secret_patterns):
            raise ValidationError("原稿に秘密情報らしい文字列が含まれています。除いて再投入してください")
        if payload.get("transcript_excerpt") is not None:
            raise NotReadyError("会話抜粋（transcript_excerpt）は v2 ではまだ受け付けません。原稿だけで送ってください。")
        atts = payload.get("attachments")
        if atts is not None and not isinstance(atts, list):
            raise ValidationError("intake.attachments は配列")
        if atts:
            raise NotReadyError("画像の添付（attachments）は v2 ではまだ受け付けません。画像なしで送ってください。")
        if payload.get("append_to_deck_id") is not None:
            raise NotReadyError("既存のスライドへの追加（append_to_deck_id）は v2 ではまだできません。新しいスライドとして送ってください。")
        force_new = payload.get("force_new_deck", False)
        if not isinstance(force_new, bool):
            raise ValidationError("intake.force_new_deck は真偽値")
        attrs = self._validate_attributes(payload.get("deck_attributes"), manuscript=manuscript)
        out = dict(payload)
        out["deck_attributes"] = attrs
        out["attachments"] = []
        out["force_new_deck"] = force_new
        return out

    def _validate_request_intake(self, payload: dict[str, Any]) -> dict[str, Any]:
        """依頼から作る取り込み（`request`）。原稿・会話抜粋・添付・追加・構成の固定は一緒に受けない。"""
        cfg = self.settings.intake
        request = payload.get("request")
        if not isinstance(request, str) or not request.strip():
            raise ValidationError(REQUEST_REQUIRED_REASON)
        if len(request) > MAX_REQUEST_CHARS:
            raise ValidationError(REQUEST_TOO_LONG_REASON)
        if find_secret_like(request, cfg.extra_secret_patterns):
            raise ValidationError("依頼の文に秘密情報らしい文字列が含まれています。除いてから送ってください")
        for k in ("transcript_excerpt", "append_to_deck_id"):
            if payload.get(k) is not None:
                raise ValidationError(f"依頼から作るときは {k} を送れません")
        if payload.get("attachments"):
            raise ValidationError("依頼から作るときは画像を添付できません")
        force_new = payload.get("force_new_deck", False)
        if not isinstance(force_new, bool):
            raise ValidationError("intake.force_new_deck は真偽値")
        raw = payload.get("deck_attributes")
        if isinstance(raw, dict) and raw.get("outline_fixed"):
            raise ValidationError("依頼から作るときは構成を固定できません（構成は下調べ係が起こします）")
        attrs = self._validate_attributes(raw)
        out = dict(payload)
        out.update(request=request.strip(), manuscript="", deck_attributes=attrs, attachments=[], force_new_deck=force_new)
        return out

    def _validate_attributes(self, raw: Any, *, manuscript: str = "") -> dict[str, Any]:
        cfg = self.settings.intake
        attrs = {} if raw is None else raw
        if not isinstance(attrs, dict):
            raise ValidationError("intake.deck_attributes はオブジェクト")
        reject_unknown_keys(attrs, DECK_ATTRIBUTE_KEYS, "intake.deck_attributes")
        for k in NOT_READY_ATTRIBUTES:
            if attrs.get(k) not in (None, "", "auto", {}, []):
                raise NotReadyError(f"deck_attributes.{k} は v2 ではまだ使えません。外して送ってください。")
        out = {k: v for k, v in attrs.items() if k not in NOT_READY_ATTRIBUTES}
        for k in ("purpose", "audience", "style_note"):
            v = out.get(k)
            if v is None:
                continue
            if not isinstance(v, str):
                raise ValidationError(f"intake.deck_attributes.{k} は文字列")
            limit = cfg.max_style_chars if k == "style_note" else MAX_TEXT_ATTRIBUTE_CHARS
            if len(v) > limit:
                raise ValidationError(f"deck_attributes.{k} が上限 {limit} 字を超えています（黙って切りません）")
            if find_secret_like(v, cfg.extra_secret_patterns):
                raise ValidationError(f"deck_attributes.{k} に秘密情報らしい文字列が含まれています。除いて再投入してください")
        mode = out.get("mode") or "presentation"
        if mode not in ENGINES:
            raise ValidationError("deck_attributes.mode は presentation（編集できて動く PPTX）・pitch（1枚ずつ絵で描く）・"
                                  "motion（クリックで拍が進む Web プレゼン。HTML 1枚）のどれか")
        engine = ENGINES[mode]
        said = bool(out.get("style"))
        style = out.get("style") or engine.default_style
        if style not in engine.styles:
            raise ValidationError(f"deck_attributes.style はこのモードでは {' / '.join(engine.styles)} のいずれか")
        look = out.get("image_look")
        if look is not None and look not in IMAGE_LOOKS:
            raise ValidationError(f"deck_attributes.image_look は {' / '.join(IMAGE_LOOKS)} のいずれか")
        images = out.get("images", True)
        if not isinstance(images, bool):
            raise ValidationError("deck_attributes.images は真偽値（画像の枠を置くか）")
        if not engine.has_images:
            images = False   # モーションは画像の枠を持たない（図は場面の HTML で描く）
        outline_fixed = out.get("outline_fixed", False)
        if not isinstance(outline_fixed, bool):
            raise ValidationError("deck_attributes.outline_fixed は真偽値（原稿は合意した構成か）")
        if outline_fixed:
            from ..scriptdoc import outline_headings

            heads = outline_headings(manuscript)
            if not heads:
                raise ValidationError("構成を固定するときは、原稿にページごとの見出し（`## 1. 見出し`）を書いてください")
            if [n for n, _ in heads] != list(range(1, len(heads) + 1)):
                raise ValidationError("構成の見出しの番号は 1 から順に書いてください（`## 1.`・`## 2.` …）")
            if len(heads) > MAX_PAGE_COUNT:
                raise ValidationError(f"構成のページは {MAX_PAGE_COUNT} 枚までです")
        pc = out.get("page_count")
        if pc is not None and (isinstance(pc, bool) or not isinstance(pc, int) or not (1 <= pc <= MAX_PAGE_COUNT)):
            raise ValidationError(f"deck_attributes.page_count は 1〜{MAX_PAGE_COUNT} の整数")
        brand_id = out.get("brand_id")
        if brand_id is not None and not isinstance(brand_id, str):
            raise ValidationError("deck_attributes.brand_id は文字列")
        style_id = out.get("style_id")
        if style_id in ("", "auto"):
            style_id = None
        if style_id is not None and not isinstance(style_id, str):
            raise ValidationError("deck_attributes.style_id は文字列")
        out.update(mode=mode, style=style, image_look=look, images=images, outline_fixed=outline_fixed,
                   style_id=style_id, style_said=said)
        return out

    def _intake_style_revision(self, attrs: dict[str, Any]) -> str:
        """`style_id` → 会話が言った `style` にあたる付属スタイル → モードの既定のスタイル（付属）。"""
        from .presets import preset_style_for

        if attrs.get("style_id"):
            row = self._one("SELECT current_revision_id FROM styles WHERE id=? AND deleted_at IS NULL", (attrs["style_id"],))
            if row is None:
                raise ValidationError("deck_attributes.style_id のスタイルが見つかりません")
            return row["current_revision_id"]
        return self.preset_style_revision_id(preset_style_for(attrs["mode"], attrs["style"]))

    def _intake_brand_revision(self, brand_id: str | None) -> str:
        """明示の brand_id → 既定（`default_brand_id`）。P4 で描く回に効かせる。"""
        if brand_id is not None:
            b = self._one("SELECT current_revision_id FROM brands WHERE id=? AND deleted_at IS NULL", (brand_id,))
            if b is None:
                raise ValidationError("deck_attributes.brand_id のブランドが見つかりません")
            return b["current_revision_id"]
        rev = self._scalar("SELECT current_revision_id FROM brands WHERE id=?", (self.default_brand_id(),))
        if rev is None:
            raise StateError("既定ブランドが初期化されていません")
        return rev

    def default_brand_id(self) -> str:
        """brand_id を言わずに作ったときのブランド: 設定の既定 → 利用者のブランド（最新）→ 付属（無彩色）。
        画面のブランドの選択肢も、どれが既定かをここで知る（`GET /api/settings` の `default_brand_id`）。"""
        if self.settings.default_brand_id:
            b = self._one("SELECT id FROM brands WHERE id=? AND deleted_at IS NULL", (self.settings.default_brand_id,))
            if b is not None:
                return b["id"]
        user = self._one("SELECT id FROM brands WHERE kind='user' AND deleted_at IS NULL"
                         " ORDER BY updated_at DESC,rowid DESC LIMIT 1")
        return user["id"] if user is not None else PRESET_BRAND_ID

    # --- 下調べ ---
    def set_researched(self, intake_id: str, run_id: str, *, manuscript: str, title: str | None,
                       purpose: str | None, audience: str | None, page_count: int | None, sources: int) -> dict[str, Any]:
        """下調べ係が起こした原稿を取り込みに書き、題・目的・読み手・枚数をデッキと描いた回に入れる。

        原稿を書くのは 1 度だけ（もう原稿があれば StateError）。題は、利用者がまだ仮の題（依頼の 1 行目）のままにしている
        ときだけ替え、目的・読み手は空のときだけ埋める（画面で先に直したものを上書きしない）。枚数は依頼に書いてあったときだけ
        来るので、回の引数に無いときだけ入れる。戻り値は回の引数に足したもの。
        """
        with self.transaction():
            row = self._get("intakes", intake_id)
            if row["request"] is None or row["manuscript"]:
                raise StateError("この取り込みの原稿はもうあります")
            self._exec("UPDATE intakes SET manuscript=? WHERE id=?", (manuscript, intake_id))
            deck = self.deck(row["deck_id"])
            fields: dict[str, str] = {}
            if title and deck["title"] == provisional_title(row["request"]):
                fields["title"] = title
            if purpose and not deck["purpose"]:
                fields["purpose"] = purpose
            if audience and not deck["audience"]:
                fields["audience"] = audience
            if fields:
                self.update_deck_metadata(row["deck_id"], **fields)
            args = self.run(run_id)["args"]
            update: dict[str, Any] = {}
            if page_count and not args.get("page_count"):
                update["page_count"] = page_count
            for k in ("purpose", "audience"):
                if fields.get(k):
                    update[k] = fields[k]
            if update:
                self.update_run_args(run_id, update)
            self.append_event("intake_researched", {
                "intake_id": intake_id, "run_id": run_id, "manuscript_chars": len(manuscript), "sources": sources,
                "fields": sorted(fields), "page_count": update.get("page_count"),
            }, deck_id=row["deck_id"])
        return update

    def retryable_request_intake(self, deck_id: str) -> dict[str, Any] | None:
        """「生成を再開」で作り直せる依頼の取り込み: 台本がまだ無い（下調べか台本係で止まった）デッキの、依頼から作る
        取り込み（一番新しいもの）。無ければ None。"""
        if self.head_script(deck_id) is not None:
            return None
        return self._one("SELECT id,origin_runtime,manuscript,payload_json FROM intakes"
                         " WHERE deck_id=? AND request IS NOT NULL ORDER BY created_at DESC,rowid DESC LIMIT 1", (deck_id,))

    def retry_request_intake(self, deck_id: str) -> dict[str, Any]:
        """台本がまだ無い依頼のデッキを作り直す: 同じ取り込みで新しい回と「1 冊を作る」仕事を作る（`create_edit` と同じ形
        {job, run, ahead} を返す）。原稿がまだ無ければ下調べから、下調べが済んでいれば台本係から（`worker/research.py`）。
        下調べが前の回に入れた枚数・目的・読み手は、新しい回にも持ち越す。"""
        with self.transaction():
            row = self.retryable_request_intake(deck_id)
            if row is None:
                raise StateError("作り直せる依頼がありません")
            ahead = int(self._scalar("SELECT COUNT(*) FROM jobs WHERE deck_id=? AND state IN ('queued','running')",
                                     (deck_id,)))
            attrs = (load_json(row["payload_json"]) or {}).get("deck_attributes") or {}
            args = self.run_args_from_attributes(attrs, origin_runtime=row["origin_runtime"])
            prev = self._one("SELECT r.args_json FROM jobs j JOIN runs r ON r.id=j.run_id WHERE j.intake_id=?"
                             " ORDER BY r.number DESC LIMIT 1", (row["id"],))
            prev_args = load_json(prev["args_json"]) if prev else {}
            for k in ("page_count", "purpose", "audience"):
                if prev_args.get(k) and not args.get(k):
                    args[k] = prev_args[k]
            run = self.create_run(deck_id, args=args)
            job = self.create_job(deck_id, run_id=run["id"], intake_id=row["id"])
            self.append_event("intake_retried", {"intake_id": row["id"], "job_id": job["id"], "run_id": run["id"],
                                                 "researched": bool(row["manuscript"]), "ahead": ahead}, deck_id=deck_id)
            return {"job": job, "run": run, "ahead": ahead, "researched": bool(row["manuscript"])}

    # --- 参照 ---
    def intake(self, intake_id: str) -> dict[str, Any]:
        row = self._get("intakes", intake_id)
        row["payload"] = load_json(row.pop("payload_json"))
        return row


def provisional_title(request: str | None) -> str | None:
    """依頼から作るデッキの仮の題: 依頼の最初の空でない行を `PROVISIONAL_TITLE_CHARS` 字で切ったもの。"""
    if not request:
        return None
    line = next((ln.strip() for ln in request.splitlines() if ln.strip()), "")
    if not line:
        return None
    return line if len(line) <= PROVISIONAL_TITLE_CHARS else line[:PROVISIONAL_TITLE_CHARS - 1] + "…"
