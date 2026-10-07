"""デッキが使ったエンジンのキャッシュの区画（P5: ごみ箱から完全に削除するときに一緒に消す）。

エンジンは描いた絵をキャッシュの置き場に「持ち主のフォルダ」ごとの区画で置く（`skills/*/scripts/cache.py`）。
持ち主は image-slide が `--out`（デッキの `work/`。P2 の回は `runs/<回>/`）、html-slide が台本のフォルダ（`scripts/`）。
区画には印 `owner.json`（持ち主の絶対パス）がある。

消してよい区画 = キャッシュの根っこの直下にあり、印の持ち主が**そのデッキの置き場（`decks/<デッキ>/`）の中**のもの。
根っこは 2 つから集める:

1. エンジンの記録（`deck-info.json`・`build.json`・`layout.json`・`pictures/index.json`）の `cache`（区画）の親
2. エンジンの置き場の決まり（`places.py` の `cache_root`。環境変数 → 設定の `cache_dir` → OS の既定）

ごみ箱へ移すときは消さない（戻せるように）。エンジン自身も上限を超えれば古い区画から消すので、消えていても困らない。
"""

from __future__ import annotations

import json
import os
import shutil
import types
from pathlib import Path
from typing import Any

from ..paths import SKILLS_DIR, DataRoot

ENGINE_SKILLS = ("html-slide", "image-slide")
STAMP = "owner.json"
RECORDS = ("deck-info.json", "build.json", "layout.json")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _places(skill: str):
    """エンジンの `places.py`（置き場の決まりの正本）を読み込む。読めなければ None。

    import の仕組みを通さずに読む（エンジンのフォルダに `__pycache__` を書かない。配る物・リンク先のため）。"""
    path = SKILLS_DIR / skill / "scripts" / "places.py"
    try:
        mod = types.ModuleType(f"quick_slide_engine_places_{skill.replace('-', '_')}")
        mod.__file__ = str(path)
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)  # noqa: S102 - 同梱のエンジン
        return mod
    except Exception:  # noqa: BLE001 - 読めなければ記録からだけ探す
        return None


def engine_config(root: DataRoot, skill: str) -> dict[str, Any]:
    """エンジンの設定（このサーバの置き場の `engines/<スキル>/config.yaml`。環境変数 `<スキル>_CONFIG` が先）。"""
    env = os.environ.get(skill.upper().replace("-", "_") + "_CONFIG")
    path = Path(env).expanduser() if env else root.root / "engines" / skill / "config.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml  # noqa: PLC0415 - エンジンの依存（無ければ既定の根っこだけ）

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def cache_roots(root: DataRoot, deck_id: str) -> list[Path]:
    """そのデッキの区画がありうるキャッシュの根っこ。"""
    found: list[Path] = []
    deck_dir = root.deck_dir(deck_id)
    records: list[Path] = []
    for folder in [deck_dir / "work", *sorted((deck_dir / "runs").glob("*"))]:
        records += [folder / name for name in RECORDS]
        records.append(folder / "pictures" / "index.json")
    for rec in records:
        cache = _read_json(rec).get("cache")
        if isinstance(cache, str) and cache:
            found.append(Path(cache).expanduser().parent)
    for skill in ENGINE_SKILLS:
        places = _places(skill)
        if places is None:
            continue
        try:
            found.append(Path(places.cache_root(skill, engine_config(root, skill))))
        except Exception:  # noqa: BLE001
            continue
    out: list[Path] = []
    for p in found:
        try:
            r = p.resolve()
        except OSError:
            continue
        if r not in out:
            out.append(r)
    return out


def deck_cache_areas(root: DataRoot, deck_id: str) -> list[Path]:
    """そのデッキが持ち主の区画（根っこの直下・印の持ち主がデッキの置き場の中）。"""
    deck_dir = root.deck_dir(deck_id).resolve()
    areas: list[Path] = []
    for base in cache_roots(root, deck_id):
        if not base.is_dir():
            continue
        try:
            children = sorted(base.iterdir())
        except OSError:
            continue
        for d in children:
            if d.is_symlink() or not d.is_dir():
                continue
            owner = _read_json(d / STAMP).get("owner")
            if not isinstance(owner, str) or not owner:
                continue
            try:
                if Path(owner).expanduser().resolve().is_relative_to(deck_dir) and d not in areas:
                    areas.append(d)
            except (OSError, ValueError):
                continue
    return areas


def remove_areas(areas: list[Path]) -> list[str]:
    """区画を消す。消せなかったものの例外の名前を返す。"""
    failed: list[str] = []
    for d in areas:
        try:
            shutil.rmtree(d)
        except FileNotFoundError:
            pass
        except OSError as exc:
            failed.append(exc.__class__.__name__)
    return failed
