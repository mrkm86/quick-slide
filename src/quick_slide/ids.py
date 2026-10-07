"""ID。アプリ発行の UUID v4 文字列。プリセットの固定 UUID もここに集める。"""

from __future__ import annotations

import uuid

# 製品同梱プリセットの固定 UUID。変更しない。
PRESET_BRAND_ID = "5a1de57d-0000-4b00-8000-000000000001"  # 既定のブランド（エンジンの既定の色）
PRESET_STYLE_STANDARD_ID = "5a1de57d-0000-4b00-8000-000000000101"  # 説明・報告（standard）
PRESET_STYLE_VISUAL_ID = "5a1de57d-0000-4b00-8000-000000000102"  # ビジュアル（visual）
PRESET_STYLE_INFOGRAPHIC_ID = "5a1de57d-0000-4b00-8000-000000000103"  # インフォグラフィック（ピッチ）


def new_id() -> str:
    return str(uuid.uuid4())


def is_uuid(text: object) -> bool:
    if not isinstance(text, str):
        return False
    try:
        return str(uuid.UUID(text)) == text.lower()
    except (ValueError, AttributeError, TypeError):
        return False
