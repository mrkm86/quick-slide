"""設定の API（ブランド・スタイル・この機械）。ファイルの配信は `static.AssetFiles` を通す。

ブランドとスタイルは v1 と同じ形で保存・見本表示し、描く回のたびにエンジンの引数へ写す（P4。`render/look.py`）。
書式（ページ番号・ウォーターマーク）はブランドの中の `format` に持ち、ブランドの編集画面で直す（一覧の「書式を登録」はやめた）。
使うパターンの目録はエンジンの型の一覧（`contenttypes.engine_types`。先頭に「おまかせ」）。
"""
from __future__ import annotations

import copy
import hashlib
import urllib.parse
import io
import logging
import os
import tempfile
import warnings
from functools import wraps

from PIL import Image, UnidentifiedImageError

from .. import brand_package
from .. import contenttypes
from .. import logo as logo_rules
from .. import palette
from .. import slide_palette
from ..errors import NotFoundError, StateError, ValidationError
from ..engines import ENGINES
from ..ids import PRESET_STYLE_STANDARD_ID, is_uuid
from ..settings import LOOPBACK_HOSTS, Settings, bind_scope, save_server_section
from ..enums import LOGO_ROLES
from ..store.brands import MAX_FOOTER_CHARS, MAX_WATERMARK_CHARS, VARIANT_TO_ROLE, merge_brand_assets, validate_brand_config
from ..store.presets import PRESET_BRAND_CONFIG
from ..store.styles import (
    AUTO_PATTERN,
    CANVAS_SIZES,
    EDITABLE_KEYS,
    IMAGE_LOOK_CHOICES,
    MAX_STYLE_NOTE_CHARS,
    STYLE_DIAL_OPTIONS,
    TONES,
    validate_style_config,
)
from .app import Response
from .errors import ApiError

log = logging.getLogger("quick_slide.server.settings")

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
IMAGE_TYPES = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg"), "WEBP": (".webp", "image/webp")}
STYLE_FIELDS = EDITABLE_KEYS
LOGO_MEDIA = ("image/png", "image/jpeg")   # エンジン（--logo・--logo-white）が受ける形
IMAGE_LOOK_LABELS = {"photo": "写真風", "illustration": "イラスト", "handdrawn": "手描き"}
ENGINE_STYLE_LABELS = {"standard": "standard（説明・報告）", "visual": "visual（ビジュアル）",
                       "infographic": "infographic（1枚のポスター）"}
CAPABILITIES = {
    # 「元の文書」は**入力に渡す文書**で、我々の成果物（画面では「スライド」）ではない。
    # ここを「スライド」にすると意味が逆になるので、成果物と混ざらない言い方にする。
    "infer": {"available": False, "label": "元の文書から色・字体を読み取る", "reason": "元の文書からの読み取りはまだ使えません。色と字体を下の欄で指定してください。"},
    "apply": {"available": False, "label": "既存のスライドに適用", "reason": "既存のスライドへの適用は、画像を作り直す機能の公開後に使えます。保存だけでは既存のスライドは変わりません。"},
}


def boundary(fn):
    @wraps(fn)
    def wrapped(app, req):
        try:
            with app.store_lock:
                return fn(app, req)
        except NotFoundError:
            raise ApiError(404, "setting_not_found", "設定または画像が見つかりません。一覧を読み直して選び直してください。") from None
        except logo_rules.LogoError:
            raise ApiError(400, "logo_position", "ロゴの位置の指定が不正です。「既定に戻す」を押して確認してください。") from None
        except ValidationError as exc:
            # Store diagnostics contain internal keys; never echo them or caller values.
            msg = str(exc)
            # 二種類を書き分ける:
            #  (a) 画面(web/app)が実際に出している入力欄に対応する不正 — 利用者が直せる。項目名を含めた文にする。
            #  (b) それ以外（schema_version・未知のキー・fonts・margins・canvas・style.id/revision/name/origin など、
            #      画面からは正常な操作では起こらないはずの形） — 呼び出し側（画面）の不具合であり、利用者の入力の
            #      問題ではない。入力を疑わせる文言にせず、詳細はサーバのログにだけ残す。
            if any(k in msg for k in ("colors.surface", "colors.base", "colors.ink", "colors.muted")):
                hint = "「他の要素の色」で変えた色は # と6桁の色番号で選び直してください。"
            elif any(k in msg for k in ("colors.primary_light", "colors.primary_dark", "colors.border")):
                hint = "ブランドの色の指定が正しくありません。基本色と強調色を選び直してください。"
            elif "colors.primary" in msg:
                hint = "基本色は # と6桁の色番号で入力してください。"
            elif "colors.accent" in msg:
                hint = "強調色は # と6桁の色番号で入力してください。"
            elif "colors.info" in msg:
                hint = "情報色は # と6桁の色番号で入力してください。"
            elif "allowed_layouts" in msg:
                hint = ("「おまかせ」とほかのパターンは一緒に選べません。どちらかにしてください。" if "おまかせ" in msg
                        else "使うパターンを1つ以上選んでください。")
            elif "emphasis_prompt" in msg:
                hint = "誰に向けて、どう書くかを入力してください。"
            elif "環境依存" in msg or "持てません" in msg:
                hint = "スタイルにはファイルの場所やブランド固有の参照を書かず、文章だけで説明してください。"
            elif "ロゴ variant" in msg or "logo.variant" in msg:
                hint = "使用するロゴの画像を登録してください。使わない場合は「なし」を選んでください。"
            elif "footer.text" in msg:
                hint = f"フッタの文字は {MAX_FOOTER_CHARS} 字以内の 1 行で入力してください。"
            elif "format.watermark" in msg:
                hint = f"透かしの文字は {MAX_WATERMARK_CHARS} 字以内の 1 行で入力してください。"
            elif "format.page_numbers" in msg:
                hint = "ページ番号は「出す」か「出さない」を選んでください。"
            elif "engine_style" in msg:
                hint = "エンジンのスタイルは、画面に表示された選択肢から選んでください。"
            elif "image_look" in msg:
                hint = "画風は、画面に表示された選択肢から選んでください。"
            elif "style_note" in msg:
                hint = f"作風の一言は {MAX_STYLE_NOTE_CHARS} 字以内の 1 行で入力してください。"
            elif "tone" in msg:
                hint = "文章の調子は硬め・標準・柔らかめから選んでください。"
            elif any(key in msg for key in ("typeface", "density", "visual_ratio")):
                hint = "絵のことは、画面に表示された選択肢から選んでください。"
            else:
                # 利用者が直せるものではない（画面の不具合の疑い）。生の診断は応答に出さずログにだけ残す。
                log.warning("設定の検証で、画面の通常操作では起こらないはずの形の要求を検出しました: %s", msg)
                hint = "設定を保存できませんでした。画面を再読み込みしてやり直してください。"
            raise ApiError(400, "invalid_setting", hint) from None
        except StateError:
            raise ApiError(409, "setting_unavailable", "この設定は変更できません。一覧を読み直して選び直してください。") from None
        except (TypeError, KeyError, ValueError, AttributeError, RecursionError):
            raise ApiError(400, "invalid_setting", "入力の形式が正しくありません。一覧から開き直し、項目を入力し直してください。") from None
    return wrapped


def payload(req, keys):
    b = req.json_object()
    if set(b) - set(keys):
        raise ApiError(400, "unknown_field", "対応していない項目が含まれています。画面を開き直して入力し直してください。")
    return b


def name_of(b):
    n = b.get("name")
    if not isinstance(n, str) or not n.strip() or len(n) > 200:
        raise ApiError(400, "name_required", "名前を1〜200文字で入力してください。")
    return n.strip()


def editable(row, b):
    if row.get("kind") == "preset":
        raise ApiError(409, "preset_read_only", "製品に付属する設定は編集・削除できません。複写して自分用の設定を作ってください。")
    if row["deleted_at"]:
        raise ApiError(409, "setting_deleted", "削除された設定です。一覧から別の設定を選んでください。")
    if b.get("expected_revision_id") != row["current_revision_id"]:
        raise ApiError(409, "setting_changed", "別の画面で設定が更新されています。入力内容を控えてから一覧を読み直してください。")


def decode_image(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ApiError(413, "image_size_limit", "画像は空でない10 MiB以下のファイルを選んでください。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                kind, size = im.format, im.size
                if kind not in IMAGE_TYPES:
                    raise ApiError(400, "image_type", "PNG・JPEG・WebPの画像を選んでください。SVGはPNGに書き出してから選んでください。")
                if size[0] * size[1] > MAX_IMAGE_PIXELS:
                    raise ApiError(413, "image_pixel_limit", "画像の画素数が多すぎます。2500万画素以下に縮小して選び直してください。")
                if getattr(im, "n_frames", 1) != 1:
                    raise ApiError(400, "image_animation", "動く画像は使えません。1枚のPNG画像として書き出してください。")
                if im.getexif().get(274, 1) != 1:
                    raise ApiError(400, "image_orientation", "回転情報のある画像です。正しい向きでPNGに書き出して選び直してください。")
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                im.load()
        return kind, size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ApiError(413, "image_pixel_limit", "画像の画素数が多すぎます。2500万画素以下に縮小して選び直してください。") from None
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError):
        raise ApiError(400, "image_invalid", "画像の内容を読み取れません。画像を開けることを確認し、PNG・JPEG・WebPで保存し直してください。") from None


def image_row(app, aid):
    if not is_uuid(aid):
        raise ApiError(400, "image_required", "ロゴ画像を選び直してください。使わない場合は「なし」を選んでください。")
    row = app.store.asset(aid)
    resolved = app.assets.resolve(row)
    data = resolved.read() if resolved else None
    if data is None or hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ApiError(400, "image_unavailable", "登録画像が見つからないか内容が変わっています。画像を選び直してください。")
    kind, size = decode_image(data)
    if size != (row["width"], row["height"]) or IMAGE_TYPES[kind][1] != row["media_type"]:
        raise ApiError(400, "image_unavailable", "画像の登録情報と内容が一致しません。画像を選び直してください。")
    return row


def public_image(row):
    return {"asset_id": row["id"], "url": f'/api/assets/{row["id"]}/file',
            "width": row["width"], "height": row["height"], "bytes": row["bytes"], "media_type": row["media_type"]}


@boundary
def upload(app, req):
    return Response.json(201, public_image(store_image(app, req.body)))


def store_image(app, data):
    """画像のバイト列を検査して置き場に登録し、登録した行を返す（設定画面の画像と、インポートしたブランドのロゴ）。"""
    kind, size = decode_image(data)
    aid = app.store.new_id()
    ext, media = IMAGE_TYPES[kind]
    # Only fresh UUID directories; no caller filenames. Reject linked parent directories.
    root = app.root.root
    for parent in (root / "runtime", root / "runtime" / "uploads", root / "assets"):
        if parent.is_symlink() or os.path.realpath(parent) != str(parent):
            raise ApiError(409, "upload_storage", "画像の保存先を確認できません。保存先の設定を管理者に確認してください。")
        parent.mkdir(exist_ok=True)
    directory = root / "assets" / aid
    directory.mkdir()
    destination = directory / ("original" + ext)
    temp = None
    try:
        fd, temp = tempfile.mkstemp(dir=root / "runtime" / "uploads")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, destination)
        with app.store.transaction():
            app.store.register_asset(relative_path=destination.relative_to(root).as_posix(), media_type=media,
                                     sha256=hashlib.sha256(data).hexdigest(), bytes_=len(data),
                                     width=size[0], height=size[1], asset_id=aid)
        return image_row(app, aid)
    except Exception:
        if temp and os.path.exists(temp):
            os.unlink(temp)
        # Keep a committed file; rollback leaves no DB reference and can be removed.
        try:
            app.store.asset(aid)
        except NotFoundError:
            destination.unlink(missing_ok=True)
            directory.rmdir()
        raise


def brand_preview(app, b):
    """見本に要る値: 配色（`palette`）・スライドが塗る色の役（`slide`）・ロゴの位置（`logo_xywh`）・フッタ文字。

    v1 の画面が読む形（`canvas` / `clean_area` / `logo_xywh` / `image_url` / `warnings` / `copyright` / `palette`）を保つ。
    **`clean_area` はいつも null**（v2 ではロゴの予約枠を持たない。置き場所はエンジンが描くときに決める）。
    ロゴの位置はエンジンの既定の寸法を写した見本で、実際の位置ではない。
    """
    config = b.get("config")
    assets = b.get("assets", [])
    if not isinstance(assets, list) or len(assets) > 20:
        raise ApiError(400, "invalid_setting", "画像は20枚以内で登録してください。")
    for a in assets:
        if not isinstance(a, dict) or set(a) - {"asset_id", "role", "label"}:
            raise ApiError(400, "invalid_setting", "画像を選び直してください。")
    validate_brand_config(config, assets)
    colors = palette.derive_from_colors(config["colors"])
    rows = {a["asset_id"]: image_row(app, a["asset_id"]) for a in assets}
    logo = config["logo"]
    corner = logo_rules.validate_placement(logo.get("placement"))[logo_rules.PLACEMENT_KEY]
    result = {"canvas": list(logo_rules.CANVAS), "clean_area": None, "logo_xywh": None, "warnings": [], "image_url": None}
    for a in assets:
        if a["role"] in LOGO_ROLES and rows[a["asset_id"]]["media_type"] not in LOGO_MEDIA:
            raise ApiError(400, "logo_type", "ロゴは PNG か JPEG で登録してください（スライドを描くエンジンが受ける形です）。")
    fmt = config.get("format") or {}
    footer_text = config["footer"]["text"]
    if logo["variant"] != "none":
        a = next(a for a in assets if a["role"] == VARIANT_TO_ROLE[logo["variant"]])
        row = rows[a["asset_id"]]
        box = logo_rules.preview_box((row["width"], row["height"]), corner)
        result.update(logo_xywh=box["logo_xywh"], warnings=box["warnings"], image_url=public_image(row)["url"])
    result["chrome"] = logo_rules.chrome_boxes(result["logo_xywh"], corner, footer=bool(footer_text),
                                              page_numbers=bool(fmt.get("page_numbers")),
                                              watermark=bool((fmt.get("watermark") or "").strip()))
    result["format"] = {"page_numbers": bool(fmt.get("page_numbers")), "watermark": (fmt.get("watermark") or "").strip()}
    result["copyright"] = {"text": footer_text, "status": "not_checked",
                           "message": "フッタは中身のページの左下に入ります（表紙・章扉・写真の全面のページには入りません）。"
                                      "ロゴが入らない角は、描くときにエンジンが別の角へ逃がします。"}
    slide = slide_palette.slide_colors(config["colors"])
    result["theme"] = slide["theme"]
    result["palette"] = colors
    # プレゼン（html-slide）が実際に塗る色の役（DADS のキーカラーの作り方）。見本はこれで塗る
    result["slide"] = {key: slide[key] for key in ("roles", "adjusted", "problems", "error", "overrides")}
    return result


def public_setting(app, row, kind):
    rev = row["current_revision"]
    result = {k: row[k] for k in ("id", "name", "kind", "current_revision_id")}
    result.update({"revision": rev["revision"], "config": rev["config"], "capabilities": {
        action: {"available": row["kind"] != "preset", "label": label,
                 "reason": "製品に付属する設定は編集・削除できません。複写して使ってください。" if row["kind"] == "preset" else ""}
        for action, label in (("edit", "編集"), ("delete", "削除"))}})
    if kind == "brands":
        result["assets"] = [{k: a[k] for k in ("asset_id", "role", "label")} for a in rev["assets"]]
    else:
        result["origin"] = rev["config"].get("origin")
        origin_id = row.get("origin_style_id")
        result["origin_name"] = app.store.style(origin_id)["name"] if origin_id else None
    return result


@boundary
def catalog(app, req):
    groups = {}
    for kind, list_fn, get_fn in (("brands", app.store.list_brands, app.store.brand), ("styles", app.store.list_styles, app.store.style)):
        groups[kind] = [public_setting(app, get_fn(r["id"]), kind) for r in list_fn()]
    # ロゴの角はエンジンの --logo-position と同じ 3 つ（右下・左下・右上）。v1 の予約枠（clean_area）と
    # 枠の上下限（bounds）は持たない（画面は無ければ描かない。bounds は空で返す）
    # 使うパターンの目録はエンジンの型の一覧（先頭は「おまかせ」＝すべて）
    patterns = [dict(AUTO_PATTERN)] + contenttypes.engine_types()
    return Response.json(200, {**groups, "counts": {k: len(v) for k, v in groups.items()},
        "brand_default": copy.deepcopy(PRESET_BRAND_CONFIG),
        # brand_id を言わずに作ったときに使うブランド（作る画面のブランドの選択肢が「既定」を示す）
        "default_brand_id": app.store.default_brand_id(),
        "logo_control": {"key": logo_rules.PLACEMENT_KEY, "corners": logo_rules.corner_choices(),
                         "default": dict(logo_rules.DEFAULT_PLACEMENT), "bounds": {}, "canvas": list(logo_rules.CANVAS)},
        "tones": [{"value": t, "label": l} for t, l in zip(TONES, ("硬め", "標準", "柔らかめ"), strict=True)],
        "style_dials": {key: [{"value": value, "label": label} for value, label in options]
                        for key, options in STYLE_DIAL_OPTIONS.items()},
        "style_default_id": PRESET_STYLE_STANDARD_ID,
        "layout_patterns": patterns,
        "engine_styles": {mode: [{"value": v, "label": ENGINE_STYLE_LABELS.get(v, v)} for v in ENGINES[mode].styles]
                          for mode in ENGINES},
        "engine_style_defaults": {mode: ENGINES[mode].default_style for mode in ENGINES},
        "image_looks": [{"value": v, "label": IMAGE_LOOK_LABELS.get(v, v)} for v in IMAGE_LOOK_CHOICES],
        "limits": {"footer": MAX_FOOTER_CHARS, "watermark": MAX_WATERMARK_CHARS, "style_note": MAX_STYLE_NOTE_CHARS},
        "canvas_sizes": sorted(CANVAS_SIZES), "capabilities": CAPABILITIES,
        "server": server_block(app)})


@boundary
def list_styles(app, req):
    return Response.json(200, {"styles": [public_setting(app, app.store.style(row["id"]), "styles")
                                          for row in app.store.list_styles()]})


@boundary
def preview_brand(app, req):
    b = payload(req, ("config", "assets"))
    return Response.json(200, brand_preview(app, b))


# ブランドの編集画面が組み直して送る config のキー。ここに無いキー（`fonts` など）は画面が知らないので送られない。
SCREEN_BRAND_KEYS = ("schema_version", "colors", "logo", "footer", "format")


def merge_brand_config(previous, submitted):
    """改訂を足すときの config。**送られたキーが勝ち、送られなかったキーは前の改訂から持ち越す。**

    画面は自分の扱うキーだけを組み直して送る（`SCREEN_BRAND_KEYS`）。送らなかったキーを「消せ」と読むと、
    複写で引き継いだ字体（`fonts`）などが、名前を変えただけで消える。決め方:

    - 上の段: 送られたキーはそのまま使う（`logo`・`footer` は中身ごと置き換え。中のキーはどれも画面が扱う）。
      送られなかったキーは前の改訂の値を写す。画面から消せないキーは、画面から消せないままにする
    - `colors` の中: 画面が扱う色（基本色・強調色と、「他の要素の色」で変えられる地の色・面の色・本文・補足）は
      **送られたものだけ**（送らない＝「自動に戻す」なので持ち越さない）。画面が扱わない役割色（`semantic` の
      success / warning / error、`gray`、画面から外した情報色・主役色（淡・濃）・区切り線）は、前の改訂で
      **人が決めた値だけ**を持ち越す（`palette.custom_roles`。算出値は基本色に合わせて算出し直す）。
      送られた側に同じ役割があれば送られた側が勝つ
    - 形が違う（オブジェクトでない）ときは何もせずに返し、検査に断らせる
    """
    if not isinstance(previous, dict) or not isinstance(submitted, dict):
        return submitted
    merged = {key: copy.deepcopy(value) for key, value in previous.items() if key not in submitted}
    merged.update(copy.deepcopy(submitted))
    colors = merged.get("colors")
    kept = palette.custom_roles(previous.get("colors"))
    if not isinstance(colors, dict) or not kept:
        return merged
    if "gray" in kept and "gray" not in colors:
        colors["gray"] = kept["gray"]
    for key in palette.LEGACY_OVERRIDES:
        if key in kept and key not in colors:
            colors[key] = kept[key]
    custom = kept.get("semantic") or {}
    sent = colors.get("semantic") if isinstance(colors.get("semantic"), dict) else {}
    if custom and set(custom) - set(sent):
        # 検査は semantic の 4 役割を全部求める。持ち越さない役割は「いまの入力から算出した値」で埋める
        # （算出値と同じなので、保存の算出で結果は変わらない）。情報色は画面の情報色に従う
        try:
            derived = palette.derive_from_colors({k: v for k, v in colors.items() if k != "semantic"} | (
                {"semantic": sent} if sent else {}))["derived"]["semantic"]
        except (ValueError, TypeError, AttributeError):
            return merged
        colors["semantic"] = {**derived, **custom, **sent}
    return merged


@boundary
def save_brand(app, req):
    b = payload(req, ("name", "config", "assets", "expected_revision_id"))
    bid = req.params.get("setting_id")
    with app.store.transaction():
        if bid:
            row = app.store.brand(bid)
            editable(row, b)
            b = {**b, "config": merge_brand_config(row["current_revision"]["config"], b.get("config")),
                 "assets": merge_brand_assets(row["current_revision"]["assets"], b.get("assets", []))}
        name = name_of(b)
        preview = brand_preview(app, b)
        config = copy.deepcopy(b["config"])
        config["colors"] = palette.snapshot_colors(config["colors"])
        assets = b.get("assets", [])
        if bid:
            app.store.rename_brand(bid, name)
            app.store.add_brand_revision(bid, config, assets=assets)
        else:
            bid = app.store.create_brand(name, config, assets=assets)["id"]
        out = public_setting(app, app.store.brand(bid), "brands")
    return Response.json(200 if req.params else 201, {"item": out, "preview": preview, "verified": True})


# ---- ブランドのエクスポート・インポート（ZIP の形は brand_package.py）----
# エクスポート: GET /api/settings/brands/<id>/export → ZIP（設定とロゴの画像）。付属のブランドはエクスポートしない
# インポート: POST /api/settings/brands/import/check（ZIP を送ると、中身と「同じ ID のブランド」があるかを返す。何も保存しない）
#           POST /api/settings/brands/import?mode=replace|add[&expected_revision_id=…]（ZIP を送り直して保存する）
#   - 同じ ID のブランドが無い: そのまま同じ ID で作る（次に同じ ZIP の新しい版を読んだとき、上書きを選べる）
#   - 同じ ID の自分のブランドがある: replace＝新しい改訂として上書き（前の改訂は残る）／add＝新しい ID で別に作る
#   - 同じ ID が付属のブランド・削除済み: add だけ（新しい ID で作る）
IMPORT_SUFFIX = "（インポート）"


@boundary
def export_brand(app, req):
    row = app.store.brand(req.params["setting_id"])
    if row["deleted_at"]:
        raise ApiError(404, "setting_not_found", "削除されたブランドはエクスポートできません。一覧を読み直してください。")
    if row["kind"] == "preset":
        raise ApiError(409, "preset_not_exportable", "付属のブランドはエクスポートできません。複製してからエクスポートしてください。")
    rev = row["current_revision"]
    assets = []
    for a in rev["assets"]:
        if a["role"] not in LOGO_ROLES:
            continue
        image = image_row(app, a["asset_id"])
        assets.append({"role": a["role"], "label": a.get("label", ""), "media_type": image["media_type"],
                       "data": app.assets.resolve(image).read()})
    blob = brand_package.build(brand_id=row["id"], name=row["name"], config=rev["config"], assets=assets,
                               exported_at=app.store.now())
    filename = urllib.parse.quote(brand_package.filename(row["name"]), safe="")
    return Response(200, blob, content_type="application/zip",
                    headers=(("Content-Disposition", f"attachment; filename=\"qs-brand.zip\"; filename*=UTF-8''{filename}"),
                             ("Cache-Control", "no-store")))


def _read_package(req):
    if not req.body:
        raise ApiError(400, "package_required", "ブランドの ZIP ファイルを選んでください。")
    try:
        return brand_package.read(req.body)
    except brand_package.PackageError as exc:
        raise ApiError(400, "package_invalid", str(exc)) from None


def _import_target(app, pkg):
    """同じ ID のブランドの状況 → (画面に見せる既存のブランド or None, 選べる道)。"""
    try:
        row = app.store.brand(pkg["id"])
    except NotFoundError:
        return None, ["create"]
    if row["deleted_at"]:
        return None, ["add"]
    existing = {"id": row["id"], "name": row["name"], "kind": row["kind"], "current_revision_id": row["current_revision_id"]}
    return existing, (["add"] if row["kind"] == "preset" else ["replace", "add"])


def _check_package_config(pkg):
    """設定の中身を保存と同じ決まりで確かめる（画像はまだ登録しないので、役割だけを並べて見る）。"""
    config = copy.deepcopy(pkg["config"])
    config["colors"] = palette.snapshot_colors(config["colors"])
    validate_brand_config(config, [{"asset_id": "", "role": a["role"]} for a in pkg["assets"]])
    return config


@boundary
def check_import(app, req):
    pkg = _read_package(req)
    config = _check_package_config(pkg)
    existing, choices = _import_target(app, pkg)
    fmt = config.get("format") or {}
    return Response.json(200, {
        "package": {"id": pkg["id"], "name": pkg["name"], "exported_at": pkg["exported_at"],
                    "colors": {"primary": config["colors"]["primary"], "accent": config["colors"]["accent"]},
                    "logos": [a["role"] for a in pkg["assets"]],
                    "footer": (config.get("footer") or {}).get("text", ""), "watermark": fmt.get("watermark", "")},
        "existing": existing, "choices": choices})


@boundary
def import_brand(app, req):
    query = urllib.parse.parse_qs(req.query or "")
    mode = (query.get("mode") or [""])[0]
    pkg = _read_package(req)
    config = _check_package_config(pkg)
    existing, choices = _import_target(app, pkg)
    if mode not in ("replace", "add") or (mode == "replace" and "replace" not in choices):
        raise ApiError(409, "import_choice_changed", "インポート先の状況が変わりました。もう一度 ZIP を選んでください。")
    if mode == "replace" and (query.get("expected_revision_id") or [""])[0] != existing["current_revision_id"]:
        raise ApiError(409, "setting_changed", "上書きするブランドが別の画面で更新されています。もう一度 ZIP を選んでください。")
    # 画像は取り引きの外で先に登録する（あとで断られても、置き場の記録とファイルは食い違わない。使われない画像が残るだけ）
    stored = [{"asset_id": store_image(app, a["data"])["id"], "role": a["role"], "label": a["label"]} for a in pkg["assets"]]
    with app.store.transaction():
        if mode == "replace":
            row = app.store.brand(existing["id"])
            assets = merge_brand_assets(row["current_revision"]["assets"], stored)
            brand_preview(app, {"config": pkg["config"], "assets": assets})
            app.store.rename_brand(row["id"], pkg["name"])
            app.store.add_brand_revision(row["id"], config, assets=assets)
            bid = row["id"]
        else:
            brand_preview(app, {"config": pkg["config"], "assets": stored})
            same_id = choices == ["create"]
            name = pkg["name"] if same_id or existing is None else (pkg["name"] + IMPORT_SUFFIX)[:200]
            bid = app.store.create_brand(name, config, assets=stored, brand_id=pkg["id"] if same_id else None)["id"]
        out = public_setting(app, app.store.brand(bid), "brands")
    return Response.json(200, {"item": out, "mode": mode, "verified": True})


def style_config(app, b, *, copying=False):
    tid = b.get("source_id") if copying else b.get("id")
    row = app.store.style(tid)
    if row["deleted_at"]:
        raise ApiError(409, "setting_deleted", "複写元が削除されています。一覧を読み直してください。")
    if copying and b.get("expected_revision_id") != row["current_revision_id"]:
        raise ApiError(409, "setting_changed", "複写元が更新されています。一覧を読み直してから複写してください。")
    config = copy.deepcopy(row["current_revision"]["config"])
    changes = b.get("config")
    if not isinstance(changes, dict) or set(changes) - set(STYLE_FIELDS):
        raise ApiError(400, "unknown_field", "スタイルは画面に表示された文章・作風・パターンの項目だけを指定してください。")
    config.update(changes)
    config["name"] = name_of(b)
    validate_style_config(config, expected_id=row["id"], expected_revision=config["revision"], expected_name=config["name"])
    return row, config


@boundary
def preview_style(app, req):
    b = payload(req, ("id", "source_id", "name", "config", "expected_revision_id"))
    _, config = style_config(app, b, copying=bool(b.get("source_id")))
    return Response.json(200, {"config": config, "message": "保存する文章の方針と作風の確認です。AI画像の見栄えは生成後に確認してください。"})


@boundary
def save_style(app, req):
    b = payload(req, ("source_id", "name", "config", "expected_revision_id"))
    tid = req.params.get("setting_id")
    with app.store.transaction():
        if tid:
            editable(app.store.style(tid), b)
        row, config = style_config(app, {**b, "id": tid}, copying=not tid)
        # 余白・画面の大きさは v2 では省略できる（書いてあるものだけ写す）
        fields = {k: config[k] for k in STYLE_FIELDS if k in config}
        if tid:
            app.store.save_style_settings(tid, config["name"], fields)
        else:
            tid = app.store.create_style_copy(row["id"], config["name"], overrides=fields)["id"]
        out = public_setting(app, app.store.style(tid), "styles")
    return Response.json(200 if req.params else 201, {"item": out, "verified": True})


@boundary
def delete_setting(app, req):
    b = payload(req, ("expected_revision_id",))
    kind = "brands" if req.path.startswith("/api/settings/brands/") else "styles"
    get_fn = app.store.brand if kind == "brands" else app.store.style
    delete_fn = app.store.delete_brand if kind == "brands" else app.store.delete_style
    with app.store.transaction():
        row = get_fn(req.params["setting_id"])
        editable(row, b)
        delete_fn(row["id"])
        deleted = get_fn(row["id"])["deleted_at"] is not None
    return Response.json(200, {"deleted": deleted})


# ---- この機械の口 ----
# 画面が見せるのは「いまファイルに書いてある値」で、`app.settings` の写しではない。
# 保存しても動いているサーバの bind は変わらないので、両方を出して食い違いを見せる（手で直したときも同じ）。
# 効かせるのは画面の「再起動」（`POST /api/server/restart`。server/restart.py）。

def server_block(app):
    """この機械の口: ファイルの値・動いている値・再起動が要るか・再起動のボタンに要るもの（走っている仕事の数・新しい URL）。"""
    st = app.settings_state()
    cfg = st["disk"] or {"host": app.host, "port": app.port, "host_names": []}
    workers = app.workers
    return {
        "host": cfg["host"],
        "host_names": list(cfg["host_names"]),
        "port": cfg["port"],
        "bind_scope": bind_scope(cfg["host"]),
        "running_host": app.host,
        "running_port": app.bound_port,
        "running_url": app.url,
        "restart_required": st["restart_required"],
        "settings_error": st["error"],
        "expected_url": st["expected_url"] or app.url,
        "running_jobs": len(workers.busy_jobs()) if workers is not None else 0,
        "restartable": bool(app.restartable),
        "restarting": app.restarting is not None,
        "loopback_hosts": list(LOOPBACK_HOSTS),
    }


@boundary
def save_server(app, req):
    b = payload(req, ("host", "host_names", "port"))
    host = b.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ApiError(400, "bad_bind_address", "公開する IP アドレスを入力してください。")
    names_raw = b.get("host_names") or []
    if isinstance(names_raw, str) or not isinstance(names_raw, list):
        raise ApiError(400, "bad_host_names", "ホスト名は 1 行に 1 つずつ書いてください。")
    names = [n.strip() for n in names_raw if isinstance(n, str) and n.strip()]
    port = b.get("port")
    if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not (1024 <= port <= 65535)):
        raise ApiError(400, "bad_port", "ポート番号は 1024〜65535 の数字で入力してください。")
    try:
        save_server_section(app.root.settings_path, host=host.strip(), host_names=names, port=port)
    except ValidationError as exc:
        # ここの文言は利用者に向けて書いてある（どこに開くかは利用者が決める）。そのまま見せる。
        raise ApiError(400, "bad_bind_address", str(exc)) from None
    return Response.json(200, {"server": server_block(app), "verified": True})
