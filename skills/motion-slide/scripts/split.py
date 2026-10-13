"""今の台本 HTML（templates/deck.html の形）→ 場面ごとの台本（JSON）に分ける。

    python3 -E split.py <deck.html> --out <script.json>

台本係が書く形（references/deck-format.md）の手本にもなる。分けて motion_slide.py で組み直すと、同じ index.html の見た目・動きになる
（見本2本で確かめている。tests/run.sh）。分け方:
  - 場面の <section> → 属性は id・title・beats・ch・…、中身は html、<aside class="notes"> は talk
  - <style> の規則のうち、#<場面 id> で始まるものはその場面の style、ほかは共通の style
  - 末尾の <script> の MS.hook("<場面 id>", (b, x) => {…}) はその場面の hook（本体だけ）。`/* ── <場面 id> … ── */` で始まる区画の
    定数・MS.ready はその場面の script。ほかの区画からも使われるもの・読み切れないものは、共通の script に残す
  - <div id="stage"> の中で場面の外にある要素（額縁・場面をまたぐ主役など）は stage
終了コード: 0=成功 / 2=分けられない（場面の前に要素がある・場面が無い など）
"""
import argparse
import html as htmllib
import json
import pathlib
import re
import sys
import textwrap

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import deckjson as D  # noqa: E402

SECTION = re.compile(r'<section class="scene([^"]*)"([^>]*)>(.*?)</section>', re.S)
HOOK = re.compile(r"""^MS\.hook\(\s*(["'])([\w-]+)\1\s*,\s*\(\s*b\s*,\s*x\s*\)\s*=>\s*\{""")


def fail(msg):
    print("NG: " + msg, file=sys.stderr)
    sys.exit(2)


def parse_attrs(raw):
    out = {}
    for m in re.finditer(r'([\w-]+)(?:="([^"]*)")?', raw):
        out[m.group(1)] = htmllib.unescape(m.group(2)) if m.group(2) is not None else True
    return out


def scene_of(m):
    cls, attrs = m.group(1).strip(), parse_attrs(m.group(2))
    body = m.group(3)
    for k in ("id", "data-beats", "data-title"):
        if k not in attrs:
            fail(f"場面に {k} が無い: <section{m.group(2)}>")
    s = {"id": attrs.pop("id"), "title": attrs.pop("data-title"), "beats": int(attrs.pop("data-beats"))}
    if "data-ch" in attrs:
        s["ch"] = int(attrs.pop("data-ch"))
    if attrs.pop("data-norail", None):
        s["norail"] = True
    if "data-term" in attrs:
        s["term"] = attrs.pop("data-term")
    if "data-dur" in attrs:
        s["dur"] = [int(float(v)) if float(v) == int(float(v)) else float(v) for v in attrs.pop("data-dur").split(",") if v.strip()]
    if "data-thumb" in attrs:
        s["thumb"] = int(attrs.pop("data-thumb"))
    if cls:
        s["class"] = cls
    if attrs:
        s["attrs"] = attrs
    notes = re.search(r'\s*<aside class="notes">(.*?)</aside>', body, re.S)
    if notes:
        ps = re.findall(r"<p>(.*?)</p>", notes.group(1), re.S)
        if ps and not any("<" in p for p in ps):
            s["talk"] = [htmllib.unescape(p).strip() for p in ps]
            body = body.replace(notes.group(0), "", 1)
    s["html"] = textwrap.dedent(body.strip("\n")).rstrip()
    return s


def split_style(style, ids):
    """共通の CSS と、場面ごとの CSS に分ける"""
    shared, mine, pending = [], {}, []

    def owner(sel, text):
        parts = D.selector_parts(sel)
        got = {re.match(r"#([\w-]+)", p).group(1) if re.match(r"#([\w-]+)", p) else None for p in parts}
        return got.pop() if len(got) == 1 and next(iter(got), None) in ids else None

    for kind, text, sel in D.css_chunks(style):
        if kind == "comment":
            pending.append(text)
            continue
        who = None
        if kind == "rule":
            who = owner(sel, text)
        elif re.match(r"@(media|supports)\b", sel):
            inner = [owner(s2, t2) for k2, t2, s2 in D.css_chunks(text[text.index("{") + 1:text.rindex("}")]) if k2 == "rule"]
            who = inner[0] if inner and all(w == inner[0] for w in inner) else None
        if who:
            keep = [c for c in pending if not re.match(r"/\*\s*[─—\-=]*\s*" + re.escape(who) + r"\b", c)]   # 組み立てが付ける見出しは写さない
            mine.setdefault(who, []).extend(keep + [text])
        else:
            shared.extend(pending + [text])
        pending = []
    shared.extend(pending)
    return "\n".join(shared), {k: "\n".join(v) for k, v in mine.items()}


def hook_of(stmt):
    """MS.hook("id", (b, x) => {本体}); なら (id, 本体)。それ以外は None"""
    m = HOOK.match(stmt)
    if not m:
        return None
    start = m.end() - 1
    end = D.js_match(stmt, start)
    if end >= len(stmt) or not re.match(r"\s*\)\s*;?\s*$", stmt[end + 1:]):
        return None
    body = stmt[start + 1:end].strip("\n")
    body = body.strip() if "\n" not in body.strip() else textwrap.dedent(body).rstrip()
    return m.group(2), body


def split_script(src, ids):
    """共通の script・場面ごとの script・場面ごとの hook に分ける。読み切れなければ全部を共通に残す"""
    stmts = D.js_statements(src)
    if stmts is None:
        return src.strip(), {}, {}
    alt = "|".join(sorted((re.escape(i) for i in ids), key=len, reverse=True))
    marker = re.compile(r"/\*\s*[─—\-=*\s]*(" + alt + r")\b")
    items, cur = [], None   # items: [owner, kind, text]
    for kind, text in stmts:
        if kind == "comment":
            m = marker.match(text)
            if m:
                cur = m.group(1)   # 区画の見出し。組み立てが付け直すので写さない
                continue
            if re.match(r"/\*\s*[─—=]{2}", text):
                cur = None
            items.append([cur, "comment", text])
            continue
        h = hook_of(text)
        items.append([h[0] if h and h[0] in ids else cur, "hook" if h and h[0] in ids else "stmt", text])
    hooks = {}
    for it in items:
        if it[1] == "hook":
            who, body = hook_of(it[2])
            if who in hooks:   # 同じ場面の hook が2つ。後のものは共通に残す
                it[0], it[1] = None, "stmt"
            else:
                hooks[who] = body
    # 他の区画からも使われる名前を宣言している文は、共通に残す（場面の script は自分の中で閉じる）
    changed = True
    while changed:
        changed = False
        for it in items:
            if it[0] is None or it[1] != "stmt":
                continue
            for name in D.declared_names(it[2]):
                pat = re.compile(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])")
                if any(o is not it and (o[0] != it[0] or o[0] is None) and pat.search(o[2]) for o in items):
                    it[0], changed = None, True
                    break
    scripts, shared = {}, []
    for who, kind, text in items:
        if kind == "hook":
            continue
        (shared if who is None else scripts.setdefault(who, [])).append(text)
    # 場面の script に入ったコメントだけで中身が無いものは落とす
    scripts = {k: "\n".join(v) for k, v in scripts.items() if any(not t.lstrip().startswith(("/*", "//")) for t in v)}
    return "\n".join(shared).strip(), scripts, hooks


def split(src_html):
    title = re.search(r"<title>(.*?)</title>", src_html, re.S)
    cfg = re.search(r'<script type="application/json" id="ms-config">(.*?)</script>', src_html, re.S)
    stage_at = src_html.find('<div id="stage">')
    if not cfg or stage_at < 0:
        fail('台本に id="ms-config" の <script> と <div id="stage"> が要る')
    head, rest = src_html[:stage_at], src_html[stage_at + len('<div id="stage">'):]
    style = "\n".join(re.findall(r"<style>(.*?)</style>", head, re.S)).strip("\n")
    sm = re.search(r"<script>", rest)
    inner = rest[:rest.rfind("</div>", 0, sm.start() if sm else len(rest))]
    script_src = "\n".join(re.findall(r"<script>(.*?)</script>", rest, re.S)).strip("\n")
    # 台本 JSON から組んだ形（hook・script・共通の script は JSON に入っている）も読む
    j = lambda i: (lambda m: json.loads(m.group(1)) if m else None)(re.search(rf'<script type="application/json" id="{i}">(.*?)</script>', src_html, re.S))
    code, shared_json = j("ms-scenes") or {}, j("ms-shared")
    sections = list(SECTION.finditer(inner))
    if not sections:
        fail("<section class=\"scene\"> が1つも無い")
    first = re.sub(r"<!--.*?-->", "", inner[:sections[0].start()], flags=re.S).strip()
    between = re.sub(r"<!--.*?-->", "", "".join(inner[a.end():b.start()] for a, b in zip(sections, sections[1:])), flags=re.S).strip()
    if first or between:
        fail("場面の前・場面の間に要素がある。場面の外の要素は、最後の場面より後に置く形にしてから分ける")
    stage = re.sub(r"<!--.*?-->", "", inner[sections[-1].end():], flags=re.S).strip()
    scenes = [scene_of(m) for m in sections]
    ids = [s["id"] for s in scenes]
    shared_style, scene_style = split_style(style, set(ids))
    shared_script, scene_script, hooks = split_script(script_src, set(ids))
    if isinstance(shared_json, str) and shared_json.strip():
        shared_script = (shared_json.strip("\n") + "\n" + shared_script).strip()
    for sid, c in code.items():
        if sid in ids and isinstance(c, dict):
            if isinstance(c.get("script"), str):
                scene_script[sid] = c["script"].strip("\n")
            if isinstance(c.get("hook"), str):
                hooks[sid] = c["hook"].strip("\n")
    for s in scenes:
        if s["id"] in scene_style:
            s["style"] = D.lines_of(scene_style[s["id"]])
        if s["id"] in scene_script:
            s["script"] = D.lines_of(scene_script[s["id"]])
        if s["id"] in hooks:
            s["hook"] = D.lines_of(hooks[s["id"]])
        s["html"] = D.lines_of(s["html"])
        order = ["id", "title", "beats", "ch", "norail", "term", "dur", "thumb", "class", "attrs", "style", "html", "script", "hook", "talk"]
        s_sorted = {k: s[k] for k in order if k in s}
        s.clear()
        s.update(s_sorted)
    out = {"title": htmllib.unescape(title.group(1)) if title else "", "config": json.loads(cfg.group(1))}
    if shared_style.strip():
        out["style"] = D.lines_of(shared_style)
    if stage:
        out["stage"] = D.lines_of(textwrap.dedent(stage))
    if shared_script.strip():
        out["script"] = D.lines_of(shared_script)
    out["scenes"] = scenes
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="今の台本 HTML → 場面ごとの台本（JSON）")
    ap.add_argument("deck")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    src = pathlib.Path(a.deck)
    if not src.is_file():
        fail(f"台本が無い: {src}")
    data = split(src.read_text(encoding="utf-8"))
    out = pathlib.Path(a.out)
    if out.resolve() == src.resolve():
        fail("--out が元の台本と同じファイル")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    sc = data["scenes"]
    print(f"{out}  {len(sc)} 場面・{sum(s['beats'] for s in sc)} 拍（hook {sum('hook' in s for s in sc)}・場面の script {sum('script' in s for s in sc)}・"
          f"場面の style {sum('style' in s for s in sc)}・共通の script {'あり' if 'script' in data else 'なし'}・stage {'あり' if 'stage' in data else 'なし'}）")
    rep = D.validate(data)
    for w in rep.warn:
        print("警告: " + w, file=sys.stderr)
    for n in rep.ng:
        print("NG: " + n, file=sys.stderr)
    return 2 if rep.ng else 0


if __name__ == "__main__":
    for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932）でなく UTF-8 で出す。読むのはエージェントとサーバ
        _out.reconfigure(encoding="utf-8")
    sys.exit(main())
