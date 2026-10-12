"""ブランドのロゴの置き場所（設定に保存する形と、設定画面の見本の位置）。標準ライブラリだけの純関数。

v2 ではロゴの置き場所を決めるのはエンジンである（`--logo-position`。ふさがっていれば別の角へ逃がす）。
ここが持つのは 2 つだけ:

- 保存する形: `logo.placement = {"corner": <角>}`。角はエンジンの `--logo-position` と同じ 3 つ
  （右下・左下・右上。左上はエンジンが選ばせない）
- 設定画面の**見本**に描く位置: エンジン（html-slide の `scripts/deck.py`）の既定の寸法を写した近似
  （ロゴ・フッタ・ページ番号・透かし）。実際の位置はエンジンが描くときに決まる（ふさがった角は別の角へ逃がす）。
  エンジンで描いた見本（本物の 1 枚）は出さない（描くのに Chrome・時間がかかる。P4 では見送り）

v1 の「ロゴの予約枠（clean area）」と枠の検査は持たない（エンジンが要らなくした）。
"""

from __future__ import annotations

from typing import Any

from .errors import ValidationError

CANVAS = (1920, 1080)
# エンジンの --logo-position の値と同じ並び（既定が先頭）
LOGO_POSITIONS = ("bottom-right", "bottom-left", "top-right")
LOGO_POSITION_LABELS = {"bottom-right": "右下", "bottom-left": "左下", "top-right": "右上"}
PLACEMENT_KEY = "corner"
DEFAULT_PLACEMENT: dict[str, Any] = {PLACEMENT_KEY: "bottom-right"}

# 見本の位置（html-slide の scripts/deck.py の既定の数字の写し。1920×1080）
_BOX_W, _BOX_H = 329, 44          # ロゴを収める枠
_SIDE = 120                       # 左右の余白（本文の余白にそろう）
_ROW_TOP, _ROW_H = 1006, round(22 * 1.4)   # 下の文字の行（フッタ・ページ番号）
_BOTTOM_MID_Y = 1006 + 22 * 1.4 / 2   # 下の文字の行の中心
_TOP_MID_Y = 46                   # 右上に置くときの縦の中心
_LOGO_GAP = 28                    # 左下のロゴと、その右へ寄せるフッタの間
_FOOTER_END, _FOOTER_END_WITH_NO = 1420, 912   # フッタの右端（ページ番号を出すときは番号の左まで）
_PAGENO_X, _PAGENO_W = 900, 120   # ページ番号（中央下）
_WATERMARK_TOP, _WATERMARK_H = 1054, 16        # 透かし（中央下の端）
SMALL_LOGO_MIN_SIDE = 30          # 短辺がこれ未満なら小さいと知らせる


class LogoError(ValidationError, ValueError):
    """ロゴの設定・画像が使えない。"""


def validate_placement(placement: Any, where: str = "logo.placement") -> dict[str, Any]:
    """`{"corner": <角>}` だけを受ける（省略は既定）。知らないキーは断る。"""
    if placement is None:
        return dict(DEFAULT_PLACEMENT)
    if not isinstance(placement, dict):
        raise LogoError(f"{where} はオブジェクト")
    unknown = sorted(set(placement) - {PLACEMENT_KEY})
    if unknown:
        raise LogoError(f"{where} に未知のキーがあります: {', '.join(unknown)}")
    corner = placement.get(PLACEMENT_KEY, DEFAULT_PLACEMENT[PLACEMENT_KEY])
    if corner not in LOGO_POSITIONS:
        raise LogoError(f"{where}.{PLACEMENT_KEY} は {'/'.join(LOGO_POSITIONS)} のいずれか")
    return {PLACEMENT_KEY: corner}


def preview_box(size: tuple[int, int], corner: str) -> dict[str, Any]:
    """原本の寸法と角 → 見本に描く `[x, y, w, h]`（1920×1080）と知らせ。

    縦横比を保って枠に収める（縮めるだけ・引き伸ばさない）。寸法が 0 なら断る。
    """
    w, h = size
    if w <= 0 or h <= 0:
        raise LogoError("ロゴの画像の大きさを読み取れません。画像を選び直してください。")
    if corner not in LOGO_POSITIONS:
        raise LogoError(f"logo.placement.{PLACEMENT_KEY} は {'/'.join(LOGO_POSITIONS)} のいずれか")
    scale = min(1.0, _BOX_W / w, _BOX_H / h)
    lw, lh = max(1, round(w * scale)), max(1, round(h * scale))
    if corner == "bottom-left":
        x, y = _SIDE, round(_BOTTOM_MID_Y - lh / 2)
    elif corner == "top-right":
        x, y = CANVAS[0] - _SIDE - lw, round(_TOP_MID_Y - lh / 2)
    else:
        x, y = CANVAS[0] - _SIDE - lw, round(_BOTTOM_MID_Y - lh / 2)
    warnings: list[dict[str, str]] = []
    if min(lw, lh) < SMALL_LOGO_MIN_SIDE:
        warnings.append({"kind": "small_logo", "message":
                         f"ロゴが {lw}×{lh}px と小さく置かれます。横長のロゴか、大きい原本があればそちらを使ってください。"})
    elif scale == 1.0 and (w < _BOX_W and h < _BOX_H):
        warnings.append({"kind": "original_size", "message":
                         f"この画像は枠より小さいため、拡大せず原寸 {lw}×{lh}px で置きます。この大きさでよいか確認してください。"})
    return {"logo_xywh": [x, y, lw, lh], "scale": scale, "warnings": warnings}


def corner_choices() -> list[dict[str, Any]]:
    """設定画面の角の選択肢。v1 の予約枠（`clean_area`）は返さない。

    `mark` は角の小さな絵に描く印の位置（枠いっぱいの横長のロゴを置いたときの `[x, y, w, h]`。1920×1080）。
    画面は角の名前から位置を作らず、この数字を canvas で割るだけ。"""
    return [{"value": c, "label": LOGO_POSITION_LABELS[c], "mark": preview_box((_BOX_W, _BOX_H), c)["logo_xywh"]}
            for c in LOGO_POSITIONS]


def chrome_boxes(logo_xywh: list[int] | None, corner: str, *, footer: bool, page_numbers: bool,
                 watermark: bool) -> dict[str, Any]:
    """見本に描く下の部品の位置（エンジンの中身のページの既定の寸法の写し。1920×1080 の `[x, y, w, h]`）。

    - フッタ: 左下（左の余白から）。ロゴが左下なら、ロゴの右へ寄せる。ページ番号を出すときは番号の手前まで
    - ページ番号: 中央下（見本は「3」を置く）
    - 透かし: 中央下の端（下の文字の行の下）
    表紙・章扉・写真の全面のページには、フッタとページ番号は入らない（エンジンの決まり。見本は中身のページの姿）。
    """
    out: dict[str, Any] = {"footer": None, "page_number": None, "watermark": None}
    if footer:
        x = _SIDE
        if logo_xywh is not None and corner == "bottom-left":
            x = logo_xywh[0] + logo_xywh[2] + _LOGO_GAP
        end = _FOOTER_END_WITH_NO if page_numbers else _FOOTER_END
        out["footer"] = [x, _ROW_TOP, max(0, end - x), _ROW_H]
    if page_numbers:
        out["page_number"] = [_PAGENO_X, _ROW_TOP, _PAGENO_W, _ROW_H]
    if watermark:
        out["watermark"] = [CANVAS[0] // 2 - 300, _WATERMARK_TOP, 600, _WATERMARK_H]
    return out
