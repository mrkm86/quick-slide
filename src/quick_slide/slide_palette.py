"""ブランドの配色 → プレゼン（html-slide）が実際に塗る色の役。設定画面の見本が使う。

色の役の作り方の正本はエンジンの `skills/html-slide/scripts/deck_palette.py`（DADS のキーカラーの作り方・ADR 0020）。
ここは写しを持たず、そのファイルを読み込んで呼ぶだけ。ブランドからエンジンのテーマへの写しは `palette.engine_theme`
（描く回と同じ道）を通すので、見本の色と描いた資料の色は同じになる。

import の仕組みを通さずに読む（エンジンのフォルダに `__pycache__` を書かない。配る物・リンク先のため。`render/caches._places` と同じ）。
"""
from __future__ import annotations

import sys
import threading
import types
from typing import Any

from . import palette
from .paths import SKILLS_DIR

DECK_PALETTE = SKILLS_DIR / "html-slide" / "scripts" / "deck_palette.py"
KEYCOLORS = SKILLS_DIR / "dads" / "scripts" / "keycolors.py"

# 「他の要素の色」で人が変えられる色: ブランドの colors のキー → エンジンの色の役
EDITABLE = {"base": "bg", "surface": "surface", "ink": "ink", "muted": "muted"}

# 画面に渡す役（見本が塗るものと、色の一覧に出すもの）
ROLES = ("bg", "ink", "muted", "faint", "surface", "rule", "rule-weak", "bar",
         "primary", "primary-mark", "primary-decor", "primary-strong", "on-primary", "primary-soft",
         "accent", "accent-mark", "accent-decor", "accent-strong", "on-accent", "accent-soft",
         "success-1", "success-2", "error-1", "error-2", "warning-orange-1", "warning-orange-2")

_lock = threading.Lock()
_module: types.ModuleType | None = None


def _load(path, name: str, namespace: dict[str, Any] | None = None) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    if namespace:
        mod.__dict__.update(namespace)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)  # noqa: S102 - 同梱のエンジン
    return mod


def _engine() -> types.ModuleType:
    """deck_palette を 1 回だけ読む。deck_palette の `import keycolors` には、読んだ keycolors をその間だけ見せる。"""
    global _module
    with _lock:
        if _module is None:
            keycolors = _load(KEYCOLORS, "quick_slide_engine_keycolors")
            before = sys.modules.get("keycolors")
            sys.modules["keycolors"] = keycolors
            try:
                _module = _load(DECK_PALETTE, "quick_slide_engine_deck_palette")
            finally:
                if before is None:
                    sys.modules.pop("keycolors", None)
                else:
                    sys.modules["keycolors"] = before
        return _module


def slide_colors(colors: dict[str, Any]) -> dict[str, Any]:
    """ブランドの `colors`（編集画面が送る形）→ `{"theme", "roles", "adjusted", "problems", "error", "overrides"}`。

    - theme: エンジンに渡すテーマ（`palette.engine_theme`）
    - roles: エンジンが塗る色の役（`ROLES`。読めない組み合わせなら null）
    - adjusted: ブランドの色を 4.5:1 に届かせるため明度を動かした役（[{role, input, used, contrast}]）
    - problems: エンジンが描かずに止まる組み合わせ（`contrast_problems`）
    - error: 色の役を作れないときの理由（エンジンも描けない）
    - overrides: 人が変えた色（`EDITABLE` のキー）ごとに {value, role, applied, reason}。カード・表の背景（面の色）は本文の文字が読みにくく
      なるときはエンジンに渡さない（`palette.engine_theme`）ので、applied=false と理由を返す
    """
    theme = palette.engine_theme(palette.snapshot_colors(colors))
    lower = {key: value.lower() for key, value in theme.items()}
    engine = _engine()
    out: dict[str, Any] = {"theme": theme, "roles": None, "adjusted": [], "problems": [], "error": None, "overrides": {}}
    for key, role in EDITABLE.items():
        value = colors.get(key)
        if not isinstance(value, str):
            continue
        applied = key != "surface" or "surface" in theme
        out["overrides"][key] = {"value": value.upper(), "role": role, "applied": applied,
                                 "reason": None if applied else "本文の文字が読みにくくなるため、この色は使っていません。"}
    try:
        roles = engine.base_palette(lower)
    except ValueError as exc:
        out["error"] = str(exc)
        return out
    out["roles"] = {role: roles[role] for role in ROLES}
    out["problems"] = engine.contrast_problems(roles)
    for role in ("primary", "accent"):
        if role not in lower:          # 強調色が基本色と同じなら、エンジンは主役の色の役をそのまま使う
            continue
        given, used = lower[role], roles[role]
        if used != given:
            out["adjusted"].append({"role": role, "input": given, "used": used,
                                    "contrast": round(engine.ratio(given, roles["bg"]), 2)})
    return out
