"""原稿に「既知の秘密らしい形」が含まれるかの小集合検査。

一致したら入口で拒否する（終了コード 2）。原稿は長期保存されるので止められるのはここだけ。
検出結果には一致した文字列そのものを含めず、パターン名だけを返す。集合は設定で追加できる。
誤検知時の案内文は未決。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key_block": re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "aws_access_key_id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    "anthropic_style_key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
}


def find_secret_like(text: str, extra_patterns: Iterable[str] = ()) -> list[str]:
    hits: list[str] = []
    for name, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            hits.append(name)
    for idx, raw in enumerate(extra_patterns):
        try:
            if re.search(raw, text):
                hits.append(f"extra_{idx}")
        except re.error:
            continue
    return hits
