"""同梱の見本（docs/adr/0007-bundled-samples.md）。モードごとに 1 冊、最初の起動で普通のデッキとして入れる。

見本の置き場は `samples/<見本>/`（`paths.SAMPLES_DIR`）:

    sample.json   手で書く（id・題・副題・目的・読み手・モード・スタイル・overrides・並び順）
    script.json   台本の正本（手で直すのはここ。直したら `samples/build.py` で作り直す）
    built/        `samples/build.py` が書く（手で直さない）: manifest.json・script.json（描いた台本）・run/（描いた回の控え）

- **入れる**（`import_sample`）: 付属のブランドとスタイルでデッキを作り（タグ「見本」）、描いた台本を版 1 にし、描いた回を 1 つ足して
  控えを写し、ページの絵を資産にする（`store.finish_run`。普通に描いた回と同じ行になる）。触っていない見本のデッキは、同じ道で
  新しい版へ**進める**（同じデッキに台本の版と回を足す）
- **起動のとき**（`sync`）: 一度も入れていない見本を入れ、触っていない古い版のデッキを進める。消された見本は入れ直さない
- **入れ直す**（`restore`。設定 › 見本）: 最新の版が残っていれば何もしない。触っていない古い版は進め、ほかは別のデッキとして足す
- **作る**（`pack`。`samples/build.py` から）: 見せる回の控えのうち要るものだけを写し、記録の中の置き場と id を目印に置き換える。
  置き換えたあとに絶対パスが残っていたら作らない（作った人の機械のパスを配らない）

目印（`{{HOME}}` など）は、JSON は文字列の値の中だけで置き換える（Windows のパスの `\\` でも JSON が壊れない）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import oscompat
from .engines import engine_for
from .enums import DECK_MODES
from .paths import SAMPLES_DIR, DataRoot
from .render import workspace as W
from .scriptdoc import dumps as script_dumps
from .scriptdoc import output_pages, page_id_problems, screen_pages, slides_of
from .store.edits import DECK_ARG_KEYS
from .store.presets import preset_style_for

log = logging.getLogger("quick_slide.samples")

FORMAT = "quick-slide-sample"
FORMAT_VERSION = 1
SAMPLE_TAG = "見本"
SAMPLE_ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
VERSION_RE = re.compile(r"^[0-9a-f]{64}$")
SAMPLE_KEYS = {"id", "order", "mode", "title", "subtitle", "purpose", "audience", "style", "overrides"}
OVERRIDE_KEYS = {"image_look", "style_note"}

# 描いた回の控えのうち、見本に入れるもの（画面・発表・書き出し・直しに要るもの）。PPTX・PDF・サムネイル一覧・検査の画像・
# ログ・検品の文・書き出しの部品・字体のファイルは入れない（ADR 0007）
RUN_PATTERNS = ("slides/slide-*.png", "pictures/*", "deck-info.json", "build.json", "settings/theme.yaml",
                "present/deck.html", "present/assets/*", "present/index.html", "index.html")
TEXT_SUFFIXES = {".json", ".html", ".yaml", ".yml", ".md", ".txt", ".css", ".js", ".svg"}
# 目印。長いパスから順に置き換える（`{{RUN_DIR}}` は `{{DECK_DIR}}` の中、`{{DECK_DIR}}` は `{{HOME}}` の中）
PATH_TOKENS = ("RUN_DIR", "DECK_DIR", "HOME", "CACHE")
ID_TOKENS = ("RUN_ID", "DECK_ID", "BRAND_REVISION_ID", "STYLE_REVISION_ID")
_TOKEN_RE = re.compile(r"\{\{([A-Z_]+)\}\}")
# 置き換えたあとに残っていてはいけないもの（作った人の機械のパス）。作るときに、その機械の値と一緒に探す
LEAK_MARKERS = ("/Users/", "/home/", "/private/var/", "/var/folders/", "C:\\Users", "C:/Users", "file:///")


class SampleError(Exception):
    """見本を読めない・入れられない・作れない（理由は日本語の 1 文）。"""


@dataclass(frozen=True)
class Sample:
    id: str
    order: int
    mode: str
    title: str
    subtitle: str
    purpose: str
    audience: str
    style: str
    overrides: dict[str, Any]
    folder: Path
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def version(self) -> str:
        return str(self.manifest.get("version") or "")

    @property
    def built(self) -> Path:
        return self.folder / "built"


# --- 読む ------------------------------------------------------------------------------------------------

def read_meta(folder: Path) -> dict[str, Any]:
    """`sample.json` を読んで確かめる（作る前にも使う。built/ はまだ無くてよい）。"""
    try:
        meta = json.loads((folder / "sample.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SampleError(f"見本 {folder.name} の sample.json を読めません（{exc.__class__.__name__}）") from None
    if not isinstance(meta, dict):
        raise SampleError(f"見本 {folder.name} の sample.json はオブジェクトにしてください")
    unknown = sorted(set(meta) - SAMPLE_KEYS)
    if unknown:
        raise SampleError(f"見本 {folder.name} の sample.json に知らないキーがあります: {', '.join(unknown)}")
    if meta.get("id") != folder.name or not SAMPLE_ID_RE.match(str(meta.get("id"))):
        raise SampleError(f"見本 {folder.name} の id はフォルダの名前と同じにしてください（英小文字・数字・ハイフン）")
    if meta.get("mode") not in DECK_MODES:
        raise SampleError(f"見本 {folder.name} の mode は {'・'.join(DECK_MODES)} のどれかにしてください")
    engine = engine_for(meta["mode"])
    style = meta.get("style") or engine.default_style
    if style not in engine.styles:
        raise SampleError(f"見本 {folder.name} の style は {'・'.join(engine.styles)} のどれかにしてください")
    for k in ("title",):
        if not isinstance(meta.get(k), str) or not meta[k].strip():
            raise SampleError(f"見本 {folder.name} の {k} は空でない文字列にしてください")
    for k in ("subtitle", "purpose", "audience"):
        if not isinstance(meta.get(k, ""), str):
            raise SampleError(f"見本 {folder.name} の {k} は文字列にしてください")
    overrides = meta.get("overrides") or {}
    if not isinstance(overrides, dict) or set(overrides) - OVERRIDE_KEYS:
        raise SampleError(f"見本 {folder.name} の overrides に書けるのは {'・'.join(sorted(OVERRIDE_KEYS))} だけです")
    order = meta.get("order", 0)
    if isinstance(order, bool) or not isinstance(order, int):
        raise SampleError(f"見本 {folder.name} の order は整数にしてください")
    return {**meta, "style": style, "overrides": overrides, "order": order}


def _sample(folder: Path, meta: dict[str, Any], manifest: dict[str, Any]) -> Sample:
    return Sample(id=meta["id"], order=meta["order"], mode=meta["mode"], title=meta["title"].strip(),
                  subtitle=meta.get("subtitle") or "", purpose=meta.get("purpose") or "",
                  audience=meta.get("audience") or "", style=meta["style"], overrides=dict(meta["overrides"]),
                  folder=folder, manifest=manifest)


def read_sample(folder: Path) -> Sample:
    """見本 1 つ（built/ まで）。中身の検査（ファイルの sha256）は入れる直前に `verify` がする。"""
    meta = read_meta(folder)
    try:
        manifest = json.loads((folder / "built" / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SampleError(f"見本 {folder.name} はまだ作られていません（samples/build.py で作ってください）") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("format_version") != FORMAT_VERSION:
        raise SampleError(f"見本 {folder.name} の形式が違います（{FORMAT} の版 {FORMAT_VERSION} だけを読みます）")
    if manifest.get("id") != meta["id"] or manifest.get("mode") != meta["mode"]:
        raise SampleError(f"見本 {folder.name} の作った中身が sample.json と合いません（作り直してください）")
    if not VERSION_RE.match(str(manifest.get("version"))):
        raise SampleError(f"見本 {folder.name} の版の形が違います")
    if not isinstance(manifest.get("files"), dict) or not isinstance(manifest.get("pages"), list):
        raise SampleError(f"見本 {folder.name} の目録が欠けています")
    return _sample(folder, meta, manifest)


def load_samples(directory: Path = SAMPLES_DIR) -> tuple[list[Sample], list[str]]:
    """同梱の見本（並び順）と、読めなかった理由。見本の置き場が無ければ空。"""
    samples: list[Sample] = []
    problems: list[str] = []
    if not directory.is_dir():
        return samples, problems
    for folder in sorted(p for p in directory.iterdir() if p.is_dir() and (p / "sample.json").is_file()):
        try:
            samples.append(read_sample(folder))
        except SampleError as exc:
            problems.append(str(exc))
    samples.sort(key=lambda s: (s.order, s.id))
    return samples, problems


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_rel(rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts or rel != p.as_posix():
        raise SampleError("見本の目録に置き場の外を指すファイルがあります")
    return p


def verify(sample: Sample) -> dict[str, Any]:
    """入れる前に中身を確かめる: 目録のファイルがすべてあり sha256 が合う・描いた台本の sha256 が合う・ページが台本と合う。
    描いた台本を返す。"""
    m = sample.manifest
    for rel, sha in m["files"].items():
        path = sample.built / "run" / _safe_rel(rel)
        try:
            data = path.read_bytes()
        except OSError:
            raise SampleError(f"見本 {sample.id} のファイル {rel} がありません") from None
        if _sha(data) != sha:
            raise SampleError(f"見本 {sample.id} のファイル {rel} の中身が目録と違います（作り直してください）")
    try:
        raw = (sample.built / "script.json").read_bytes()
        script = json.loads(raw)
    except (OSError, ValueError):
        raise SampleError(f"見本 {sample.id} の描いた台本を読めません") from None
    if _sha(raw) != m.get("script_sha256"):
        raise SampleError(f"見本 {sample.id} の描いた台本が目録と違います（作り直してください）")
    if page_id_problems(slides_of(script)):
        raise SampleError(f"見本 {sample.id} の台本のページの id が欠けています")
    want = [(p["no"], p["key"]) for p in output_pages({"mode": sample.mode}, script)]
    have = [(p.get("no"), p.get("key")) for p in m["pages"]]
    if want != have:
        raise SampleError(f"見本 {sample.id} のページが台本と合いません（作り直してください）")
    links = m.get("links") or {}
    if not isinstance(links, dict) or any(not isinstance(t, str) or t not in m["files"] for t in links.values()):
        raise SampleError(f"見本 {sample.id} の目録の同じ中身のファイルの指し先がありません")
    for rel in links:
        _safe_rel(rel)
    for no, _ in want:
        rel = f"slides/slide-{no:02d}.png"
        if rel not in m["files"] and rel not in links:
            raise SampleError(f"見本 {sample.id} の {no} 枚目の絵がありません")
    return script


# --- 目印 -----------------------------------------------------------------------------------------------

def _walk(value: Any, fn: Callable[[str], Any]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_walk(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: _walk(v, fn) for k, v in value.items()}
    return value


def fill(value: Any, values: dict[str, str | None]) -> Any:
    """JSON の値の中の目印を、入れる機械の値に戻す。値の分からない目印を含む文字列は null にする。"""
    def one(s: str) -> Any:
        if "{{" not in s:
            return s
        names = set(_TOKEN_RE.findall(s))
        if any(values.get(n) is None for n in names):
            return None
        return _TOKEN_RE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), s)
    return _walk(value, one)


def fill_text(text: str, values: dict[str, str | None], *, escape: bool) -> str:
    """JSON でない文字のファイル（HTML・YAML）の目印を戻す。`escape` は YAML の二重引用符の中に書く形（JSON と同じ）。"""
    def one(m: re.Match[str]) -> str:
        v = values.get(m.group(1))
        if v is None:
            return "" if m.group(1) in values else m.group(0)
        return json.dumps(v, ensure_ascii=False)[1:-1] if escape else v
    return _TOKEN_RE.sub(one, text)


def _variants(path: str) -> list[str]:
    out = [path]
    try:
        real = os.path.realpath(path)
    except OSError:
        real = path
    if real not in out:
        out.append(real)
    if real.startswith("/private/") and real[len("/private"):] not in out:
        out.append(real[len("/private"):])   # mac の /private/var と /var は同じ
    return out


def tokenizer(paths: dict[str, list[str]], ids: dict[str, str]) -> Callable[[str], str]:
    """作るとき: 文字列の中の置き場と id を目印に置き換える関数。`paths` は目印 → その機械での置き場（いくつでも）。"""
    pairs: list[tuple[str, str]] = []
    for name, roots in paths.items():
        for r in roots:
            for v in _variants(str(r)):
                pairs.append((v, "{{" + name + "}}"))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    id_pairs = [(v, "{{" + k + "}}") for k, v in ids.items() if v]

    def one(s: str) -> str:
        for old, new in pairs:
            if old in s:
                s = s.replace(old, new)
        for old, new in id_pairs:
            if old in s:
                s = s.replace(old, new)
        return s
    return one


def leaks(data: bytes, markers: list[str]) -> list[str]:
    """残っていてはいけないもの（作った人の機械のパス）が入っていれば、その印。"""
    return [m for m in markers if m and m.encode("utf-8") in data]


# --- 入れる ---------------------------------------------------------------------------------------------

def _values(store: Any, root: DataRoot, sample: Sample, deck: dict[str, Any], run_id: str,
            extra_env: dict[str, str] | None) -> dict[str, str | None]:
    from .render.caches import engine_cache_base

    cache = engine_cache_base(root, engine_for(sample.mode).skill, deck["id"], extra_env)
    return {"HOME": str(root.root), "DECK_DIR": str(root.deck_dir(deck["id"])), "RUN_DIR": str(root.run_dir(deck["id"], run_id)),
            "CACHE": str(cache) if cache is not None else None, "RUN_ID": run_id, "DECK_ID": deck["id"],
            "BRAND_REVISION_ID": deck["brand_revision_id"], "STYLE_REVISION_ID": deck["style_revision_id"]}


def _place_files(sample: Sample, run_dir: Path, values: dict[str, str | None]) -> None:
    """控えを描いた回のフォルダへ写す（写す。リポジトリのファイルに張らない）。文字のファイルは目印を戻す。"""
    src_root = sample.built / "run"
    for rel in sorted(sample.manifest["files"]):
        src, dst = src_root / _safe_rel(rel), run_dir / _safe_rel(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix.lower() == ".json":
            data = fill(json.loads(src.read_text(encoding="utf-8")), values)
            dst.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        elif src.suffix.lower() in TEXT_SUFFIXES:
            text = src.read_text(encoding="utf-8")
            dst.write_text(fill_text(text, values, escape=src.suffix.lower() in (".yaml", ".yml")), encoding="utf-8")
        else:
            shutil.copyfile(src, dst)
    for rel, same in (sample.manifest.get("links") or {}).items():   # 同じ中身のファイルは目録に 1 つだけ
        dst = run_dir / _safe_rel(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(run_dir / _safe_rel(same), dst)


def _link_present_fonts(run_dir: Path) -> None:
    """発表の画面の字体: deck.html が読む字体ファイルが入れる機械にあれば、回の控えへ張る（無ければ OS の字体で出る）。"""
    html_path = run_dir / W.PRESENT / "deck.html"
    if not html_path.is_file():
        return
    from urllib.parse import unquote

    for m in W._FONT_URL.finditer(html_path.read_text(encoding="utf-8")):
        path = unquote(m.group(1))
        font = Path(path)
        if font.suffix.lower() in W.PRESENT_FONT_TYPES and font.is_file():
            try:
                W._link(font, run_dir / W.PRESENT / "fonts" / W.present_font_name(path))
            except OSError:
                continue


def import_sample(store: Any, root: DataRoot, sample: Sample, *, extra_env: dict[str, str] | None = None,
                  into: str | None = None) -> dict[str, Any]:
    """見本を入れる。`into` が無ければ新しいデッキ、あればそのデッキ（触っていない見本のデッキ）を新しい版へ進める。
    戻り値はデッキの行。呼ぶ側がサーバの錠を持つ。"""
    script = verify(sample)
    m = sample.manifest
    made: list[Path] = []
    try:
        with store.transaction():
            if into is None:
                style_rev = store.preset_style_revision_id(preset_style_for(sample.mode, sample.style))
                deck = store.create_deck(sample.title, mode=sample.mode, brand_revision_id=store.preset_brand_revision_id(),
                                         style_revision_id=style_rev, subtitle=sample.subtitle, purpose=sample.purpose,
                                         audience=sample.audience)
                made.append(root.deck_dir(deck["id"]))
                store.add_tag(deck["id"], SAMPLE_TAG)
            else:
                deck = store.deck(into)
                if deck["mode"] != sample.mode:
                    raise SampleError("モードの違うデッキは進められません")
            deck_id = deck["id"]
            saved = store.save_script(deck_id, script, written_by="screen", writer_backend=None, intake_id=None,
                                      run_id=None)
            made.append(store.script_path(saved))
            store.set_script_check(saved["id"], 0, [])
            run = store.create_run(deck_id, args={"mode": sample.mode})
            run_dir = root.run_dir(deck_id, run["id"])
            made += [run_dir, root.assets_dir / "decks" / deck_id / run["id"]]
            values = _values(store, root, sample, deck, run["id"], extra_env)
            args = {k: v for k, v in (fill(m.get("args") or {}, values) or {}).items() if k in (*DECK_ARG_KEYS, "look")}
            args["mode"] = sample.mode
            store.update_run_args(run["id"], args)
            store.accept_script(saved["id"], screen_pages(args, script))
            _place_files(sample, run_dir, values)
            pages = output_pages(args, script)
            store.start_rendering(run["id"], saved["id"], pages,
                                  {"phase": "rendering", "step": "sample", "done": len(pages), "total": len(pages)})
            info = {int(p["no"]): p for p in m["pages"]}
            store.finish_run(run["id"], state="done", rc=0, message=None, failure_kind=None, pages={
                p["no"]: {"file": str(run_dir / "slides" / f"slide-{p['no']:02d}.png"),
                          "warnings": list(info[p["no"]].get("warnings") or []),
                          "picture_sha": info[p["no"]].get("picture_sha256")} for p in pages})
            store.set_run_record(run["id"], {"sample": {"id": sample.id, "version": sample.version}})
            store.record_sample_deck(deck_id, sample_id=sample.id, version=sample.version, script_id=saved["id"],
                                     run_id=run["id"])
            store.mark_sample_seen(sample.id)
            store.append_event("sample_imported", {"sample_id": sample.id, "version": sample.version,
                                                   "run_id": run["id"], "advanced": into is not None,
                                                   "pages": len(pages)}, deck_id=deck_id)
    except BaseException:
        for p in made:                               # 行が戻ったので、写したファイルも片付ける
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        raise
    # 描いた絵をデッキのキャッシュの区画へ（できなければ次に描くとき描き直すだけ）。発表の画面の字体
    if sample.mode == "presentation":
        from .render.caches import forget_duplicate_cache, seed_duplicate

        try:
            seed_duplicate(root, deck_id, extra_env)
        except Exception:  # noqa: BLE001
            try:
                forget_duplicate_cache(root, deck_id)
            except Exception:  # noqa: BLE001
                pass
        _link_present_fonts(run_dir)
    return store.deck(deck_id)


def sync(store: Any, root: DataRoot, *, directory: Path = SAMPLES_DIR,
         extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    """起動のとき: 一度も入れていない見本を入れ、触っていない古い版の見本のデッキを進める。消された見本は入れ直さない。
    1 つの見本が入れられなくても、ほかの見本は続ける（理由は `problems`）。"""
    samples, problems = load_samples(directory)
    out: dict[str, Any] = {"imported": [], "advanced": [], "problems": problems}
    for s in reversed(samples):      # 先頭の見本が一覧のいちばん上に来るように、後ろから入れる
        try:
            if not store.sample_seen(s.id):
                out["imported"].append(import_sample(store, root, s, extra_env=extra_env)["id"])
                continue
            for row in store.sample_decks(s.id):
                if row["untouched"] and row["version"] != s.version:
                    out["advanced"].append(import_sample(store, root, s, extra_env=extra_env, into=row["deck_id"])["id"])
        except Exception as exc:  # noqa: BLE001 - 見本のせいで起動を止めない
            log.warning("見本 %s を入れられませんでした: %s", s.id, exc)
            out["problems"].append(str(exc) if isinstance(exc, SampleError) else f"見本 {s.id} を入れられませんでした")
    return out


def restore(store: Any, root: DataRoot, *, directory: Path = SAMPLES_DIR,
            extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    """設定 › 見本 の「入れ直す」: 最新の版が残っていれば何もしない。触っていない古い版は進め、ほかは別のデッキとして足す。"""
    samples, problems = load_samples(directory)
    out: dict[str, Any] = {"imported": [], "advanced": [], "kept": [], "problems": problems}
    for s in reversed(samples):
        try:
            live = [r for r in store.sample_decks(s.id) if r["live"]]
            if any(r["version"] == s.version for r in live):
                out["kept"].append(s.id)
                continue
            fresh = next((r for r in live if r["untouched"]), None)
            if fresh is not None:
                out["advanced"].append(import_sample(store, root, s, extra_env=extra_env, into=fresh["deck_id"])["id"])
            else:
                out["imported"].append(import_sample(store, root, s, extra_env=extra_env)["id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("見本 %s を入れられませんでした: %s", s.id, exc)
            out["problems"].append(str(exc) if isinstance(exc, SampleError) else f"見本 {s.id} を入れられませんでした")
    return out


def status(store: Any, *, directory: Path = SAMPLES_DIR) -> dict[str, Any]:
    """設定 › 見本 に出す一覧: 見本ごとに、いまのデッキ（ごみ箱でない）と、最新の版が入っているか。"""
    samples, problems = load_samples(directory)
    items = []
    for s in samples:
        decks = [{"id": r["deck_id"], "title": r["title"], "current": r["version"] == s.version,
                  "archived": r["archived_at"] is not None}
                 for r in store.sample_decks(s.id) if r["live"]]
        state = "present" if any(d["current"] for d in decks) else ("outdated" if decks else "missing")
        items.append({"id": s.id, "title": s.title, "subtitle": s.subtitle, "mode": s.mode,
                      "pages": len(s.manifest.get("pages") or []), "state": state, "decks": decks})
    return {"items": items, "problems": problems}


# --- 作る（samples/build.py から） --------------------------------------------------------------------------

def run_files(run_dir: Path) -> list[str]:
    """描いた回の控えのうち、見本に入れるファイル（`RUN_PATTERNS`。書きかけの .tmp は入れない）。"""
    out: set[str] = set()
    for pattern in RUN_PATTERNS:
        for p in run_dir.glob(pattern):
            if p.is_file() and not p.is_symlink() and not p.name.endswith(".tmp"):
                out.add(p.relative_to(run_dir).as_posix())
    return sorted(out)


def pack(store: Any, root: DataRoot, deck_id: str, folder: Path, *, cache_roots: list[Path],
         extra_markers: list[str] = ()) -> dict[str, Any]:
    """デッキの見せる回から `<folder>/built/` を作り直す（書き終えてから入れ替える）。目録を返す。

    記録の中の置き場（データの置き場・デッキ・回・キャッシュの根っこ）と id（デッキ・回・ブランドとスタイルの改訂）を目印にし、
    そのあとに作った人の機械のパス（`LEAK_MARKERS`・`extra_markers`）が 1 つでも残っていれば `SampleError`。"""
    meta = read_meta(folder)
    deck = store.deck(deck_id)
    run = store.shown_run(deck_id)
    head = store.head_script(deck_id)
    if run is None or head is None or run["script_id"] != head["id"]:
        raise SampleError("見本のデッキが、いまの台本で描き終わっていません")
    pages_rows = store.run_pages(run["id"])
    if not pages_rows or any(p["state"] != "ready" for p in pages_rows):
        raise SampleError("描けなかったページがあります")
    run_dir = root.run_dir(deck_id, run["id"])
    convert = tokenizer(
        {"RUN_DIR": [str(run_dir)], "DECK_DIR": [str(root.deck_dir(deck_id))], "HOME": [str(root.root)],
         "CACHE": [str(c) for c in cache_roots]},
        {"RUN_ID": run["id"], "DECK_ID": deck_id, "BRAND_REVISION_ID": deck["brand_revision_id"],
         "STYLE_REVISION_ID": deck["style_revision_id"]})
    markers = [*LEAK_MARKERS, *extra_markers, str(root.root), str(Path.home()), deck_id, run["id"]]
    script_raw = script_dumps(store.load_script(head))

    built = folder / "built"
    stage = Path(tempfile.mkdtemp(prefix=".built-", dir=folder))
    try:
        files: dict[str, str] = {}
        links: dict[str, str] = {}
        by_sha: dict[str, str] = {}
        problems: list[str] = []
        for rel in run_files(run_dir):
            src = run_dir / rel
            suffix = src.suffix.lower()
            if suffix == ".json":
                data = (json.dumps(_walk(json.loads(src.read_text(encoding="utf-8")), convert), ensure_ascii=False,
                                   indent=1) + "\n").encode("utf-8")
            elif suffix in TEXT_SUFFIXES:
                # 発表の画面の字体（deck.html の file:// の URL）も、データの置き場の fonts/ の下なら目印になる
                # （入れる機械の同じ所を引く）。外にある字体は残ったパスとして断る
                data = convert(src.read_text(encoding="utf-8")).encode("utf-8")
                if suffix in (".yaml", ".yml"):   # 二重引用符の中に JSON の書き方で入った置き場も
                    data = _tokenize_escaped(data.decode("utf-8"), convert).encode("utf-8")
            else:
                data = src.read_bytes()
            hit = leaks(data, markers)
            if hit:
                problems.append(f"{rel}: {', '.join(hit)}")
                continue
            sha = _sha(data)
            if sha in by_sha and suffix not in TEXT_SUFFIXES:
                links[rel] = by_sha[sha]
                continue
            by_sha.setdefault(sha, rel)
            dst = stage / "run" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            files[rel] = sha
        if problems:
            raise SampleError("作った機械のパスなどが残っています（配れません）: " + " / ".join(problems))
        args = _walk({k: v for k, v in run["args"].items() if k in (*DECK_ARG_KEYS, "look") and v is not None}, convert)
        args_text = json.dumps(args, ensure_ascii=False)
        hit = leaks(args_text.encode("utf-8"), markers)
        if hit:
            raise SampleError("描いた回の引数に作った機械のパスなどが残っています: " + ", ".join(hit))
        pages = [{"no": p["page_no"], "key": p["key"], "warnings": p["warnings"], "picture_sha256": p["picture_sha256"]}
                 for p in pages_rows]
        (stage / "script.json").write_bytes(script_raw)
        body = {"format": FORMAT, "format_version": FORMAT_VERSION, "id": meta["id"], "mode": meta["mode"],
                "engine": engine_for(meta["mode"]).skill, "script_sha256": _sha(script_raw), "args": args,
                "pages": pages, "files": files, "links": links}
        body["version"] = _sha(json.dumps({**body, "meta": meta}, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        (stage / "manifest.json").write_text(json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                             encoding="utf-8")
        old = folder / ".built-old"
        shutil.rmtree(old, ignore_errors=True)
        if built.exists():
            oscompat.replace(built, old)
        oscompat.replace(stage, built)
        shutil.rmtree(old, ignore_errors=True)
        return body
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _tokenize_escaped(text: str, convert: Callable[[str], str]) -> str:
    """YAML の二重引用符の中の文字列（JSON の書き方）を、ほどいて目印にしてから書き戻す。"""
    def one(m: re.Match[str]) -> str:
        try:
            raw = json.loads(m.group(0))
        except ValueError:
            return m.group(0)
        return json.dumps(convert(raw), ensure_ascii=False)
    return re.sub(r'"(?:[^"\\]|\\.)*"', one, text)
