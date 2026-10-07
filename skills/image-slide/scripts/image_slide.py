#!/usr/bin/env python3
"""image-slide の入口。html-slide と同じ台本から、1枚ずつ画像生成で丸ごと描いた資料を作る。

    check   台本を読み、1枚ずつの描く指示（プロンプト）を組む。Codex は呼ばない（--prompts で書き出す）
    auth    Codex の認証を確かめる（deck が最初に1度だけやるのと同じもの）
    deck    台本 → 認証を1度 → 全ページを並列に描く → 16:9 にそろえる・帯の検査 → PPTX・PDF・ページの画像・サムネイル（--format で選ぶ）→ inspect.md
    revise  描いた1枚を指示で直す（既定は微修正＝今の絵を土台に・--remake で渡さずに作り直す）→ PPTX・PDF を組み直す（ADR 0012）

終了コード: 0=成功 / 1=実行時エラー（描けなかったページがある）/ 2=台本・引数の誤り / 4=依存不足・認証
"""
from __future__ import annotations

import argparse
import shutil
import json
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.dont_write_bytecode = True   # 置いた場所（配る物の中・ほかの環境からのリンク先）に __pycache__ を残さない
sys.path.insert(0, str(HERE))

import cache as K  # noqa: E402
import codex_run as X  # noqa: E402
import compose as C  # noqa: E402
import layout as L  # noqa: E402
import layouts as Y  # noqa: E402
import outputs as OUT  # noqa: E402
import places as PL  # noqa: E402
import prompt as P  # noqa: E402
import pptx_out as O  # noqa: E402
import script_io as S  # noqa: E402
import styles as ST  # noqa: E402

MAX_REFS = 5          # 1枚に添える参照画像の上限（画像の枠・構図の見本・_characters・--ref の合計）。Codex の画像ツールの上限
                      # MAX_EDIT_IMAGES = 5（openai/codex codex-rs/ext/image-generation/src/tool.rs・0.159.2）
DEFAULT_PARALLEL = 8  # 並列の既定（サブスク枠は画像で減りが速いので、日常は8）
DEFAULT_TIMEOUT = 600


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def out_root(cfg: dict) -> pathlib.Path:
    return PL.out_root("image-slide", cfg)


def _image_arg(p, what) -> pathlib.Path:
    q = pathlib.Path(p).expanduser().resolve()
    if not q.is_file():
        raise S.ScriptError(f"{what} が無い: {q}")
    if q.suffix.lower() not in S.IMAGE_EXTS:
        raise S.ScriptError(f"{what} は {' / '.join(S.IMAGE_EXTS)} のどれか: {q.name}")
    return q


def plan(a, cfg: dict) -> dict:
    """台本・テーマ・引数から、1枚ずつの描く指示まで組む（Codex は呼ばない）。誤りは ScriptError。"""
    deck = S.load(a.script)
    theme = S.load_theme(a.theme or deck["theme"], deck["dir"], cfg)
    logo = _image_arg(a.logo, "ロゴ（--logo）") if a.logo else None
    logo_white = _image_arg(a.logo_white, "白いロゴ（--logo-white）") if getattr(a, "logo_white", None) else None
    if logo_white and not logo:
        raise S.ScriptError("--logo-white は --logo と一緒に渡す")
    if logo_white:
        try:
            w1, w2 = C.read_logo(logo)[0].size, C.read_logo(logo_white)[0].size
        except ValueError as e:
            raise S.ScriptError(str(e)) from e
        if abs(w2[0] / w2[1] - w1[0] / w1[1]) > 0.02 * w1[0] / w1[1]:
            raise S.ScriptError(f"白いロゴ（--logo-white）の縦横比がロゴと違う（{w2[0]}×{w2[1]} と {w1[0]}×{w1[1]}）。同じ形の公式の白版を渡す")
    logo_position = getattr(a, "logo_position", None) or "bottom-right"
    if logo_position not in L.LOGO_POSITIONS:
        raise S.ScriptError(f"ロゴの位置（--logo-position）は {' / '.join(L.LOGO_POSITIONS)} のどれか（{logo_position!r}）")
    pages, warnings = [], []
    if logo:
        if logo.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise S.ScriptError(f"ロゴ（--logo）は PNG か JPEG（html-slide と同じ）: {logo.name}")
        try:
            _, geo = C.read_logo(logo)
        except ValueError as e:
            raise S.ScriptError(str(e)) from e
        if geo["warning"]:
            warnings.append(geo["warning"])
    refs = [_image_arg(r, "参照画像（--ref）") for r in (a.ref or [])]
    layouts = Y.load(getattr(a, "layout", None))
    style = ST.load(getattr(a, "style", None))
    if getattr(a, "image_look", None):
        style["image_look"] = a.image_look      # 絵の部分の画風（html-slide と同じ3語・同じ言い回し）
    if style.get("one_page"):
        return {**plan_one_page(deck, theme, logo, refs, layouts, style, a, warnings), "logo_position": logo_position,
                "logo_white": logo_white}
    for pg in deck["slides"]:
        number = str(pg["no"]) if pg["chrome"] and deck["page_numbers"] else ""
        # 帯を頼むのは、帯の部品（フッタ・ページ番号・ロゴ）を載せる本文のページだけ。表紙・章扉・写真には頼まず、
        # 描いた後に右下・左下の空いている方にロゴを置く（全面の絵を下で途切れさせない・ADR 0008）。
        # 全面に描かせるスタイル（visual の bleed）では本文のページにも帯を頼まず、下端に文字や図だけ置かせない（ADR 0009）
        wants = bool(pg["chrome"] and (logo or deck["footer"] or number))
        bleed = wants and style.get("bleed", False)
        needs_band = wants and not bleed
        built = P.build(deck, pg, theme, style=style, style_note=getattr(a, "style_note", None) or "",
                        refs_common=len(refs), band="bleed" if bleed else needs_band, layouts=layouts)
        own = [x["path"] for x in built["images"] if x["path"]]
        lay = built["layout"]
        own += [lay["spec"]["reference"]] if lay and lay["spec"]["reference"] else []   # 構図の見本は枠の後ろ・キャラの前
        own += [c["path"] for c in built["characters"]]                                  # キャラは構図の見本の後ろ・--ref の前
        if len(own) + len(refs) > MAX_REFS:
            raise S.ScriptError(f"{pg['no']}枚目: 参照画像が {len(own) + len(refs)} 枚（画像の枠・構図の見本・キャラ {len(own)}・--ref {len(refs)}）。"
                                f"1枚に添えられるのは {MAX_REFS} 枚まで（Codex の画像ツールの上限）")
        ref_paths = own + refs
        pages.append({**pg, "text": built["text"], "refs": ref_paths, "key": P.page_key(built["key_text"], ref_paths),
                      "number": number, "needs_band": needs_band, "bleed": bleed, "notes": S.talk_text(pg["spec"]),
                      "alt": S.headline_of(pg["spec"]), "images": built["images"], "layout": built["layout"],
                      "content": S.content_of(pg["spec"], built["images"])})
    return {"deck": deck, "theme": theme, "logo": logo, "logo_white": logo_white, "logo_position": logo_position, "refs": refs, "pages": pages,
            "warnings": warnings, "style": style}


def plan_one_page(deck, theme, logo, refs, layouts, style, a, warnings) -> dict:
    """one_page のスタイル（infographic など）: 台本の全ページを1枚にまとめる。ページ番号は出さない（1枚なので）。"""
    needs_band = bool(logo) or bool(deck["footer"])
    built = P.build_one_page(deck, theme, style=style, style_note=getattr(a, "style_note", None) or "",
                             refs_common=len(refs), band=needs_band, layouts=layouts)
    own = [x["path"] for x in built["images"] if x["path"]]
    lay = built["layout"]
    own += [lay["spec"]["reference"]] if lay and lay["spec"]["reference"] else []
    own += [c["path"] for c in built["characters"]]
    if len(own) + len(refs) > MAX_REFS:
        raise S.ScriptError(f"参照画像が {len(own) + len(refs)} 枚（全ページの画像の枠・構図の見本・キャラ {len(own)}・--ref {len(refs)}）。"
                            f"1枚に凝縮すると、全ページぶんを1枚に添えることになる。添えられるのは {MAX_REFS} 枚まで")
    ref_paths = own + refs
    notes = "\n\n".join(f"【{p['no']}枚目】" + S.talk_text(p["spec"]) for p in deck["slides"] if S.talk_text(p["spec"]))
    page = {"no": 1, "type": "infographic" if style["name"] == "infographic" else style["name"], "spec": deck["slides"][0]["spec"],
            "chrome": True, "text": built["text"], "refs": ref_paths, "key": P.page_key(built["key_text"], ref_paths),
            "number": "", "needs_band": needs_band, "bleed": False, "notes": notes, "alt": deck["title"], "images": built["images"],
            "layout": lay, "content": built["content"], "pages": [p["no"] for p in deck["slides"]]}
    warnings.append(f"スタイル {style['name']} は台本の {len(deck['slides'])} 枚を1枚に凝縮する")
    return {"deck": deck, "theme": theme, "logo": logo, "refs": refs, "pages": [page], "warnings": warnings, "style": style}


def _redo_set(spec: str | None, n: int) -> set[int]:
    if not spec:
        return set()
    if spec == "all":
        return set(range(1, n + 1))
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out |= set(range(int(lo), int(hi) + 1))
        elif part:
            out.add(int(part))
    bad = sorted(x for x in out if not 1 <= x <= n)
    if bad:
        raise S.ScriptError(f"--redo のページ番号が範囲の外: {bad}（1〜{n}）")
    return out


def adopt_raw(old: pathlib.Path, new: pathlib.Path) -> None:
    """前の形（出力フォルダの raw/）の絵と鍵を、キャッシュの区画へ移す。区画に同じ名前があれば区画の方を残す。"""
    if not old.is_dir():
        return
    for p in sorted(old.rglob("*")):
        dest = new / p.relative_to(old)
        if p.is_file() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), dest)
    shutil.rmtree(old, ignore_errors=True)


def prepare_revision(a, pl: dict, rawdir: pathlib.Path) -> dict:
    """revise の注文を確かめ、直すページの描く指示と参照画像を差し替える（ADR 0012）。誤りは ScriptError（rc=2）。

    - 微修正（minor・既定）は今の絵（キャッシュの区画の絵・ロゴや透かしの前）を土台に添える。作り直し（remake）は添えない
    - 参照画像の並びは 元の並び（枠・見本・キャラ・--ref）→ 土台 → --attach。元の指示の番号をずらさない
    - 前の絵と鍵は区画の history/ に写してから描く（戻したいときはそこから）。鍵（key）は台本から作った元のまま入れるので、
      台本を変えずに deck を流し直しても、直した絵が使い回される
    """
    pages = pl["pages"]
    if not 1 <= a.page <= len(pages):
        raise S.ScriptError(f"--page {a.page} が範囲の外（1〜{len(pages)}）")
    attach = [_image_arg(r, "参考画像（--attach）") for r in (a.attach or [])]
    note = (a.note or "").strip()
    if not note and not attach:
        raise S.ScriptError("直す中身が無い。--note（文）か --attach（参考画像）のどちらかを渡す")
    pg = pages[a.page - 1]
    no = pg["no"]
    raw, meta = rawdir / f"slide-{no:02d}.png", rawdir / f"slide-{no:02d}.json"
    if a.mode == "minor" and not raw.is_file():
        raise S.ScriptError(f"{no}枚目はまだ描いていないので、微修正の土台が無い。先に deck を流すか、--remake で描く")
    n = len(pg["refs"]) + (1 if a.mode == "minor" else 0) + len(attach)
    if n > MAX_REFS:
        raise S.ScriptError(f"{no}枚目: 参照画像が {n} 枚（元の {len(pg['refs'])}・土台 {1 if a.mode == 'minor' else 0}・--attach {len(attach)}）。"
                            f"1枚に添えられるのは {MAX_REFS} 枚まで（Codex の画像ツールの上限）")
    hist = None
    if raw.is_file():
        (rawdir / "history").mkdir(parents=True, exist_ok=True)
        hist = rawdir / "history" / f"slide-{no:02d}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        shutil.copy2(raw, hist)
        if meta.is_file():
            shutil.copy2(meta, hist.with_suffix(".json"))
    refs = list(pg["refs"])
    base_no = None
    if a.mode == "minor":
        refs.append(hist)
        base_no = len(refs)
    span = (len(refs) + 1, len(refs) + len(attach)) if attach else None
    refs += attach
    pg["text"] += "\n\n" + P.revision_part(a.mode, note, base_no, span)
    pg["refs"] = refs
    pg["revision"] = {"mode": a.mode, "note": note, "attach": [str(x) for x in attach],
                      "previous": str(hist) if hist else None, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return pg


def band_notice(band: dict) -> str:
    return (f"【やり直しの理由】前回の絵は、下端の帯（高さ {L.BAND_RATIO:.0%}）に物が描かれていた"
            f"（帯の {band['share']:.1%} が帯の色と違っていた）。同じ構図をもう一度描かない。"
            "要素は帯の手前で終え、帯は端から端まで1色の無地にする。帯の上にもはっきり余白を空ける。")


# --- 入口 --------------------------------------------------------------------------------

def cmd_check(a) -> int:
    cfg = S.load_config()
    try:
        pl = plan(a, cfg)
    except S.ScriptError as e:
        print(f"NG: {e}", file=sys.stderr)
        return e.rc
    if a.prompts:
        d = pathlib.Path(a.prompts).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        for pg in pl["pages"]:
            (d / f"slide-{pg['no']:02d}.txt").write_text(pg["text"] + "\n", encoding="utf-8")
    for w in pl["warnings"]:
        print(f"警告: {w}", file=sys.stderr)
    for pg in pl["pages"]:
        refs = f"・参照画像 {len(pg['refs'])}" if pg["refs"] else ""
        lay = f"レイアウト {pg['layout']['name']}" if pg["layout"] else "構図は任せる"
        print(f"{pg['no']:>2}枚目 {pg['type']:<11} {lay:<20} {pg['alt'][:36]}（指示 {len(pg['text'])}字{refs}）")
    print(f"OK（{len(pl['pages'])}枚・スタイル {pl['style']['name']}・テーマ {pl['theme']['name']}）")
    return 0


def cmd_auth(a) -> int:
    cfg = S.load_config()
    try:
        cli = X.find_cli(cfg)
        r = X.check_auth(cli, log=log)
    except X.CodexError as e:
        print(f"NG: {e.detail}", file=sys.stderr)
        return 4
    rep = r["report"]
    print(f"OK: ChatGPT でログイン済み（最後の更新から {rep['age_days']} 日・残り {rep['hours_left']} 時間"
          + ("・いま更新した" if rep["refreshed_here"] else "") + "）")
    return 0


def cmd_deck(a) -> int:
    cfg = S.load_config()
    t0 = time.monotonic()
    try:
        fmts = OUT.formats_of(a.format)
        if a.no_pdf:        # 前の口（--format から pdf を外すのと同じ）
            fmts = tuple(f for f in fmts if f != "pdf")
        pl = plan(a, cfg)
        redo = _redo_set(a.redo, len(pl["pages"]))
    except (S.ScriptError, ValueError) as e:
        print(f"NG: {e}", file=sys.stderr)
        return getattr(e, "rc", 2)
    deck, theme, pages = pl["deck"], pl["theme"], pl["pages"]
    for w in pl["warnings"]:
        log(f"警告: {w}")
    out = pathlib.Path(a.out).expanduser() if a.out else out_root(cfg) / deck["path"].stem
    for sub in ("prompts", "slides", "logs"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    # 描いた絵と鍵はキャッシュの区画（ADR 0015）。前の形の raw/ は区画へ移し、作り直せる base/ は消す
    rawdir = K.area("image-slide", cfg, out)
    adopt_raw(out / "raw", rawdir)
    shutil.rmtree(out / "base", ignore_errors=True)
    target = None
    if a.cmd == "revise":   # 直すページだけ描く。ほかのページは前に描いた絵をそのまま使う（鍵が古くても描かない）
        try:
            missing = [pg["no"] for pg in pages if pg["no"] != a.page and not (rawdir / f"slide-{pg['no']:02d}.png").is_file()]
            if missing:
                raise S.ScriptError(f"まだ描いていないページがある: {missing}。先に deck を流す（revise は1枚だけ描く）")
            target = prepare_revision(a, pl, rawdir)
        except S.ScriptError as e:
            print(f"NG: {e}", file=sys.stderr)
            return e.rc

    # 前に描いた絵を使い回すか（プロンプトと参照画像が同じで、--redo に入っていない）
    todo = []
    for pg in pages:
        raw, meta = rawdir / f"slide-{pg['no']:02d}.png", rawdir / f"slide-{pg['no']:02d}.json"
        (out / "prompts" / f"slide-{pg['no']:02d}.txt").write_text(pg["text"] + "\n", encoding="utf-8")
        try:
            old = json.loads(meta.read_text()) if meta.is_file() else {}
        except (OSError, ValueError):
            old = {}                                   # 壊れた鍵は、使い回さずに描き直す
        old = old if isinstance(old, dict) else {}
        pg["raw"], pg["meta"] = raw, meta
        if target is not None and pg is not target:
            pg["gen"] = {**old, "status": "cached"}
            if old.get("key") != pg["key"]:
                log(f"警告: {pg['no']}枚目は台本が変わっているが、revise では描き直さない（deck を流すと描き直す）")
        elif target is None and raw.is_file() and old.get("key") == pg["key"] and pg["no"] not in redo:
            pg["gen"] = {**old, "status": "cached"}
        else:
            todo.append(pg)
    if target is not None:
        what = {"minor": "微修正（今の絵を土台に渡す）", "remake": "作り直し（今の絵は渡さない）"}[a.mode]
        log(f"{target['no']}枚目を{what}" + (f"。前の絵は {target['revision']['previous']}" if target["revision"]["previous"] else ""))
    log(f"{len(pages)}枚（描く {len(todo)}・使い回す {len(pages) - len(todo)}）→ {out}")

    auth_report, results = None, []
    if todo:
        try:
            cli = X.find_cli(cfg)
            auth = X.check_auth(cli, log=log)           # 認証は、この実行でここの1度だけ
        except X.CodexError as e:
            print(f"NG: {e.detail}", file=sys.stderr)
            return 4
        auth_report = auth["report"]
        attempts = 1 + max(0, a.retries)
        runner = X.Runner(a.parallel or int(cfg.get("max_parallel") or DEFAULT_PARALLEL), attempts, log=log)
        timeout = int(cfg.get("timeout") or DEFAULT_TIMEOUT)

        def work(pg, attempt, last):
            """1回ぶん。候補は別名（.try.png）に描き、受け入れたときだけ絵と鍵（.json）を一緒に入れ替える。
            途中で落ちても、前の絵と前の鍵の組は崩れない（描き直しに失敗した絵を、次の実行で使い回さない）。"""
            text = pg["text"]
            if last is not None and last.kind == "band":
                text += "\n\n" + band_notice(last.detail)
            cand = pg["raw"].with_name(pg["raw"].stem + ".try.png")
            try:
                got = X.generate(cli, auth["snapshot"], text, pg["refs"], cand, out / "logs",
                                 timeout=timeout, should_stop=runner.stop.is_set)
                band = None
                if pg["needs_band"]:
                    im, _ = C.normalize(cand, theme["background"])
                    band = C.band_check(im)
                    if not band["ok"] and attempt < attempts:
                        raise X.CodexError("band", band)
                meta = {"key": pg["key"], "attempts": attempt, "seconds": got["seconds"],
                        "band_ok": None if band is None else band["ok"],
                        "worker_refreshed": got["worker_refreshed"], "extra_images": got["extra_images"],
                        **({"revision": pg["revision"]} if pg.get("revision") else {})}
                # 入れ替えの順: 新しい鍵を一時ファイルに書き切る → 古い鍵を消す → 絵を入れ替える → 鍵を入れ替える。
                # どこで落ちても「新しい絵＋古い鍵」の組は残らない（鍵が無ければ次の実行で描き直すだけ）
                meta_tmp = pg["meta"].with_suffix(".json.tmp")
                meta_tmp.write_text(json.dumps(meta, ensure_ascii=False) + "\n")
                pg["meta"].unlink(missing_ok=True)
                cand.replace(pg["raw"])
                meta_tmp.replace(pg["meta"])
                return meta
            except OSError as e:
                raise X.CodexError("gen", f"絵か鍵を書けなかった: {e}")
            finally:
                cand.unlink(missing_ok=True)
                pg["meta"].with_suffix(".json.tmp").unlink(missing_ok=True)

        log(f"並列 {runner.max_parallel} で描く（1枚の上限 {timeout} 秒・試行 {attempts} 回まで）")
        results = runner.run(todo, work)
        for pg, res, err in results:
            pg["gen"] = {**res, "status": "generated"} if err is None else {"status": "failed", "error": err.kind,
                                                                           "detail": str(err.detail)[:300]}
        refreshed = sum(1 for _, res, _ in results if res and res.get("worker_refreshed"))
        auth_report["worker_refreshed"] = refreshed
        if refreshed:
            log(f"警告: {refreshed} 本のワーカーの中でトークンが更新された（実 auth.json は書き換えていない）。"
                "失効が出たら `codex login` で入り直す")

    K.trim("image-slide", cfg, rawdir, log=log)
    failed = [pg for pg in pages if pg["gen"]["status"] == "failed"]
    build = {"title": deck["title"], "script": str(deck["path"]), "theme": theme["name"], "style": pl["style"]["name"],
             "auth": auth_report, "cache": str(rawdir),
             "parallel": a.parallel or cfg.get("max_parallel") or DEFAULT_PARALLEL,
             "slides": [{"no": pg["no"], "type": pg["type"], "layout": pg["layout"]["name"] if pg["layout"] else None, "key": pg["key"], **pg["gen"]}
                        for pg in pages]}
    if failed:
        build["elapsed"] = round(time.monotonic() - t0, 1)
        (out / "build.json").write_text(json.dumps(build, ensure_ascii=False, indent=1) + "\n")
        for pg in failed:
            print(f"NG: {pg['no']}枚目が描けなかった（{pg['gen']['error']}）: {pg['gen']['detail']}", file=sys.stderr)
        print("描けたページは覚えている。直してもう一度 deck を流せば、残りだけ描く", file=sys.stderr)
        return 4 if any(pg["gen"]["error"] == "auth" for pg in failed) else 1
    # そろえた絵（PPTX に入れる元）は作り直せるので、出力フォルダに残さない
    with tempfile.TemporaryDirectory(prefix="image-slide-base.") as basedir:
        return finish(a, cfg, pl, out, build, fmts, t0, pathlib.Path(basedir))


def finish(a, cfg: dict, pl: dict, out: pathlib.Path, build: dict, fmts, t0: float, basedir: pathlib.Path) -> int:
    """描き終えた後: そろえる・帯の検査・ロゴ・確認用の合成 → 出力。そろえた絵は basedir（使い捨て）に置く。"""
    deck, theme, pages = pl["deck"], pl["theme"], pl["pages"]

    # そろえる・帯の検査・ロゴの置き場・確認用の合成
    OUT.prune_pages(out / "slides", len(pages))
    font_file = C.font_path(theme, cfg)
    watermark = getattr(a, "watermark", None) or ""
    wm_font = C.font_path(theme, cfg, "regular") if watermark else None
    logo = C.load_logo(pl["logo"], out, pl.get("logo_white"), L.row_mid(bool(deck["footer"] or deck["page_numbers"]))) if pl["logo"] else None
    for pg in pages:
        # 下の行を上げるのは、フッタかページ番号があるページだけ（下の端は透かしの場所）。ロゴだけのページ（表紙・写真・
        # フッタの無いポスター）は上げない。ロゴは角なので中央の透かしに届かず、上げると周りの絵や枠にかかる（ADR 0010）
        footer = deck["footer"] if pg["chrome"] else ""
        mid = L.row_mid(bool(footer or pg["number"]))
        lg = {**logo, "y": round(mid - logo["h"] / 2)} if logo else None
        im, norm = C.normalize(pg["raw"], theme["background"])
        base = basedir / f"slide-{pg['no']:02d}.png"
        im.save(base)
        band = C.band_check(im)
        plate = pg["needs_band"] and not band["ok"]
        if plate:
            log(f"警告: {pg['no']}枚目の帯が空いていない（{band['share']:.1%}）。地の色の板を敷いてから部品を載せた")
        place = (C.place_logo(im, lg, band=("plate" if plate else "ok") if pg["needs_band"] else None,
                              position=pl["logo_position"], bleed=pg["bleed"], background=theme["background"]) if logo else None)
        if place and place.get("off"):
            log(f"{pg['no']}枚目: ロゴを置ける角に1色の地が無い（写真・絵がかかっている）ので、ロゴは置かない")
        boxes = C.chrome_boxes(footer, pg["number"], place if logo and place and not place.get("off") else None, font_file, mid)
        ink = C.inks(im, theme=theme, band=band, plate=plate, bleed=pg["bleed"], boxes=boxes)
        if watermark:   # 測り終えてから焼く。PPTX の絵にも焼いたまま渡す（全ページ・表紙も写真も）
            im = C.burn_watermark(im, watermark, wm_font)
            base = basedir / f"slide-{pg['no']:02d}.watermark.png"
            im.save(base)
        shown = C.overlay(im, theme=theme, plate=plate, footer=footer, number=pg["number"], ink=ink,
                          logo=lg, place=place, font_file=font_file, mid=mid)
        shown.save(out / "slides" / f"slide-{pg['no']:02d}.png")
        pg.update(base=base, band=band, plate=plate, normalize=norm, logo=place, ink=ink, mid=mid)
    # 出力（ADR 0013）。ページの画像（slides/）は PDF と検品の元なので、png を選ばなくても残す
    pngs = [out / "slides" / f"slide-{pg['no']:02d}.png" for pg in pages]
    made = {"png": [str(p) for p in pngs]}
    if "pptx" in fmts:
        O.write_pptx(pages, out / "deck.pptx", theme=theme, footer=deck["footer"], logo=logo,
                     font_file=font_file, title=deck["title"])
        made["pptx"] = str(out / "deck.pptx")
    if "pdf" in fmts:
        O.write_pdf(pngs, out / "deck.pdf")
        made["pdf"] = str(out / "deck.pdf")
    if "thumbs" in fmts:
        made["thumbs"] = [str(p) for p in OUT.write_thumbs(pngs, [pg["alt"] for pg in pages], out, deck["title"])]
    build["outputs"] = {"format": list(fmts), **made}
    for row, pg in zip(build["slides"], pages):
        row.update(normalize=pg["normalize"], band=pg["band"] if pg["needs_band"] else None, plate=pg["plate"])
    if logo:   # html-slide の deck-info.json の logo と同じ形
        build["logo"] = {"path": logo["src"], "position": pl["logo_position"], **{k: logo[k] for k in ("x", "y", "w", "h", "scale", "trim")},
                         "pages": [{"no": pg["no"], "off": True} if pg["logo"].get("off") else
                                   {"no": pg["no"], "corner": pg["logo"]["corner"], "x": pg["logo"]["x"], "plate": pg["logo"]["plate"],
                                    **({"variant": "white"} if pg["logo"].get("variant") == "white" else {}),
                                    **({"sticker": True} if pg["logo"].get("sticker") else {})}
                                   for pg in pages]}
    if watermark:
        build["watermark"] = watermark
    build["elapsed"] = round(time.monotonic() - t0, 1)
    (out / "build.json").write_text(json.dumps(build, ensure_ascii=False, indent=1) + "\n")
    insp = write_inspect(out, pl, build)
    gen = sum(1 for pg in pages if pg["gen"]["status"] == "generated")
    print(f"slides={len(pages)} generated={gen} cached={len(pages) - gen} plates={sum(1 for pg in pages if pg['plate'])} "
          f"elapsed={build['elapsed']}s")
    if "pdf" in made:
        print(f"pdf: {made['pdf']}")
    print(f"png: {out / 'slides'}（{len(pngs)}枚）")
    for t in made.get("thumbs", []):
        print(f"thumbs: {t}")
    print(f"inspect: {insp}")
    if "pptx" in made:
        print(made["pptx"])
    return 0


def band_parts(pg: dict, deck: dict, logo, watermark: str = "") -> str:
    """帯に載せた部品の一覧（検品役が、絵の下端の文字を台本と照らせるように）。"""
    parts = []
    if pg["chrome"] and deck["footer"]:
        parts.append(f"フッタ「{deck['footer']}」")
    if pg["number"]:
        parts.append(f"ページ番号「{pg['number']}」")
    place = pg.get("logo")
    if logo and place and not place.get("off"):
        parts.append("ロゴ（" + {"bottom-right": "右下", "bottom-left": "左下", "top-right": "右上"}[place["corner"]]
                     + ("・ステッカー" if place.get("sticker") else "・後ろに地" if place["plate"] else "")
                     + ("・公式の白いロゴ" if place.get("variant") == "white" else "") + "）")
    if pg.get("plate"):
        parts.append("地の色の板（帯が空いていなかったため）")
    if watermark:
        parts.append(f"透かし「{watermark}」（中央下の端・薄いグレー）")
    return "・".join(parts)


def write_inspect(out: pathlib.Path, pl: dict, build: dict) -> pathlib.Path:
    """INSPECTION.md の検品役が読む写し。絵そのものは slides/ の画像を検品する側に渡す。"""
    deck, pages = pl["deck"], pl["pages"]
    plates = [pg["no"] for pg in pages if pg["plate"]]
    lines = [f"# 検品用の写し — {deck['title']}", "",
             f"- 台本: `{deck['path']}`", (f"- PPTX: `{out / 'deck.pptx'}`" if "pptx" in build["outputs"] else "- PPTX: 作っていない（--format）"), f"- 枚数: {len(pages)}",
             f"- スタイル: {pl['style']['name']}" + (f"（{pl['style']['description']}）" if pl['style']['description'] else ""),
             f"- テーマ: {pl['theme']['name']}（地 {pl['theme']['background']}・文字 {pl['theme']['ink']}・主役の色 {pl['theme']['primary']}" + (f"・差し色 {pl['theme']['accent']}" if pl['theme'].get('accent') else "") + "）",
             "", "## 機械の検査", "",
             "- 帯（ロゴ・フッタ・ページ番号を載せる下端）: " + ("全ページ空いていた" if not plates else f"空いていなかったページ {plates}（地の色の板を敷いた）"),
             "- 文字は絵の中にある。**描く文字（台本の中身）と、絵の文字が一字一句合っているかは機械では見ていない**（検品役が絵を見て照らす）",
             ""]
    for pg in pages:
        row = next(r for r in build["slides"] if r["no"] == pg["no"])
        lines += [f"## {pg['no']}枚目 — {pg['type']}" + (f"（レイアウト {pg['layout']['name']}）" if pg["layout"] else ""), "",
                  f"- 絵: `{out / 'slides' / ('slide-%02d.png' % pg['no'])}`",
                  f"- 生成: {row['status']}" + (f"（{row.get('attempts')} 回目で通った）" if row.get("attempts") else ""),
                  "- 機械が載せたもの（絵の中の文字ではない・台本の一番外から）: " + (band_parts(pg, deck, pl["logo"], build.get("watermark", "")) or "なし"),
                  "", "描く文字（台本の中身）:", "", "```json",
                  json.dumps(pg["content"], ensure_ascii=False, indent=1), "```", ""]
        if pg["layout"] and pg["layout"]["parts"]:
            lines += ["レイアウトの部品（これも絵の中に描く文字）:", "", "```json",
                      json.dumps(pg["layout"]["parts"], ensure_ascii=False, indent=1), "```", ""]
        lines += ["ノート:", "", f"> {pg['notes'].replace(chr(10), ' / ') or '（なし）'}", ""]
    p = out / "inspect.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


FORMAT_HELP = ("作る出力を pptx,pdf,png,thumbs からカンマで選ぶ（既定は4つとも）。png はページの画像（slides/slide-NN.png）、"
               "thumbs は全ページを並べた一覧（thumbs.png）。例: --format png（ページの画像はどの指定でも残る）。html-slide と同じ口")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="image-slide — 画像生成で1枚丸ごと描く資料（html-slide と同じ台本）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--script", required=True, help="台本（html-slide と同じ JSON）")
        p.add_argument("--theme", help="default / forest / warm / テーマの YAML（台本の theme より優先）")
        p.add_argument("--logo", help="ロゴの画像（PNG・JPEG）。329×44 の枠に縮めて、PowerPoint の部品として置く")
        p.add_argument("--logo-white", help="組織の公式の白いロゴ（任意・ロゴと同じ縦横比）。ロゴが下に沈むときに使う。無ければロゴの後ろに地を敷く（ロゴ自体は加工しない）")
        p.add_argument("--logo-position", choices=list(L.LOGO_POSITIONS), default="bottom-right",
                       help="ロゴを置く角（既定 bottom-right）。ふさがったページは、右下 → 左下・左下 → 右下・右上 → 右下へ逃がし、それもだめなら置かない")
        p.add_argument("--watermark", help="透かしの文字（例: example.com・社外秘）。全ページの中央下の端に、固定の薄いグレーで絵に焼く"
                                           "（PPTX でも絵に焼いたまま）")
        p.add_argument("--ref", action="append", help="全ページに添える参照画像（画風の手本など）。ページごとのキャラは台本の _characters。何度でも")
        p.add_argument("--style", help="スタイル: visual（既定・ビジュアル寄り。プレゼン・ピッチ向け）／standard（説明・報告・社内資料向け）／"
                                       "infographic（資料全体を1枚に凝縮したポスター）／カスタムスタイルの YAML（ADR 0007）")
        p.add_argument("--style-note", help="スタイルへの追加の注文（文）")
        p.add_argument("--image-look", choices=list(ST.IMAGE_LOOKS),
                       help="絵の部分（写真・挿絵）の画風: photo（写実の写真風）／illustration（平らなイラスト）／handdrawn（手描きのスケッチ）。"
                            "省けばスタイルの文章のまま。html-slide の --image-look と同じ3語")
        p.add_argument("--layout", action="append",
                       help="持ち込みのレイアウト（YAML）。何度でも。台本のページに _layout で名前を書いたページだけに効く（ADR 0006）")

    c = sub.add_parser("check", help="台本を読み、描く指示を組む（Codex は呼ばない）")
    common(c)
    c.add_argument("--prompts", help="描く指示を書き出すフォルダ")
    sub.add_parser("auth", help="Codex の認証を確かめる")
    d = sub.add_parser("deck", help="台本 → 並列に描く → PPTX・PDF")
    common(d)
    d.add_argument("--out", help="出力フォルダ（省略時は config の out:/<台本の名前>）")
    d.add_argument("--parallel", type=int, help=f"同時に描く数（既定 {DEFAULT_PARALLEL}・設定の max_parallel）")
    d.add_argument("--retries", type=int, default=1, help="1枚ごとの描き直しの回数（既定 1）")
    d.add_argument("--redo", help="前に描いた絵を使わずに描き直すページ（3,5 / 2-4 / all）")
    d.add_argument("--format", help=FORMAT_HELP)
    d.add_argument("--no-pdf", action="store_true", help=argparse.SUPPRESS)   # 前の口（--format から pdf を外す）
    r = sub.add_parser("revise", help="描いた1枚を指示で直す（微修正・作り直し）→ PPTX・PDF を組み直す（ADR 0012）")
    common(r)
    r.add_argument("--out", help="deck を流した出力フォルダ（省略時は config の out:/<台本の名前>）")
    r.add_argument("--page", type=int, required=True, help="直すページの番号（1から）")
    r.add_argument("--remake", action="store_true",
                   help="作り直し: 今の絵を渡さずに作り直す（テイストを変えるとき）。付けなければ微修正（今の絵を土台に、指示だけ反映して残りは保つ）。"
                        "html-slide の revise と同じ口")
    r.add_argument("--note", help="直す指示（文）")
    r.add_argument("--attach", action="append", help="このページの直しに添える参考画像。何度でも")
    r.add_argument("--retries", type=int, default=1, help="描き直しの回数（既定 1）")
    r.add_argument("--format", help=FORMAT_HELP)
    r.add_argument("--no-pdf", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.cmd == "revise":
        a.redo, a.parallel = None, 1
        a.mode = "remake" if a.remake else "minor"
    return {"check": cmd_check, "auth": cmd_auth, "deck": cmd_deck, "revise": cmd_deck}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
