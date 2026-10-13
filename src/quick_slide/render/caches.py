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
import time
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


def engine_config(root: DataRoot, skill: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    """エンジンの設定（このサーバの置き場の `engines/<スキル>/config.yaml`。環境変数 `<スキル>_CONFIG` が先）。
    `env` を渡せばその環境で引く（エンジンに渡す子の環境。既定は今の環境）。"""
    env = (os.environ if env is None else env).get(skill.upper().replace("-", "_") + "_CONFIG")
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


def engine_cache_base(root: DataRoot, skill: str, deck_id: str, extra_env: dict[str, str] | None = None) -> Path | None:
    """**エンジンに渡す子の環境**での、そのエンジンの描いた絵のキャッシュの根っこ（置き場の決まり: 環境変数
    `<スキル>_CACHE` → 設定の `cache_dir` → 既定）。分からなければ None。

    `extra_env` はワーカーが子に足す環境（`Tools.extra_env`）。相対の指定は、エンジンが動くフォルダ（デッキの `work/`）から。"""
    from ..childenv import child_env

    env = child_env(home=root.root, extra=extra_env)
    base: Path | None = None
    try:
        var = skill.upper().replace("-", "_") + "_CACHE"
        if env.get(var):
            base = Path(env[var]).expanduser()
        else:
            cfg = engine_config(root, skill, env)
            if cfg.get("cache_dir"):
                base = Path(str(cfg["cache_dir"])).expanduser()
            else:
                places = _places(skill)
                if places is not None:
                    base = Path(places.cache_root(skill, {})).expanduser()
    except Exception:  # noqa: BLE001
        base = None
    if base is not None and not base.is_absolute():
        base = root.work_dir(deck_id) / base     # エンジンは work/ で動く（`render/runner.py`）
    return base


def seed_duplicate(root: DataRoot, deck_id: str, extra_env: dict[str, str] | None = None) -> int:
    """複製したデッキ（`store.duplicate_deck`）のプレゼンの描いた絵を、そのデッキの区画へ引き継ぐ。張った枚数。

    html-slide は生成の枠の絵を台本のフォルダごとの区画（`gen-<鍵>.png`）から引くので、複製の台本のフォルダは
    空の区画になり、次に描くとき全部の絵を Codex で描き直してしまう。写した回の控え（`pictures/gen-*`）を、
    複製の台本のフォルダの区画へ張っておく（鍵は描く指示から作るので、中身が同じなら同じ鍵でそのまま使われる）。

    1. 先に、写した回の記録（`deck-info.json`・`pictures/index.json`）の `cache` を複製の区画に書き換える（置き換えで
       書く。元の回とファイルを共有しない）。版から戻すとき控えの絵をこの `cache` へ戻すので、元のデッキの区画を
       指したままだと元のデッキのキャッシュを書き換えてしまう。ここが済めば、2 が失敗しても描き直しになるだけ
    2. 区画を作って絵を張る（添えた JSON は写す。エンジンが JSON をその場で書き換えるため）。失敗したら、書きかけの
       一時ファイルを消して例外を上げる（張れた絵はそのまま使われる）

    区画の根っこは、**エンジンに渡す子の環境**での置き場の決まり（`HTML_SLIDE_CACHE` → 設定の `cache_dir` → 既定）。
    `extra_env` はワーカーが子に足す環境（`Tools.extra_env`）。相対の指定は、エンジンが動くフォルダ（`work/`）から。
    ピッチは描くときに控えから区画へ並べ直す（`worker/pipeline._pitch_fallback`）ので、ここでは何もしない。
    """
    from .workspace import _copy, _link, pitch_area_for, write_json

    owner = root.scripts_dir(deck_id).resolve()
    jobs: list[tuple[Path, Path]] = []
    for run_dir in sorted((root.deck_dir(deck_id) / "runs").glob("*")):
        info_path = run_dir / "deck-info.json"
        cache = _read_json(info_path).get("cache")
        if isinstance(cache, str) and cache:
            jobs.append((run_dir, Path(cache)))
    if not jobs:
        return 0
    base = engine_cache_base(root, "html-slide", deck_id, extra_env)
    area = pitch_area_for(owner, (base / "x") if base is not None else jobs[0][1])   # エンジンの cache.area_path と同じ名前
    for run_dir, _ in jobs:                      # 1. 記録を先に
        for rec in (run_dir / "deck-info.json", run_dir / "pictures" / "index.json"):
            data = _read_json(rec)
            if data.get("cache"):
                data["cache"] = str(area)
                write_json(rec, data)
    n = 0
    try:                                         # 2. 絵を張る
        area.mkdir(parents=True, exist_ok=True)
        (area / STAMP).write_text(json.dumps({"owner": str(owner), "used": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                             ensure_ascii=False) + "\n", encoding="utf-8")
        for run_dir, _ in jobs:
            for f in sorted((run_dir / "pictures").glob("gen-*")):
                if f.name.endswith(".tmp") or (area / f.name).exists():
                    continue
                (_copy if f.suffix == ".json" else _link)(f, area / f.name)
                n += f.suffix == ".png"
    except BaseException:
        for tmp in area.glob("*.tmp") if area.is_dir() else []:
            tmp.unlink(missing_ok=True)
        raise
    return n


def forget_duplicate_cache(root: DataRoot, deck_id: str) -> None:
    """引き継ぎ（`seed_duplicate`）が記録を書き換える前に止まったときの守り: 写した回の記録から `cache` を外す
    （元のデッキの区画を指したまま残さない。版から戻すときは区画が分からないので絵を戻さず、描き直すだけになる）。"""
    from .workspace import write_json

    for run_dir in sorted((root.deck_dir(deck_id) / "runs").glob("*")):
        for rec in (run_dir / "deck-info.json", run_dir / "pictures" / "index.json"):
            data = _read_json(rec)
            if not data.get("cache"):
                continue
            owner = _read_json(Path(str(data["cache"])) / STAMP).get("owner")
            if owner != str(root.scripts_dir(deck_id).resolve()):   # このデッキの区画でなければ外す
                data.pop("cache")
                write_json(rec, data)
