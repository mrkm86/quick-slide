#!/usr/bin/env python3
"""同梱の見本を作り直す（docs/adr/0006-bundled-samples.md）。`samples/<見本>/script.json` を直したら、これで `built/` を作る。

    python3 samples/build.py                 # 全部の見本
    python3 samples/build.py pitch           # 指定した見本だけ
    python3 samples/build.py --fresh pitch   # 前に作った絵を使わずに描き直す（ピッチは全ページで Codex の枠を使う）
    python3 samples/build.py --keep          # 一時的なデータの置き場を消さずに残す（確かめる用）

流れ（見本ごと）:
1. 一時的なデータの置き場でサーバを立てる（本物のエンジン。台本係は呼ばない。見本は起動のときに入れない）
2. 前に作った `built/` があれば、起動のときと同じ取り込みの道で入れる（絵の控えが入る）。`--fresh` なら空のデッキから
3. `script.json` を検査する（ページの id・日本語の検査・エンジンの check）。通ればいまの台本にし、描き直し（render）の仕事で描く。
   中身が変わらないページの絵は控えから使い回す（ピッチは鍵が合えば Codex を呼ばない）
4. 見せる回から `built/` を作り直す（記録の中の置き場と id を目印にする。作った機械のパスが残っていたら作らない）

エンジンの設定は一時的な置き場に**字体（Noto Sans JP）だけ**を書く。この機械の設定（ロゴ・出力先など）は読まない。
字体はこの機械のデータの置き場の `fonts/` から写す（無ければエンジンの代わりの字体で組まれる）。
ピッチの絵は Codex（ChatGPT のサブスク枠）で描く。従量課金の API は使わない。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from quick_slide import samples as S  # noqa: E402
from quick_slide.engines import engine_for  # noqa: E402
from quick_slide.paths import DataRoot  # noqa: E402
from quick_slide.render.runner import check_script  # noqa: E402
from quick_slide.scriptdoc import dumps, page_id_problems, screen_pages, slides_of  # noqa: E402
from quick_slide.server.app import QuickSlideServer  # noqa: E402
from quick_slide.settings import Settings, WorkerSettings  # noqa: E402
from quick_slide.store.presets import preset_style_for  # noqa: E402
from quick_slide.worker.pipeline import engine_script_view, lint_view  # noqa: E402
from quick_slide.worker.tools import Tools  # noqa: E402
from quick_slide.writer import jlint as JLINT  # noqa: E402
from quick_slide.render import look as LOOK  # noqa: E402

ENGINE_SKILLS = ("html-slide", "image-slide", "motion-slide")
NOTO = ("noto-sans-jp/NotoSansJP-Regular.ttf", "noto-sans-jp/NotoSansJP-Bold.ttf")
WAIT_SECONDS = 1800


def say(msg: str) -> None:
    print(msg, flush=True)


def prepare_home(tmp: Path, real: DataRoot) -> tuple[DataRoot, dict[str, str]]:
    """一時的なデータの置き場（字体だけの設定）と、エンジンに渡す環境（キャッシュも一時的な置き場へ）。"""
    root = DataRoot(tmp / "home").ensure()
    fonts = real.root / "fonts"
    lines = []
    if all((fonts / f).is_file() for f in NOTO):
        shutil.copytree(fonts / "noto-sans-jp", root.root / "fonts" / "noto-sans-jp")
        lines = ["fonts:", '  "Noto Sans JP":',
                 f"    regular: {json.dumps(str(root.root / 'fonts' / NOTO[0]))}",
                 f"    bold: {json.dumps(str(root.root / 'fonts' / NOTO[1]))}"]
    else:
        say(f"注意: {fonts}/noto-sans-jp に Noto Sans JP が無いので、エンジンの代わりの字体で組みます")
    # html-slide が組むのに使う Node と Chrome は、この機械のデータの置き場にあればそこを指す（無ければエンジンの既定の探し方）
    node_lines = []
    for key, rel in (("node_modules", "engines/html-slide/node/node_modules"), ("chrome", "engines/html-slide/chrome")):
        if (real.root / rel).exists():
            node_lines.append(f"{key}: {json.dumps(str(real.root / rel))}")
    for skill in ("html-slide", "image-slide"):
        cfg = root.root / "engines" / skill / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        body = ["# samples/build.py が書いた一時的な設定（字体だけ）", *lines, *(node_lines if skill == "html-slide" else [])]
        cfg.write_text("\n".join(body) + "\n", encoding="utf-8")
    env = {f"{s.upper().replace('-', '_')}_CACHE": str(tmp / "cache" / s) for s in ENGINE_SKILLS}
    for s in ENGINE_SKILLS:   # 環境変数で別の設定を指していても、一時的な置き場の設定を読ませる
        env[f"{s.upper().replace('-', '_')}_CONFIG"] = str(root.root / "engines" / s / "config.yaml")
    (root.root / "engines" / "motion-slide").mkdir(parents=True, exist_ok=True)
    (root.root / "engines" / "motion-slide" / "config.yaml").write_text("# samples/build.py が書いた一時的な設定（空）\n",
                                                                         encoding="utf-8")
    return root, env


def check_source(folder: Path, meta: dict, tools: Tools, root: DataRoot, tmp: Path) -> dict:
    """台本の正本を確かめる: ページの id・型・日本語の検査・エンジンの check。通った台本を返す。"""
    data = json.loads((folder / "script.json").read_text(encoding="utf-8"))
    args = {"mode": meta["mode"], "style": meta["style"], **meta["overrides"]}
    slides = slides_of(data)
    problems = page_id_problems(slides) + (LOOK.type_problems(slides, args) if meta["mode"] != "motion" else [])
    ng, notes = JLINT.problems_and_notes(lint_view(args, slides), enforce=True)
    problems += ng
    for n in notes:
        say(f"  {n}")
    if problems:
        raise S.SampleError("台本の検査で止まりました:\n  " + "\n  ".join(problems))
    path = tmp / "check" / folder.name / "engine.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dumps(engine_script_view(data, args)))
    got = check_script(engine_for(meta["mode"]), tools=tools, home=root.root, script=path, args=args, timeout=900)
    if got.rc != 0:
        raise S.SampleError("エンジンの check で止まりました（rc=%s）:\n  %s" % (got.rc, "\n  ".join(got.problems or got.warnings)))
    for w in got.warnings:
        say(f"  {w}")
    return data


def build_one(server: QuickSlideServer, folder: Path, *, fresh: bool, tools: Tools, tmp: Path, cache_roots: list[Path],
              markers: list[str]) -> dict:
    root, store, lock = server.root, server.store, server.store_lock
    meta = S.read_meta(folder)
    say(f"[{meta['id']}] 台本を確かめています")
    data = check_source(folder, meta, tools, root, tmp)
    with lock:
        deck = None
        if not fresh and (folder / "built" / "manifest.json").is_file():
            try:
                deck = S.import_sample(store, root, S.read_sample(folder), extra_env=tools.extra_env)
                say(f"[{meta['id']}] 前に作った見本を入れました（絵の控えを使い回します）")
            except S.SampleError as exc:
                say(f"[{meta['id']}] 前に作った見本を入れられないので、空のデッキから描きます（{exc}）")
        if deck is None:
            style_rev = store.preset_style_revision_id(preset_style_for(meta["mode"], meta["style"]))
            deck = store.create_deck(meta["title"], mode=meta["mode"], brand_revision_id=store.preset_brand_revision_id(),
                                     style_revision_id=style_rev)
        args = {"mode": meta["mode"], "style": meta["style"], "overrides": meta["overrides"], "images": False,
                "purpose": meta.get("purpose") or "", "audience": meta.get("audience") or "",
                "page_count": len(slides_of(data))}
        head = store.head_script(deck["id"])
        if head is None or dumps(store.load_script(head)) != dumps(data):
            saved = store.save_script(deck["id"], data, written_by="screen", writer_backend=None, intake_id=None, run_id=None)
            store.set_script_check(saved["id"], 0, [])
            store.accept_script(saved["id"], screen_pages(args, data))
        with store.transaction():
            run = store.create_run(deck["id"], args=args)
            store.create_job(deck["id"], run_id=run["id"], intake_id=None, kind="edit",
                             request={"op": "render", "source": "screen"})
    say(f"[{meta['id']}] 描いています")
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        with lock:
            if not store.deck_working(deck["id"]):
                done = store.run(run["id"])
                break
        if time.monotonic() > deadline:
            raise S.SampleError(f"{WAIT_SECONDS} 秒待っても描き終わりませんでした")
        time.sleep(1.0)
    if done["state"] != "done":
        raise S.SampleError(f"描けませんでした（{done['failure_kind']}）: {done['message']}")
    placed = (done.get("record") or {}).get("cache_placed") or {}
    if placed:
        say(f"[{meta['id']}] 控えの絵を使ったページ: {', '.join(sorted(placed))}")
    with lock:
        manifest = S.pack(store, root, deck["id"], folder, cache_roots=cache_roots, extra_markers=markers)
    size = sum(p.stat().st_size for p in (folder / "built").rglob("*") if p.is_file())
    say(f"[{meta['id']}] できました: {len(manifest['pages'])} ページ・{size / 1e6:.1f} MB・版 {manifest['version'][:12]}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="同梱の見本を作り直す（samples/<見本>/built/）")
    ap.add_argument("ids", nargs="*", help="作る見本（省けば全部）")
    ap.add_argument("--fresh", action="store_true", help="前に作った絵を使わずに描き直す")
    ap.add_argument("--keep", action="store_true", help="一時的なデータの置き場を残す")
    a = ap.parse_args(argv)
    folders = sorted(p for p in HERE.iterdir() if p.is_dir() and (p / "sample.json").is_file())
    if a.ids:
        unknown = sorted(set(a.ids) - {p.name for p in folders})
        if unknown:
            ap.error(f"知らない見本: {', '.join(unknown)}")
        folders = [p for p in folders if p.name in a.ids]
    real = DataRoot.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="quick-slide-samples-")).resolve()
    say(f"一時的なデータの置き場: {tmp}")
    root, env = prepare_home(tmp, real)
    tools = Tools(extra_env=env)
    server = QuickSlideServer(root, settings=Settings(worker=WorkerSettings(parallel=1)), port=0, tools=tools)
    server.start_in_thread()
    failed = 0
    try:
        markers = [str(tmp), str(real.root)]
        cache_roots = [tmp / "cache" / s for s in ENGINE_SKILLS]
        for folder in folders:
            try:
                build_one(server, folder, fresh=a.fresh, tools=tools, tmp=tmp, cache_roots=cache_roots, markers=markers)
            except S.SampleError as exc:
                failed += 1
                say(f"[{folder.name}] 作れませんでした: {exc}")
    finally:
        server.close()
        if a.keep:
            say(f"残しました: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
