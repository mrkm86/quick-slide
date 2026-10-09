"""テストの共通: scripts/ を import できるようにし、tokens/ などの場所を渡す（標準ライブラリだけ）。"""
import pathlib
import re
import sys

DADS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DADS / "scripts"))


def dads_css_vars() -> dict[str, str]:
    """tokens/dads.css の変数 → 値（var(--x) は解決する）。"""
    text = (DADS / "tokens" / "dads.css").read_text(encoding="utf-8")
    raw = dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", text))
    out = {}
    for k, v in raw.items():
        m = re.fullmatch(r"var\((--[a-z0-9-]+)\)", v.strip())
        out[k] = raw[m.group(1)].strip() if m else v.strip()
    return out
