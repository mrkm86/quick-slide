#!/usr/bin/env python3
"""レビューモード: 台本 → review.html（全ページ・目次・メモ・検査結果を並べた、人が読む HTML）。ADR 0028。

    python3 html_slide.py review --script <json> [--out <dir>] [--watch] [--wishes [<md>]]

<dir>/review.html を出す。中のスライドは deck と同じ section・CSS・素材（deck.build_html）で、同じフォルダの deck.html・assets/ に
も書く（検査はこの deck.html を縮小・折り畳みなしで開いて行う）。review.html は 1 枚で完結しない:
- 画像は出力フォルダの assets/ を相対パスで読む（フォルダごと動かせば付いてくる）
- 字体は、生成した機械のファイルを絶対の file URL で読む（deck.html と同じ。deck.font_faces）。出力フォルダには字体を入れない。
  別の機械へ渡すと、その機械に同じ字体のファイルが無ければ使えず、改行・配置が変わりうる。渡し先で字体を準備して作り直す

- ページ直下の `_memo`（メモ）・`_wish`（見せ方の希望）は文字列、`_ok` は真偽値。deck は読まない（出力は変わらない）
- ページごとの誤りで止めない。そのページには理由を出す枠を置き、残りを描く。終了コードは、書き終えてから返す
  （0 = 誤りなし / 2 = 台本・ページの誤り・検査の違反 / 4 = Node・Chrome が足りない / 1 = 書き出しの失敗）
- --wishes は全ページの `_wish` を台本順の Markdown に書き出す
- --watch は台本のバイト列が変わるたびに作り直す（ブラウザは再読み込みで反映）
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True   # 置いた場所（配る物の中・ほかの環境からのリンク先）に __pycache__ を残さない
sys.path.insert(0, str(HERE))

import browser as B  # noqa: E402
import deck  # noqa: E402
import oscompat as OS  # noqa: E402
import outputs as OUT  # noqa: E402
import render as R  # noqa: E402

TEMPLATE = HERE.parent / "templates" / "review.html"
PROBE = HERE / "review_probe.mjs"
POLL = 0.5          # --watch が台本を見に行く間隔（秒）
SETTLE = 0.3        # 変わってから、保存が終わるのを待つ（秒）
NOTE_KEYS = ("_memo", "_wish", "_ok")   # ページ直下の、レビュー用の予約名（ADR 0028）


def esc(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def md_esc(s) -> str:
    """Markdown の見出し・箇条書きに入れる文字。制御に使う記号は、文字として出す。"""
    return re.sub(r"([\\`*_{}\[\]<>#|~!])", r"\\\1", str(s))


# --- 台本のレビュー用の注記 -----------------------------------------------------------------

def notes_of(spec) -> tuple[dict, list[str]]:
    """ページの `_memo`・`_wish`・`_ok`。(注記, 型の誤り)。省略・空文字は無いのと同じ。誤りは注記に入れない。"""
    out = {"memo": "", "wish": "", "ok": False}
    bad = []
    if not isinstance(spec, dict):
        return out, bad
    for key, name in (("_memo", "memo"), ("_wish", "wish")):
        if key not in spec:
            continue
        v = spec[key]
        if isinstance(v, str):
            out[name] = v.strip("\n") if v.strip() else ""
        else:
            bad.append(f"{key} は文字で書く（{'null' if v is None else type(v).__name__} が書いてある。使わないならキーごと外す）")
    if "_ok" in spec:
        v = spec["_ok"]
        if isinstance(v, bool):
            out["ok"] = v
        else:
            bad.append(f"_ok は true か false で書く（{'null' if v is None else type(v).__name__} が書いてある。使わないならキーごと外す）")
    return out, bad


def wishes_markdown(pages: list[dict], title: str) -> str:
    """全ページの希望を台本順に。希望の無いページも載せる（「希望なし」）。"""
    L = [f"# 見せ方の希望 — {md_esc(title)}", ""]
    for p in pages:
        L.append(f"## {p['no']}ページ — {md_esc(p['label'])}")
        if p.get("id"):
            L.append(f"- ID: {md_esc(p['id'])}")
        if p["wish"]:
            L.append("- 希望:")
            L += [f"  {md_esc(x)}" if x else "" for x in p["wish"].split("\n")]
        else:
            L.append("- 希望: 希望なし")
        L.append("")
    return "\n".join(L)


# --- 検査 --------------------------------------------------------------------------------

def probe(deck_html: Path) -> dict:
    node, _ = B.node()
    if not node:
        raise R.RenderError(f"検査には node が要る。{B.SETUP_HINT}", 4)
    r = subprocess.run([node, str(PROBE), str(deck_html)], capture_output=True, text=True, timeout=600,
                       env=B.child_env(), encoding="utf-8")
    if r.returncode != 0:
        raise R.RenderError(f"はみ出しの検査を実行できなかった: {r.stderr.strip()[:300]}", 4 if r.returncode == 4 else 1)
    return json.loads(r.stdout.strip().splitlines()[-1])


def split_doc(html: str) -> tuple[str, list[str]]:
    """deck.html を、<body> の手前までと、1 ページ 1 section に分ける。"""
    head, _, body = html.partition("<body>")
    return head, re.findall(r"<section data-slide.*?</section>", body, flags=re.S)


# --- 組み立て ----------------------------------------------------------------------------

def collect(script: Path, out: Path, a) -> dict:
    """台本から、review の材料（ページごとの注記・検査・section）を集める。台本全体の誤りは DeckError。"""
    sc = deck.load_script(script)
    specs = sc["slides"]
    page_errors: dict = {}
    # 描く前の絵が無い生成の枠は仮の枠にする（review は画像を生成しない）。持ち込みの絵が無いページは、そのページの誤り
    html_path, info = deck.build_html(script, out, theme=a.theme, mode=a.mode, placeholder="generated", style=a.style,
                                      logo=a.logo, logo_position=a.logo_position, watermark=a.watermark,
                                      image_look=a.image_look, page_errors=page_errors)
    head, sections = split_doc(html_path.read_text(encoding="utf-8"))
    if len(sections) != len(specs):
        raise deck.DeckError(f"組んだページの数（{len(sections)}）が台本（{len(specs)}）と合わない")
    vs = R.check(html_path)
    pr = probe(html_path)
    pages = []
    for no, spec in enumerate(specs, 1):
        notes, bad = notes_of(spec)
        pages.append({"no": no, "label": OUT.label_of(spec) if isinstance(spec, dict) else "", "id": (spec.get("id") if isinstance(spec, dict) else None),
                      "type": spec.get("type") if isinstance(spec, dict) else None, "section": sections[no - 1],
                      "memo": notes["memo"], "wish": notes["wish"], "ok": notes["ok"], "checks": [], "failed": no in page_errors})
        for m in bad:
            pages[-1]["checks"].append({"level": "error", "rule": "注記の型", "el": "", "detail": m})
        if no in page_errors:
            pages[-1]["checks"].append({"level": "error", "rule": "描画不能", "el": "", "detail": page_errors[no]})
    doc = []
    if 0 in page_errors:
        doc.append({"level": "error", "rule": "共通設定", "el": "", "detail": page_errors[0]})
    for v in vs:
        n = v.get("slide") or 0
        item = {"level": v.get("level", "error"), "rule": v["rule"], "el": v.get("el") or "", "detail": v["detail"]}
        if n == 0:
            doc.append(item)
        elif 1 <= n <= len(pages) and not pages[n - 1]["failed"]:     # 組めなかったページの代わりの枠は検査しない
            pages[n - 1]["checks"].append(item)
    for o in pr["overflow"]:
        p = pages[o["slide"] - 1]
        if not p["failed"]:
            p["checks"].append({"level": "warn", "rule": "はみ出し" if o["kind"] == "overflow" else "画面の外", "el": o["label"],
                                "detail": o["detail"]})
    for h in pr["headlines"]:
        if h["lines"] >= 2 and not pages[h["slide"] - 1]["failed"]:
            pages[h["slide"] - 1]["checks"].append({"level": "info", "rule": "見出しの折り返し", "el": "", "detail": f"{h['lines']} 行"})
    for w in info.get("warnings") or []:
        m = re.match(r"(\d+)枚目", w)
        target = pages[int(m.group(1)) - 1]["checks"] if m and 1 <= int(m.group(1)) <= len(pages) else doc
        target.append({"level": "warn", "rule": "警告", "el": "", "detail": w})
    for x in info.get("images") or []:
        if x["origin"] == "generated" and not x["exists"] and 1 <= x["slide"] <= len(pages):
            pages[x["slide"] - 1]["checks"].append({"level": "warn", "rule": "画像未生成", "el": x["slot"],
                                                   "detail": "仮の枠で描いている（review は画像を生成しない）"})
    return {"title": info["title"], "mode": info["mode"], "head": head, "pages": pages, "doc": doc, "info": info}


def errors_in(data: dict) -> int:
    n = sum(1 for c in data["doc"] if c["level"] == "error")
    return n + sum(1 for p in data["pages"] for c in p["checks"] if c["level"] == "error")


LEVEL = {"error": "誤り", "warn": "警告", "info": "情報"}


def _checks_html(checks: list[dict]) -> str:
    if not checks:
        return '<p class="none">検査: 問題なし</p>'
    rows = "".join(f'<tr class="lv-{c["level"]}"><td>{LEVEL[c["level"]]}</td><td>{esc(c["rule"])}</td>'
                   f'<td>{esc(c["el"])}</td><td>{esc(c["detail"])}</td></tr>' for c in checks)
    return ('<table class="checks"><thead><tr><th>段階</th><th>規則</th><th>対象</th><th>理由</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>")


def _badges(p: dict) -> str:
    err = sum(1 for c in p["checks"] if c["level"] == "error")
    warn = sum(1 for c in p["checks"] if c["level"] == "warn")
    out = []
    if p["ok"]:
        out.append('<span class="badge ok">OK</span>')
    if err:
        out.append(f'<span class="badge err">誤り {err}</span>')
    if warn:
        out.append(f'<span class="badge warn">警告 {warn}</span>')
    return "".join(out)


def page_html(head: str, p: dict) -> str:
    """1 ページ分。スライドは、共通の head（CSS・字体）と、そのページの section だけの文書として iframe の srcdoc に入れる。"""
    doc = f"{head}<body>\n{p['section']}\n  </body>\n</html>\n"
    memo = f'<div class="text">{esc(p["memo"])}</div>' if p["memo"] else '<p class="none">なし</p>'
    wish = f'<div class="text">{esc(p["wish"])}</div>' if p["wish"] else '<p class="none">なし</p>'
    ident = f' <span class="id">{esc(p["id"])}</span>' if p.get("id") else ""
    return (f'<details class="page{" is-ok" if p["ok"] else ""}" id="p{p["no"]}"{"" if p["ok"] else " open"}>\n'
            f'<summary><span class="no">{p["no"]}</span> <span class="label">{esc(p["label"])}</span>{ident} {_badges(p)}</summary>\n'
            f'<div class="body"><div class="stage"><iframe title="{p["no"]}ページ" width="1920" height="1080" scrolling="no" '
            f'srcdoc="{esc(doc)}"></iframe></div>\n'
            f'<div class="side"><h3>メモ</h3>{memo}<h3>見せ方の希望</h3>{wish}<h3>検査結果</h3>{_checks_html(p["checks"])}</div></div>\n'
            f"</details>")


def review_html(data: dict, stamp: str) -> str:
    pages = data["pages"]
    toc = "".join(f'<li><a href="#p{p["no"]}"><span class="no">{p["no"]}</span> {esc(p["label"])}</a> {_badges(p)}</li>' for p in pages)
    doc = (f'<section class="docchecks"><h2>全体の検査</h2>{_checks_html(data["doc"])}</section>' if data["doc"] else "")
    n_ok = sum(1 for p in pages if p["ok"])
    n_err = sum(1 for p in pages if any(c["level"] == "error" for c in p["checks"]))
    summary = f"{len(pages)} ページ・OK {n_ok}・誤りのあるページ {n_err}"
    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("{{TITLE}}", esc(data["title"]))
            .replace("{{SUMMARY}}", esc(summary))
            .replace("{{STAMP}}", esc(stamp))
            .replace("{{TOC}}", toc)
            .replace("{{DOCCHECKS}}", doc)
            .replace("{{PAGES}}", "\n".join(page_html(data["head"], p) for p in pages)))


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    OS.replace(tmp, path)


def generate(script: Path, out: Path, a, wishes: Path | None) -> int:
    """1 回分。書き出して、終了コードを返す。台本全体の誤り・Chrome の不足では、前の成果物を触らない。"""
    out.mkdir(parents=True, exist_ok=True)
    try:
        data = collect(script, out, a)
    except deck.DeckError as e:
        print(f"NG: {e}", file=sys.stderr)
        return 2
    except R.RenderError as e:
        print(f"NG: {e}", file=sys.stderr)
        return e.rc
    try:
        write_atomic(out / "review.html", review_html(data, time.strftime("%Y-%m-%d %H:%M:%S")))
        if wishes:
            wishes.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(wishes, wishes_markdown(data["pages"], data["title"]))
    except OSError as e:
        print(f"NG: 書き出せなかった: {e}", file=sys.stderr)
        return 1
    for c in data["doc"]:
        print(f"{'NG' if c['level'] == 'error' else '警告'}: 全体: [{c['rule']}] {c['detail']}", file=sys.stderr)
    for p in data["pages"]:
        for c in p["checks"]:
            if c["level"] != "info":
                print(f"{'NG' if c['level'] == 'error' else '警告'}: {p['no']}枚目: [{c['rule']}] {c['el']} {c['detail']}".replace("  ", " "), file=sys.stderr)
    n = errors_in(data)
    print(f"review: {out / 'review.html'}（{len(data['pages'])} ページ・誤り {n} 件）")
    if wishes:
        print(f"wishes: {wishes}")
    return 2 if n else 0


def watch(script: Path, out: Path, a, wishes: Path | None, *, stop=None, poll: float = POLL, settle: float = SETTLE) -> int:
    """台本のバイト列のハッシュが変わるたびに作り直す。生成中の変更は、終わってから最新を作る。
    壊れた台本・Chrome の不足は端末に出して、直前の成果物を残したまま見続ける。stop（呼び出し可能）が真を返したら終わる。"""
    last = None
    rc = 0
    print("台本を見ている（Ctrl+C で終わる）", file=sys.stderr)
    try:
        while not (stop and stop()):
            try:
                cur = hashlib.sha256(script.read_bytes()).hexdigest()
            except OSError:
                cur = None          # 保存の入れ替えの途中。次の回で読む
            if cur is not None and cur != last:
                time.sleep(settle)  # 保存が続くあいだはまとめる
                try:
                    cur = hashlib.sha256(script.read_bytes()).hexdigest()
                except OSError:
                    continue
                last = cur
                rc = generate(script, out, a, wishes)
                continue            # 生成中に変わっていたら、すぐ次の回で最新を作る
            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    return rc
