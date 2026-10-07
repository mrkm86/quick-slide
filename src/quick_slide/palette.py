"""ブランドの入力色から、保存する配色スナップショットを決定的に作る。

ここが配色算出規則の唯一の正本である。呼出元はこの結果を改訂へ保存し、
過去の改訂を読み出すときに再計算してはならない。
"""
from __future__ import annotations

import colorsys
import re
from typing import Any


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_GRAY_LIGHTNESS = (0.10, 0.25, 0.40, 0.55, 0.70, 0.84, 0.93, 0.97)
_FLAT_OVERRIDES = ("primary_light", "primary_dark", "border", "surface", "base", "gray")
_SEMANTIC_KEYS = ("success", "warning", "error", "info")


def _hex_to_hls(value: str) -> tuple[float, float, float]:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError("色は #RRGGBB の 6 桁 hex で指定してください")
    rgb = tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(*rgb)


def _hls_to_hex(hue: float, lightness: float, saturation: float) -> str:
    rgb = colorsys.hls_to_rgb(hue % 1.0, max(0.0, min(1.0, lightness)), max(0.0, min(1.0, saturation)))
    return "#" + "".join(f"{round(channel * 255):02X}" for channel in rgb)


def _require_color(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError(f"{name} は #RRGGBB の 6 桁 hex で指定してください")
    return value


def _semantic_color(hue: float, source_hls: tuple[float, float, float]) -> str:
    """用途ごとの色相を固定し、彩度・明度だけをブランドの強さへ寄せる。"""
    _, lightness, saturation = source_hls
    return _hls_to_hex(hue, 0.38 + lightness * 0.16, 0.48 + saturation * 0.20)


def _relative_luminance(value: str) -> float:
    """WCAG の sRGB 相対輝度。配色の可読性計算はここだけに置く。"""
    _require_color(value, "色")
    channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(first: str, second: str) -> float:
    """WCAG コントラスト比（明るい方 + .05）/（暗い方 + .05）。"""
    one, two = _relative_luminance(first), _relative_luminance(second)
    return (max(one, two) + 0.05) / (min(one, two) + 0.05)


def _readable_semantic(value: str, base: str, minimum: float) -> str:
    """色相・彩度を保ち、base 上で読める最も近い明度へ決定的に寄せる。"""
    if _contrast_ratio(value, base) >= minimum:
        return value
    hue, lightness, saturation = _hex_to_hls(value)
    # 白地なら暗く、黒地なら明るくする。ただし任意の base でも両端を調べる。
    candidates = []
    for direction in (-1, 1):
        for step in range(1, 257):
            candidate_lightness = lightness + direction * step / 256
            if candidate_lightness < 0 or candidate_lightness > 1:
                break
            candidate = _hls_to_hex(hue, candidate_lightness, saturation)
            if _contrast_ratio(candidate, base) >= minimum:
                candidates.append((step, candidate))
                break
    # 黒または白は常に十分な差を作れる。丸めで境界を越えないよう念のため検査する。
    if not candidates:
        endpoints = (_hls_to_hex(hue, 0, saturation), _hls_to_hex(hue, 1, saturation))
        readable = [color for color in endpoints if _contrast_ratio(color, base) >= minimum]
        if not readable:
            raise ValueError("base に対するセマンティック色のコントラストを確保できません")
        return readable[0]
    return min(candidates, key=lambda candidate: candidate[0])[1]


def derive(primary: str, accent: str, *, info: str | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """基本色・強調色から表示用と保存用の配色を返す。

    ``overrides`` は ``derived`` と同じキー（``semantic`` はその子キー）を
    部分指定できる。``info`` は意図を明示する入力であり、上書き一覧には含めない。
    """
    primary = _require_color(primary, "primary")
    accent = _require_color(accent, "accent")
    if info is not None:
        info = _require_color(info, "info")
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise ValueError("overrides はオブジェクトで指定してください")
    unknown = set(overrides) - set(_FLAT_OVERRIDES) - {"semantic"}
    if unknown:
        raise ValueError("overrides に未知のキーがあります")

    primary_hls = _hex_to_hls(primary)
    accent_hls = _hex_to_hls(accent)
    hue, lightness, saturation = primary_hls
    soft_saturation = 0.0 if saturation < 0.05 else saturation
    derived: dict[str, Any] = {
        # 主役色の色相を残し、明暗だけを十分に離す。
        "primary_light": _hls_to_hex(hue, 0.91, min(0.48, max(0.16, soft_saturation * 0.48)) if soft_saturation else 0),
        "primary_dark": _hls_to_hex(hue, max(0.18, lightness * 0.56), saturation),
        # 背景・罫線は主役色の色相を低彩度で残す。白地の上でも罫線は埋もれない。
        "border": _hls_to_hex(hue, 0.87, min(0.30, max(0.12, soft_saturation * 0.30)) if soft_saturation else 0),
        "surface": _hls_to_hex(hue, 0.975, min(0.36, max(0.10, soft_saturation * 0.38)) if soft_saturation else 0),
        "base": "#FFFFFF",
        "gray": [_hls_to_hex(0, value, 0) for value in _GRAY_LIGHTNESS],
        "semantic": {
            "success": _semantic_color(0.38, primary_hls),
            "warning": _semantic_color(0.09, primary_hls),
            "error": _semantic_color(0.0, primary_hls),
            "info": info if info is not None else _semantic_color(0.58, accent_hls),
        },
    }

    overridden: list[str] = []
    for key in _FLAT_OVERRIDES:
        if key not in overrides:
            continue
        value = overrides[key]
        if key == "gray":
            if not isinstance(value, list) or not value:
                raise ValueError("overrides.gray は 1 色以上の配列で指定してください")
            derived[key] = [_require_color(color, f"overrides.gray[{index}]") for index, color in enumerate(value)]
        else:
            derived[key] = _require_color(value, f"overrides.{key}")
        overridden.append(key)

    semantic = overrides.get("semantic")
    if semantic is not None:
        if not isinstance(semantic, dict) or set(semantic) - set(_SEMANTIC_KEYS):
            raise ValueError("overrides.semantic は既知の色役割だけを指定してください")
        for key, value in semantic.items():
            if key == "info" and info is not None:
                continue
            derived["semantic"][key] = _require_color(value, f"overrides.semantic.{key}")
            overridden.append(f"semantic.{key}")

    # 役割色は小さな文字にも使えるため AA（4.5:1）を満たす。
    # warning は橙系の意味を保つと 4.5:1 に届きにくいため、3:1 以上の大きな文字向けとする。
    minimums = {"success": 4.5, "error": 4.5, "info": 4.5, "warning": 3.0}
    for key, minimum in minimums.items():
        derived["semantic"][key] = _readable_semantic(derived["semantic"][key], derived["base"], minimum)

    return {
        "input": {"primary": primary, "accent": accent, "info": info},
        "derived": derived,
        "overridden": overridden,
        "note": "配色は算出値です。生成される絵で実際にこの色が使われる保証はありません。",
    }


def derive_from_colors(colors: dict[str, Any]) -> dict[str, Any]:
    """brand config の ``colors`` からプレビュー表現を作る。"""
    semantic = colors.get("semantic")
    info = colors.get("info") if "info" in colors else (semantic.get("info") if isinstance(semantic, dict) else None)
    overrides = {key: colors[key] for key in _FLAT_OVERRIDES if key in colors}
    if isinstance(semantic, dict):
        overrides["semantic"] = {key: value for key, value in semantic.items() if key != "info"}
    return derive(colors.get("primary"), colors.get("accent"), info=info, overrides=overrides)


def snapshot_colors(colors: dict[str, Any]) -> dict[str, Any]:
    """入力色と算出一式を、将来の算出規則変更から独立した保存形にする。"""
    palette = derive_from_colors(colors)
    return {
        "primary": colors["primary"],
        "accent": colors["accent"],
        **palette["derived"],
    }


def custom_roles(colors: dict[str, Any]) -> dict[str, Any]:
    """保存済みの ``colors``（スナップショット）のうち、算出では出ない＝人が決めた役割色だけを返す。

    対象はブランドの編集画面が扱わない役割（``semantic`` の success / warning / error と ``gray``）。
    スナップショットは算出値と上書き値を区別せずに持つので、同じ入力（基本色・強調色・情報色・平らな上書き）から
    算出し直して、違うものだけを「人が決めた値」とみなす。算出値と同じものは返さない（基本色を変えたら算出し直す）。
    戻り値は ``{"semantic": {...}, "gray": [...]}`` の部分集合。読めない形なら空。
    """
    if not isinstance(colors, dict):
        return {}
    semantic = colors.get("semantic") if isinstance(colors.get("semantic"), dict) else {}
    info = colors.get("info") if "info" in colors else semantic.get("info")
    flat = {key: colors[key] for key in _FLAT_OVERRIDES if key in colors and key != "gray"}
    try:
        baseline = derive(colors.get("primary"), colors.get("accent"), info=info, overrides=flat)["derived"]
    except ValueError:
        return {}
    kept: dict[str, Any] = {}
    custom_semantic = {
        key: semantic[key] for key in ("success", "warning", "error")
        if isinstance(semantic.get(key), str) and semantic[key].lower() != baseline["semantic"][key].lower()
    }
    if custom_semantic:
        kept["semantic"] = custom_semantic
    gray = colors.get("gray")
    if isinstance(gray, list) and [str(g).lower() for g in gray] != [g.lower() for g in baseline["gray"]]:
        kept["gray"] = list(gray)
    return kept


# --- エンジンのテーマ（P4）---------------------------------------------------------------------------------------
# ブランドの配色 → エンジン（html-slide・image-slide 共通）のテーマの YAML のキー。対応は docs/P4-NOTES.md の表。
# エンジンのテーマのキーは background・ink・primary（必須）と accent・muted・surface・font・font_files（任意）。
# エンジンは primary・accent から文字の色・面の色・淡い地を自分で作り、読めない組み合わせは描かずに止める（rc=2）。
# だからここでは「人が決めた色」だけを渡し、算出はエンジンに任せる。
INK_STEP = 1             # gray の何段目を本文の文字にするか（算出の gray[1] は明度 0.25 = エンジンの既定 #404040）
INK_MIN_CONTRAST = 7.0   # 本文の文字と地の差がこれに届かなければ、gray・黒・白から一番差の大きい色にする
SURFACE_MIN_CONTRAST = 7.0


def engine_theme(colors: dict[str, Any]) -> dict[str, str]:
    """保存した配色（`snapshot_colors` の形）→ エンジンのテーマ（キーの順は YAML に書く順）。

    | ブランド | テーマ |
    |---|---|
    | 地の色（base。既定は白） | background |
    | gray の 2 段目（読めなければ gray・黒・白で一番読める色） | ink |
    | 基本色（primary） | primary |
    | 強調色（accent。基本色と同じなら書かない＝エンジンが primary を使う） | accent |
    | 面の色（surface。人が上書きしたときだけ・文字が読めるときだけ） | surface |
    | 情報色（info）・成功/注意/エラー・主役色（淡/濃）・区切り線 | 渡さない（エンジンに役が無い・エンジンが作る） |
    | 字体（fonts） | ここでは渡さない（`render/look.brand_font` が、字体ファイルの在りかが分かるときだけ font・font_files を足す） |
    """
    primary = _require_color(colors.get("primary"), "primary").upper()
    accent = _require_color(colors.get("accent"), "accent").upper()
    base = colors.get("base") if isinstance(colors.get("base"), str) and _HEX.fullmatch(colors["base"]) else "#FFFFFF"
    base = base.upper()
    gray = [g for g in (colors.get("gray") or []) if isinstance(g, str) and _HEX.fullmatch(g)]
    ink = gray[INK_STEP].upper() if len(gray) > INK_STEP else "#404040"
    if _contrast_ratio(ink, base) < INK_MIN_CONTRAST:
        ink = max([g.upper() for g in gray] + ["#000000", "#FFFFFF"], key=lambda c: _contrast_ratio(c, base))
    theme = {"background": base, "ink": ink, "primary": primary}
    if accent != primary:
        theme["accent"] = accent
    surface = colors.get("surface")
    if isinstance(surface, str) and _HEX.fullmatch(surface):
        semantic = colors.get("semantic") if isinstance(colors.get("semantic"), dict) else {}
        info = colors.get("info") if "info" in colors else semantic.get("info")
        try:
            computed = derive(primary, accent, info=info)["derived"]["surface"]
        except ValueError:
            computed = surface
        if surface.lower() != computed.lower() and _contrast_ratio(ink, surface) >= SURFACE_MIN_CONTRAST:
            theme["surface"] = surface.upper()
    return theme


def theme_yaml(theme: dict[str, str]) -> str:
    """テーマを YAML の文にする（キーは決まった順・値は "#RRGGBB" だけなので、引用符で囲めば足りる）。"""
    lines = ["# Quick Slide がブランドから作ったテーマ（描いた回の控え。手で直さない）"]
    for key in ("background", "ink", "primary", "accent", "muted", "surface"):
        if key in theme:
            lines.append(f'{key}: "{_require_color(theme[key], key)}"')
    return "\n".join(lines) + "\n"
