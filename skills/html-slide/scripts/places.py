"""置き場（設定・出力・描いた絵のキャッシュ）。image-slide の ADR 0016・html-slide の ADR 0015。

根っこは Quick Slide のデータの置き場で、ここ1か所だけで決める（環境変数 QUICK_SLIDE_HOME・既定 ~/.quick-slide）。
エンジンごとの設定と、--out を省いたときの出力は、その下の engines/<スキル>/ に置く。
描いた絵のキャッシュは OS のキャッシュの置き場（macOS は ~/Library/Caches/quick-slide/<スキル>）。
どれも環境変数（<スキル>_CONFIG・<スキル>_CACHE。例: IMAGE_SLIDE_CONFIG）か設定（out・cache_dir）で変えられる。
html-slide と同じファイル（テストがバイト単位で見比べる）。
"""
from __future__ import annotations

import os
import pathlib
import sys

APP = "quick-slide"


def _env(skill: str, what: str) -> str | None:
    return os.environ.get(skill.upper().replace("-", "_") + "_" + what) or None


def home() -> pathlib.Path:
    """Quick Slide のデータの置き場。"""
    return pathlib.Path(os.environ.get("QUICK_SLIDE_HOME") or pathlib.Path.home() / f".{APP}").expanduser()


def config_path(skill: str) -> pathlib.Path:
    return pathlib.Path(_env(skill, "CONFIG") or home() / "engines" / skill / "config.yaml").expanduser()


def load_config(skill: str) -> dict:
    path = config_path(skill)
    if not path.is_file():
        return {}
    import yaml
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def out_root(skill: str, cfg: dict) -> pathlib.Path:
    """--out を省いたときの出力の根っこ。"""
    return pathlib.Path(str(cfg.get("out") or home() / "engines" / skill / "output")).expanduser()


def cache_root(skill: str, cfg: dict) -> pathlib.Path:
    env = _env(skill, "CACHE")
    if env:
        return pathlib.Path(env).expanduser()
    if cfg.get("cache_dir"):
        return pathlib.Path(str(cfg["cache_dir"])).expanduser()
    if sys.platform == "darwin":
        return pathlib.Path.home() / "Library" / "Caches" / APP / skill
    return pathlib.Path(os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache").expanduser() / APP / skill
