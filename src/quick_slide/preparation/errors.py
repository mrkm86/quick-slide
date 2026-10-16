"""素材準備が止まるときの理由。`message` は画面と会話にそのまま出す日本語、`code` は機械が見る短い名前。"""

from __future__ import annotations

from typing import Any


class PreparationStopped(Exception):
    def __init__(self, message: str, code: str = "stopped", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class EngineInputUnsupported(PreparationStopped):
    """確定台本をエンジンの入力にできない（まだ対応していないモード・役割）。"""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "engine_input_unsupported", details)


SKILL_UNSUPPORTED_MESSAGE = ("リソース「{name}」はスキルのフォルダ（quick-slide-read.json か SKILL.md がある）です。"
                             "スキルの読み取りはまだ使えないため、素材の準備を止めました。"
                             "通常のフォルダ（README.md か README.txt に使い方を書く）に置き換えるか、対応した版を待ってください。")
