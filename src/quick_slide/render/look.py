"""設定（ブランド・スタイル・書式）→ エンジンの引数・テーマの YAML・台本の一番外のキー・台本係への方針（P4）。

**描く回を始めるたびに読む**（仕事の最初に 1 回。止まった回を続けるときは読み直さず、控えた引数で描く）。
デッキはブランド・スタイルの改訂を指しているが、そのブランド・スタイルに新しい改訂があれば、そちらへ進めてから使う
（設定を変えたら次に描く回から効く。描き直しは勝手に始めない）。消したブランド・スタイルは、指していた改訂のまま。

写し方:

| 設定 | エンジン |
|---|---|
| ブランドの配色 | `--theme <回の控え>/settings/theme.yaml`（`palette.engine_theme`） |
| ブランドの字体 | テーマの `font`・`font_files`（エンジンの設定の `fonts:` に字体ファイルがあるときだけ。`brand_font`・P5） |
| ブランドのロゴ（使う種類の画像） | `--logo`（PNG・JPEG だけ。加工しない） |
| ブランドの白いロゴ（`logo_white`） | `--logo-white`（ピッチだけ。縦横比がロゴと 2% 以内で合うときだけ） |
| ロゴの角 | `--logo-position`（bottom-right・bottom-left・top-right） |
| フッタの文字 | 台本の一番外の `footer`（`scripts/engine.json` に書くときに入れる。台本の版は変えない） |
| ページ番号 | 台本の一番外の `page_numbers` |
| 透かし | `--watermark` |
| スタイルの `engine_style[モード]` | `--style`（モーションは渡さない。テーマとロゴだけ受ける） |
| スタイルの `image_look` | `--image-look` |
| スタイルの `style_note`（と会話の作風の一言） | ピッチは `--style-note`。どちらも台本係の指示文 |
| スタイルの `emphasis_prompt`・`tone`・`allowed_layouts` | 台本係の指示文（使ってよい型はサーバも確かめる） |

会話が `style`・`image_look`・`style_note` を言ったときは、その値が勝つ（`overrides`。デッキの以後の回にも引き継ぐ）。
**infographic（全ページを 1 枚）かどうかは、デッキの最初の回で決まり、あとから替えない**（ページの数え方が変わるため。
替わる設定のときは、いまの描き方のままにして控えに理由を書く）。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from .. import contenttypes, motion, palette
from ..engines import IMAGE_LOOKS, engine_for
from ..logo import DEFAULT_PLACEMENT, PLACEMENT_KEY
from ..paths import DataRoot

log = logging.getLogger("quick_slide.render.look")

VARIANT_TO_ROLE = {"normal": "logo_normal", "white": "logo_white", "transparent": "logo_transparent",
                   "symbol": "logo_symbol"}
LOGO_MEDIA = ("image/png", "image/jpeg")
WHITE_ASPECT_TOLERANCE = 0.02
TONE_WORDS = {"firm": "硬め（です・ます調で言い切る。くだけた言い回しを使わない）", "standard": "標準",
              "soft": "柔らかめ（話しかけるように。難しい語は言い換える）"}


def resolve(store: Any, root: DataRoot, run: dict[str, Any], *, previous_style: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """この回に使う設定を決め、テーマの YAML を回の控えに書く。(runs.args に足すもの, 再現の記録) を返す。

    `previous_style` はこのデッキの前の回のエンジンのスタイル（infographic かどうかを保つため）。最初の回は None。
    store を触るので、呼ぶ側がサーバの錠を持つ。
    """
    deck = store.deck(run["deck_id"])
    mode = run["mode"]
    engine = engine_for(mode)
    args = run["args"]
    overrides = args.get("overrides")
    if overrides is None:
        # P4 より前の回（会話の画風・一言は args に直に入っていた）。スタイルの名前は付属スタイルが表しているので見ない
        overrides = {k: args[k] for k in ("image_look", "style_note") if args.get(k)}
    notes: list[str] = []

    brand_rev = _current_revision(store, "brand", deck["brand_revision_id"])
    style_rev = _current_revision(store, "style", deck["style_revision_id"])
    store.follow_deck_settings(deck["id"], brand_revision_id=brand_rev["id"], style_revision_id=style_rev["id"])
    brand_cfg, style_cfg = brand_rev["config"], style_rev["config"]

    # --- スタイル ---
    wanted = overrides.get("style") or (style_cfg.get("engine_style") or {}).get(mode) or engine.default_style
    if wanted not in engine.styles:
        notes.append(f"スタイル {wanted} はこのモードで使えないので、{engine.default_style} で描きます")
        wanted = engine.default_style
    if previous_style is not None and (previous_style == "infographic") != (wanted == "infographic"):
        notes.append("全ページを 1 枚に描くか（infographic）は最初の回で決まるので、前の回のスタイルのまま描きます")
        wanted = previous_style
    look = overrides.get("image_look") or style_cfg.get("image_look")
    if look not in IMAGE_LOOKS:
        look = None
    note_parts = [p.strip() for p in (overrides.get("style_note"), style_cfg.get("style_note")) if p and p.strip()]
    style_note = "。".join(dict.fromkeys(note_parts))

    # --- ブランド: テーマ ---
    colors = palette.snapshot_colors(brand_cfg["colors"])
    theme = palette.engine_theme(colors)
    settings_dir = root.run_settings_dir(run["deck_id"], run["id"])
    settings_dir.mkdir(parents=True, exist_ok=True)
    # 字体の控え（使えなかった理由も）は記録の `font` に書く（付属のブランドの字体は名前だけなので毎回ここに出る）
    font = brand_font(root, brand_cfg, engine.skill)
    theme_path = settings_dir / "theme.yaml"
    theme_path.write_text(palette.theme_yaml(theme) + font_yaml(font), encoding="utf-8")

    # --- ブランド: ロゴ ---
    logo_cfg = brand_cfg.get("logo") or {}
    corner = (logo_cfg.get("placement") or DEFAULT_PLACEMENT).get(PLACEMENT_KEY, DEFAULT_PLACEMENT[PLACEMENT_KEY])
    assets = {a["role"]: a for a in brand_rev.get("assets") or []}
    logo_path = logo_sha = None
    variant = logo_cfg.get("variant") or "none"
    if variant != "none":
        logo_path, logo_sha, why = _asset_file(store, root, assets.get(VARIANT_TO_ROLE.get(variant, "")))
        if why:
            notes.append(f"ロゴを置きません（{why}）")
    white_path = white_sha = None
    if logo_path is not None and mode == "pitch" and variant != "white" and "logo_white" in assets:
        white_path, white_sha, why = _asset_file(store, root, assets["logo_white"])
        if white_path is not None and not _same_aspect(assets[VARIANT_TO_ROLE[variant]], assets["logo_white"]):
            white_path = white_sha = None
            why = "白いロゴの縦横比がロゴと違う"
        if why:
            notes.append(f"白いロゴは使いません（{why}）")

    # --- 書式（ブランドの中）---
    fmt = brand_cfg.get("format") or {}
    footer = ((brand_cfg.get("footer") or {}).get("text") or "").strip()

    allowed = contenttypes.allowed(style_cfg.get("allowed_layouts"))
    plan = {
        "theme": str(theme_path),
        "logo": None if logo_path is None else str(logo_path),
        "logo_white": None if white_path is None else str(white_path),
        "logo_position": corner,
        "footer": footer,
        "page_numbers": bool(fmt.get("page_numbers")),
        "watermark": (fmt.get("watermark") or "").strip(),
        "allowed_types": allowed,
        "emphasis": (style_cfg.get("emphasis_prompt") or "").strip(),
        "tone": style_cfg.get("tone") or "standard",
        "brand_revision_id": brand_rev["id"],
        "style_revision_id": style_rev["id"],
    }
    update = {"style": wanted, "image_look": look, "style_note": style_note, "look": plan}
    record = {
        "brand": {"id": brand_rev["brand_id"], "revision_id": brand_rev["id"], "revision": brand_rev["revision"]},
        "style": {"id": style_rev["style_id"], "revision_id": style_rev["id"], "revision": style_rev["revision"]},
        "theme": theme, "theme_file": str(theme_path),
        "font": font,
        "theme_sha256": hashlib.sha256(theme_path.read_bytes()).hexdigest(),
        "logo": None if logo_path is None else {"path": str(logo_path), "sha256": logo_sha, "variant": variant},
        "logo_white": None if white_path is None else {"path": str(white_path), "sha256": white_sha},
        "logo_position": corner, "footer": footer, "page_numbers": plan["page_numbers"],
        "watermark": plan["watermark"], "engine_style": wanted, "image_look": look, "style_note": style_note or None,
        "allowed_types": allowed, "overrides": overrides or None, "notes": notes,
    }
    return update, record


# エンジンの既定の字体（名前が同じブランドの字体は、渡さずに既定のまま組む）。html-slide は DADS の Noto Sans JP（ADR 0020）。
# image-slide は画像に焼くので、游ゴシックのまま
ENGINE_DEFAULT_FONT = "Noto Sans JP"
DEFAULT_FONT_NAMES = {"noto sans jp", "notosansjp", "noto sans cjk jp"}
IMAGE_ENGINE_DEFAULT_FONT = "Yu Gothic"
IMAGE_ENGINE_FONT_NAMES = {"yu gothic", "yugothic", "游ゴシック", "游ゴシック体"}


def default_font(skill: str) -> tuple[str, set[str]]:
    """そのエンジンの既定の字体の名前と、同じ字体を指す名前の一覧（小文字）。"""
    if skill == "image-slide":
        return IMAGE_ENGINE_DEFAULT_FONT, IMAGE_ENGINE_FONT_NAMES
    return ENGINE_DEFAULT_FONT, DEFAULT_FONT_NAMES


_UNSAFE_FONT = set('"\\<>;{}')


def brand_font(root: DataRoot, brand_cfg: dict[str, Any], skill: str) -> dict[str, Any]:
    """ブランドの字体 → エンジンのテーマの `font`・`font_files`（P5）。

    エンジンは字体の名前だけでは組めない（文字の幅を字体ファイルで測る・PNG に焼く）。だから**字体ファイルの在りかが
    分かるときだけ**渡す: エンジンの設定（`engines/<スキル>/config.yaml`）の `fonts:` にその名前の regular（と bold）が
    書いてあり、ファイルがあること。分からなければエンジンの既定（html-slide は Noto Sans JP・image-slide は游ゴシック）のまま組み、回の記録（`look.font.note`）に理由を書く。
    ブランドの字体は本文（`fonts.body`）の名前、無ければ見出し（`fonts.heading`）。代わりの名前（`fallback_families`）も順に見る。
    戻り値: {"requested", "applied"（渡した名前か None）, "files"（regular・bold）, "note"}。
    """
    fonts = brand_cfg.get("fonts") if isinstance(brand_cfg.get("fonts"), dict) else {}
    names: list[str] = []
    for role in ("body", "heading"):
        f = fonts.get(role) if isinstance(fonts.get(role), dict) else {}
        for n in [f.get("requested_family"), *(f.get("fallback_families") or [])]:
            if isinstance(n, str) and n.strip() and n.strip() not in names:
                names.append(n.strip())
    requested = names[0] if names else None
    default_name, default_names = default_font(skill)
    out: dict[str, Any] = {"requested": requested, "applied": None, "files": None, "note": None}
    if requested is None or requested.lower() in default_names:
        return out
    from .caches import engine_config

    configured = engine_config(root, skill).get("fonts")
    configured = configured if isinstance(configured, dict) else {}
    for name in names:
        if name.lower() in default_names or name in ("sans-serif", "serif") or set(name) & _UNSAFE_FONT:
            continue
        spec = configured.get(name)
        if not isinstance(spec, dict) or not spec.get("regular"):
            continue
        files = {k: str(Path(str(spec.get(k) or spec["regular"])).expanduser()) for k in ("regular", "bold")}
        if all(Path(v).is_file() for v in files.values()):
            out.update(applied=name, files=files)
            if name != requested:
                out["note"] = f"ブランドの字体 {requested} の字体ファイルが分からないので、代わりの {name} で組みます"
            return out
    out["note"] = (f"ブランドの字体 {requested} の字体ファイルが分からないので、エンジンの既定（{default_name}）で組みます"
                   f"（エンジンの設定の fonts: に字体ファイルの在りかを書けば使います）")
    return out


def font_yaml(font: dict[str, Any]) -> str:
    """テーマの YAML に足す行（字体を渡すときだけ）。値は JSON の文字列（YAML の二重引用符の書き方と同じ）。"""
    if not font.get("applied"):
        return ""
    import json

    files = font["files"]
    return (f"font: {json.dumps(font['applied'], ensure_ascii=False)}\n"
            f"font_files:\n  regular: {json.dumps(files['regular'], ensure_ascii=False)}\n"
            f"  bold: {json.dumps(files['bold'], ensure_ascii=False)}\n")


def _current_revision(store: Any, kind: str, revision_id: str) -> dict[str, Any]:
    """デッキが指す改訂の持ち主に新しい改訂があればそれ（消したものは指していた改訂のまま）。"""
    rev = store.brand_revision(revision_id) if kind == "brand" else store.style_revision(revision_id)
    owner = store.brand(rev["brand_id"]) if kind == "brand" else store.style(rev["style_id"])
    if owner["deleted_at"] is None and owner["current_revision_id"] and owner["current_revision_id"] != revision_id:
        return store.brand_revision(owner["current_revision_id"]) if kind == "brand" else \
            store.style_revision(owner["current_revision_id"])
    return rev


def _asset_file(store: Any, root: DataRoot, row: dict[str, Any] | None) -> tuple[Path | None, str | None, str | None]:
    """ブランドの画像のファイル（中身が登録と同じか確かめる）。(パス, sha256, 使えない理由)。"""
    if row is None:
        return None, None, "画像が登録されていない"
    asset = store.asset(row["asset_id"])
    if asset["media_type"] not in LOGO_MEDIA:
        return None, None, "PNG か JPEG ではない（エンジンが受けない）"
    path = root.root / asset["relative_path"]
    try:
        data = path.read_bytes()
    except OSError:
        return None, None, "画像のファイルが見つからない"
    sha = hashlib.sha256(data).hexdigest()
    if sha != asset["sha256"]:
        return None, None, "画像の中身が登録と違う"
    return path, sha, None


def _same_aspect(a: dict[str, Any], b: dict[str, Any]) -> bool:
    try:
        ra, rb = a["width"] / a["height"], b["width"] / b["height"]
    except (KeyError, TypeError, ZeroDivisionError):
        return False
    return abs(rb - ra) <= WHITE_ASPECT_TOLERANCE * ra


def apply_format(view: dict[str, Any], look: dict[str, Any] | None) -> dict[str, Any]:
    """エンジンに渡す台本（`scriptdoc.engine_view` 済み）に、ブランドのフッタとページ番号を入れる。
    台本の版（台本係が書いたもの）は変えない。設定が無い（P4 より前の回を続ける）ときはそのまま。"""
    if not look:
        return view
    out = dict(view)
    if look.get("footer"):
        out["footer"] = look["footer"]
    else:
        out.pop("footer", None)
    out["page_numbers"] = bool(look.get("page_numbers"))
    return out


def writer_guidance(args: dict[str, Any]) -> list[str]:
    """台本係の「資料の属性」に足す行（スタイルの書き方の方針・調子・使ってよい型）。"""
    look = args.get("look") or {}
    lines = []
    if look.get("emphasis"):
        lines.append(f"書き方の方針（スタイル）: {look['emphasis']}")
    tone = look.get("tone")
    if tone and tone != "standard":
        lines.append(f"文章の調子: {TONE_WORDS.get(tone, tone)}")
    allowed = look.get("allowed_types")
    if allowed and not motion.is_motion(args):   # モーションの場面は型を持たない
        lines.append("使ってよい型: " + "・".join(allowed) + "（ほかの型は使わない）")
    return lines


def type_problems(slides: list[dict[str, Any]], args: dict[str, Any], *, only: set[str] | None = None) -> list[str]:
    """使ってよい型の外の型を使ったページ（`NG:` の行。台本係に書き直させる）。

    `only` を渡せば、その id のページだけを見る（直しでは、書き換えたページだけ。前からあるページは問わない）。"""
    allowed = (args.get("look") or {}).get("allowed_types")
    if not allowed:
        return []
    out = []
    for i, s in enumerate(slides, 1):
        if only is not None and str(s.get("id") if isinstance(s, dict) else "") not in only:
            continue
        t = s.get("type") if isinstance(s, dict) else None
        if isinstance(t, str) and t not in allowed:
            out.append(f"NG: {i}枚目（id={s.get('id')}）の型 {t} は、このスタイルで使わない型。"
                       f"使ってよい型（{'・'.join(allowed)}）で書き直す")
    return out


BRAND_COLOR_MESSAGE = ("ブランドの色の組み合わせでは読めない文字ができるため、描けません。"
                       "設定のブランドの色（地の色・基本色・強調色）を見直してから、もう一度頼んでください。")


def theme_refused(problems: list[str]) -> bool:
    """エンジンの検査がテーマの色（ブランド）で止まったか。台本係が直せるものではないので、書き直させずに止める。"""
    return any(p.startswith("NG: テーマ") for p in problems)
