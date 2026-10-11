"""場面ごとの台本（JSON）の検査と、templates/deck.html の形の台本 HTML への組み立て。motion_slide.py と split.py が使う。

台本の形は references/deck-format.md。ここは形の検査（足りないキー・id の重複・拍の数・章の番号・場面をまたぐ参照・
HTML の閉じ忘れ・JS の文法）と、組み立てだけを持つ。Chrome は使わない（全拍の検査は check.py）。
"""
import html as htmllib
import json
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

TOP_KEYS = {"title", "theme", "config", "style", "stage", "script", "scenes"}
CONFIG_KEYS = {"crumb", "chapters", "dur", "terms", "ambient", "sfx", "bgmVolume"}
SCENE_KEYS = {"id", "title", "beats", "ch", "norail", "term", "dur", "thumb", "class", "attrs",
              "style", "html", "script", "hook", "talk"}
ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
FLOW_ATTRS = ("in", "hl", "dim", "st", "cin")   # この拍から効く属性。拍の数以上なら、その要素は一度も出ない


def text_of(v):
    """文字列か、行の配列（改行でつなぐ）。それ以外は None"""
    if isinstance(v, str):
        return v
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return "\n".join(v)
    return None


def lines_of(s):
    """文字列を、JSON に書き出す行の配列にする（1行なら文字列のまま）"""
    s = s.strip("\n")
    return s.split("\n") if "\n" in s else s


# --- CSS・JS の走査（最小限。文字列・コメントを飛ばして括弧の深さを数える） --------------------------

def css_chunks(src):
    """CSS を、コメント・規則・@ 規則の塊に分ける。[(kind, text, selector)]。kind: comment / rule / at"""
    out, i, n = [], 0, len(src)
    while i < n:
        if src[i].isspace():
            i += 1
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(("comment", src[i:j], ""))
            i = j
            continue
        j, depth, q = i, 0, None
        while j < n:
            c = src[j]
            if q:
                if c == "\\":
                    j += 1
                elif c == q:
                    q = None
            elif c in "\"'":
                q = c
            elif src.startswith("/*", j):
                k = src.find("*/", j + 2)
                j = (n if k < 0 else k + 2) - 1
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            elif c == ";" and depth == 0:
                j += 1
                break
            j += 1
        text = src[i:j]
        sel = text.split("{", 1)[0].strip()
        out.append(("at" if sel.startswith("@") else "rule", text, sel))
        i = j
    return out


def selector_parts(sel):
    return [p.strip() for p in re.split(r",(?![^(]*\))", sel) if p.strip()]


def scene_prefixed(sel, sid):
    """セレクタの全部が #<場面 id> で始まるか"""
    return all(re.match(r"#" + re.escape(sid) + r"(?![\w-])", p) for p in selector_parts(sel))


def _strip_nth(part):
    return re.sub(r":nth-[a-z-]+\([^)]*\)", "", part)


def strip_css_comments(src):
    """文字列の外の /* … */ を空白に替える（セレクタの途中のコメントで結合子を隠せないように）"""
    out, i, n, q = [], 0, len(src), None
    while i < n:
        c = src[i]
        if q:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 1
            elif c == q:
                q = None
        elif c in "\"'":
            q = c
            out.append(c)
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 1
            out.append(" ")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def escapes_scene(part, sid):
    """#<場面 id> の複合セレクタが、兄弟の結合子（~・+）や :has() で場面の外（後ろの場面）へ出るか"""
    i, depth, n = len("#" + sid), 0, len(part)
    while i < n:
        c = part[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif depth == 0 and (c.isspace() or c in ">+~"):
            break
        i += 1
    lead, rest = part[:i], part[i:].lstrip()
    return bool(re.search(r":has\(", lead, re.I)) or rest[:1] in ("+", "~") or bool(re.match(r"[+~]", _strip_nth(rest)))


def css_unbalanced(src):
    """コメント・文字列・括弧が閉じていない（後ろの規則を飲み込む）"""
    depth, i, n = 0, 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                return True
            i = j + 2
            continue
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            if j >= n or src[j] != c:
                return True
            i = j + 1
            continue
        depth += (c == "{") - (c == "}")
        if depth < 0:
            return True
        i += 1
    return depth != 0


def css_problems(style, sid):
    """場面の CSS の誤り。[メッセージ]。他の場面にも効く規則・外へ出る結合子・飲み込み・外部の取り込み"""
    if re.search(r"</style|<!--", style, re.I):
        return ["</style・<!-- は書けない（HTML の境界が壊れる）"]
    if css_unbalanced(style):
        return ["コメント・文字列・{ } が閉じていない（後ろの規則を飲み込む）"]
    bad = []

    def rule(sel):
        for part in selector_parts(strip_css_comments(sel)):
            if not re.match(r"#" + re.escape(sid) + r"(?![\w-])", part):
                bad.append(f"規則 `{sel}` が #{sid} で始まっていない（他の場面にも効いてしまう。#{sid} .xxx{{…}} の形にする）")
                return
            if escapes_scene(part, sid):
                bad.append(f"規則 `{sel}` が兄弟の結合子（~ +）か :has() で #{sid} の外（後ろの場面）へ効く。#{sid} の中だけを指す")
                return

    def walk(chunks):
        for kind, text, sel in chunks:
            if kind == "rule":
                rule(sel)
            elif kind == "at":
                if re.match(r"@(-webkit-)?keyframes\b", sel):
                    continue
                if re.match(r"@(media|supports)\b", sel) and "{" in text:
                    walk(css_chunks(text[text.index("{") + 1:text.rindex("}")]))
                else:
                    bad.append(f"`{sel.split('{')[0].strip()[:30]}` は使えない（使える @ 規則は @media・@supports・@keyframes）")

    walk(css_chunks(style))
    return bad


def js_skip(src, i):
    """src[i] が文字列・コメント・テンプレートの頭なら、その終わりの次の位置。そうでなければ None"""
    n, c = len(src), src[i]
    if src.startswith("//", i):
        j = src.find("\n", i)
        return n if j < 0 else j
    if src.startswith("/*", i):
        j = src.find("*/", i + 2)
        return n if j < 0 else j + 2
    if c in "\"'":
        j = i + 1
        while j < n and src[j] != c:
            j += 2 if src[j] == "\\" else 1
        return j + 1
    if c == "`":
        j = i + 1
        while j < n and src[j] != "`":
            if src[j] == "\\":
                j += 2
            elif src.startswith("${", j):
                j = js_match(src, j + 1) + 1   # ${ … } の中は式。閉じる } の次へ
            else:
                j += 1
        return j + 1
    return None


def js_match(src, i):
    """src[i] の括弧（( [ {）に対応する閉じ括弧の位置。無ければ len(src)"""
    pairs, stack, j, n = {"(": ")", "[": "]", "{": "}"}, [], i, len(src)
    while j < n:
        k = js_skip(src, j)
        if k is not None:
            j = k
            continue
        c = src[j]
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")]}":
            if stack:
                stack.pop()
            if not stack:
                return j
        elif c == "/":   # 正規表現のリテラル（直前が値でないときだけ）
            prev = src[:j].rstrip()[-1:]
            if prev == "" or prev in "(,=:[!&|?{};":
                k, in_cls = j + 1, False
                while k < n and src[k] != "\n":
                    if src[k] == "\\":
                        k += 2
                        continue
                    if src[k] == "[":
                        in_cls = True
                    elif src[k] == "]":
                        in_cls = False
                    elif src[k] == "/" and not in_cls:
                        break
                    k += 1
                if k < n and src[k] == "/":
                    j = k
        j += 1
    return n


def js_statements(src):
    """トップレベルの文に分ける。[(kind, text)]。kind: comment / stmt。文は ; か、} で閉じる文（function・if など）まで。
    読み切れなければ None（呼ぶ側は分けずに全部を共通の script に置く）"""
    out, i, n = [], 0, len(src)
    while i < n:
        if src[i].isspace():
            i += 1
            continue
        if src.startswith("/*", i) or src.startswith("//", i):
            j = js_skip(src, i)
            out.append(("comment", src[i:j]))
            i = j
            continue
        j, block_stmt = i, bool(re.match(r"(function|async\s+function|class|if|for|while|try|switch)\b|\{", src[i:i + 20]))
        while j < n:
            k = js_skip(src, j)
            if k is not None:
                j = k
                continue
            c = src[j]
            if c in "([{":
                m = js_match(src, j)
                if m >= n:
                    return None
                j = m + 1
                if c == "{" and block_stmt and not re.match(r"\s*(else|catch|finally|while)\b", src[j:j + 12]):
                    break
                continue
            if c == ";":
                j += 1
                break
            j += 1
        out.append(("stmt", src[i:j]))
        i = j
    return out


def declared_names(stmt):
    return re.findall(r"\b(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)", stmt)


# --- 検査 -------------------------------------------------------------------------------------

BANNED_TAGS = {"script": "拍の動きは hook、部品の組み立ては script に", "style": "見た目は style に", "section": "外枠はエンジンが付ける",
               "iframe": "", "object": "", "embed": "", "base": "", "meta": "", "link": "", "title": "", "textarea": "（入力欄は <input>）",
               "template": "", "noscript": "", "xmp": "", "plaintext": "", "noembed": "", "noframes": "", "frame": "", "frameset": "", "applet": ""}


class _Tags(HTMLParser):
    TRACK = {"div", "span", "svg", "g", "b", "em", "small", "strong", "i", "h1", "h2", "h3", "h4", "label", "button",
             "ul", "ol", "table", "a", "aside", "figure", "pre", "code", "defs", "text", "p", "li", "tr", "td", "th"}
    LOOSE = {"p", "li", "tr", "td", "th"}   # 閉じタグを省ける要素。閉じ忘れは数えない

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.banned = [], [], []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in BANNED_TAGS:
            self.banned.append(tag)
        if tag == "aside" and any(k == "class" and v and "notes" in v for k, v in attrs):
            self.banned.append("aside-notes")
        if tag in self.TRACK:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in BANNED_TAGS:
            self.banned.append(tag)
            return
        if tag not in self.TRACK:
            return
        if tag in self.stack:
            while self.stack[-1] != tag:
                t = self.stack.pop()
                if t not in self.LOOSE:
                    self.errors.append(f"<{t}> が閉じていない")
            self.stack.pop()
        elif tag not in self.LOOSE:
            self.errors.append(f"対応する開きが無い </{tag}>（閉じすぎ）")


def html_problems(markup):
    """場面の HTML（と stage）の誤り。禁止のタグ・閉じ忘れ・閉じすぎ・閉じていないコメント。タグ名は大文字小文字を区別しない（実際の HTML パーサーと同じ）"""
    out = []
    if "<!--" in re.sub(r"<!--.*?-->", "", markup, flags=re.S):
        out.append("<!-- が閉じていない（後ろの HTML を飲み込む）")
    p = _Tags()
    p.feed(markup)
    p.close()
    for t in dict.fromkeys(p.banned):
        if t == "aside-notes":
            out.append("<aside class=\"notes\"> は書けない（話す台本は talk に）")
        else:
            hint = BANNED_TAGS[t]
            out.append(f"<{t}> は書けない" + (f"（{hint}）" if hint else ""))
    return out + p.errors + [f"<{t}> が閉じていない" for t in p.stack if t not in _Tags.LOOSE]


def node_syntax(js, label):
    """node があれば --check で文法を見る。誤りなら 'N行目: メッセージ'、通る・node が無いなら None"""
    node = shutil.which("node")
    if not node:
        return None
    with tempfile.TemporaryDirectory(prefix="motion-slide-js-") as tmp:
        p = Path(tmp) / "x.js"
        p.write_text(js, encoding="utf-8")
        r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        return None
    err = next((ln for ln in r.stderr.splitlines() if ln.startswith(("SyntaxError", "ReferenceError"))), "文法の誤り")
    m = re.search(r"x\.js:(\d+)", r.stderr)
    return (f"{int(m.group(1))}行目: " if m else "") + err


_GAP = r"(?:\s|/\*.*?\*/)*"


def node_function(args, body):
    """エンジンが場面の hook・script を作るのと同じ new Function(args…, body) を node で試す。誤りなら 'SyntaxError: …'、通る・node が無いなら None"""
    node = shutil.which("node")
    if not node:
        return None
    with tempfile.TemporaryDirectory(prefix="motion-slide-js-") as tmp:
        p = Path(tmp) / "x.json"
        p.write_text(json.dumps({"args": args, "body": body}), encoding="utf-8")
        r = subprocess.run([node, "-e", 'const c=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"));'
                            'try{new Function(...c.args,c.body)}catch(e){console.error(e.name+": "+e.message);process.exit(1)}', str(p)],
                           capture_output=True, text=True, timeout=30)
    return None if r.returncode == 0 else (r.stderr.strip().splitlines() or ["SyntaxError"])[0]


def refers(body, name):
    """JS・CSS が name（場面 id・要素の id）を引いているか。#name のセレクタ・[id=name]、または getElementById・querySelector(All)・$・
    x.table・x.cell・x.wire・x.hand・MS.hook の第1引数。空白・コメントをはさんでも見る。表の列の鍵など、たまたま同じ文字の文字列は引いたことにしない。
    「うっかり」を止める補助で、わざと抜ける書き方（文字列をつなぐなど）は止めない（ADR 0003）"""
    n = re.escape(name)
    call = rf"""\b(?:getElementById|querySelector(?:All)?|table|cell|wire|hand|hook)["']?\]?{_GAP}\??\.?{_GAP}\({_GAP}["'`]{n}["'`]"""
    return bool(re.search(rf"""#{n}(?![\w-])|\[{_GAP}id{_GAP}={_GAP}["']?{n}\b|{call}|\${_GAP}\({_GAP}["'`]{n}["'`]""", body, re.S))


class Report:
    def __init__(self):
        self.ng, self.warn = [], []

    def add(self, msg, scene=None, warn=False):
        """scene: (番号, id) か None（台本全体）"""
        head = f"{scene[0]}枚目（id {scene[1]}）: " if scene else "台本: "
        (self.warn if warn else self.ng).append(head + msg)


def validate(data):
    """台本の形を検査して Report を返す。Chrome は使わない"""
    rep = Report()
    if not isinstance(data, dict):
        rep.add("一番外は JSON のオブジェクトにする")
        return rep
    for k in sorted(set(data) - TOP_KEYS):
        hint = "（章の名前などは config に）" if k in ("crumb", "chapters") else ""
        rep.add(f"一番外に知らないキー {k}。使えるのは {', '.join(sorted(TOP_KEYS))}{hint}")
    for k in ("title", "theme"):
        if k in data and not (isinstance(data[k], str) and data[k].strip()):
            rep.add(f"{k} は空でない文字列にする")
    cfg = data.get("config", {})
    chapters = []
    if not isinstance(cfg, dict):
        rep.add("config はオブジェクトにする")
        cfg = {}
    for k in sorted(set(cfg) - CONFIG_KEYS):
        hint = "（テーマは一番外の theme に）" if k == "theme" else ""
        rep.add(f"config に知らないキー {k}。使えるのは {', '.join(sorted(CONFIG_KEYS))}{hint}")
    if "chapters" in cfg:
        if isinstance(cfg["chapters"], list) and all(isinstance(c, str) and c for c in cfg["chapters"]):
            chapters = cfg["chapters"]
        else:
            rep.add("config.chapters は章の名前（文字列）の配列にする")
    for k, ok in (("crumb", lambda v: isinstance(v, str)), ("ambient", lambda v: isinstance(v, bool)),
                  ("sfx", lambda v: isinstance(v, bool)),
                  ("dur", lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0),
                  ("terms", lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0),
                  ("bgmVolume", lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1)):
        if k in cfg and not ok(cfg[k]):
            rep.add(f"config.{k} の型・範囲が違う（dur は ms の正の数・terms は 0 以上の整数・bgmVolume は 0〜1・ambient と sfx は true / false）")
    shared = {}
    for k in ("style", "stage", "script"):
        if k in data:
            t = text_of(data[k])
            if t is None:
                rep.add(f"{k} は文字列か、行の配列にする")
            else:
                shared[k] = t
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        rep.add("scenes は場面の配列（1つ以上）にする")
        return rep
    for msg in html_problems(shared.get("stage", "")):
        rep.add(f"stage: {msg}")
    if re.search(r"</style|<!--", shared.get("style", ""), re.I):
        rep.add("共通の style に </style・<!-- は書けない（HTML の境界が壊れる）")
    elif css_unbalanced(shared.get("style", "")):
        rep.add("共通の style のコメント・文字列・{ } が閉じていない（後ろの規則を飲み込む）")
    if "script" in shared:
        err = node_syntax(shared["script"], "script")
        if err:
            rep.add(f"共通の script の文法の誤り（{err}）")

    ids = [s.get("id") if isinstance(s, dict) else None for s in scenes]
    seen = {}
    for i, sid in enumerate(ids):
        if isinstance(sid, str) and sid in seen:
            rep.add(f"id {sid} が {seen[sid] + 1}枚目と重なっている", (i + 1, sid))
        elif isinstance(sid, str):
            seen[sid] = i
    texts = {}   # 場面 → その場面の html・style・script・hook
    owned = {}   # 場面 id → html の要素の id
    for i, s in enumerate(scenes):
        sid = s.get("id") if isinstance(s, dict) else None
        where = (i + 1, sid if isinstance(sid, str) else "?")
        if not isinstance(s, dict):
            rep.add("場面はオブジェクトにする", where)
            continue
        for k in sorted(set(s) - SCENE_KEYS):
            hint = "（拍の数は beats・題は title・属性は attrs に）" if k.startswith("data-") else ""
            rep.add(f"知らないキー {k}。使えるのは {', '.join(sorted(SCENE_KEYS))}{hint}", where)
        if not (isinstance(sid, str) and ID_RE.match(sid)) or sid.startswith("ms-"):
            rep.add("id は英小文字で始まる英小文字・数字・ハイフン（MS.hook と section の id になる。ms- で始めない）", where)
            continue
        if not (isinstance(s.get("title"), str) and s["title"].strip()):
            rep.add("title（data-title）が要る。パンくず・章のレール・一覧に出る", where)
        beats = s.get("beats")
        if not (isinstance(beats, int) and not isinstance(beats, bool) and beats >= 1):
            rep.add("beats（拍の数）は 1 以上の整数にする", where)
            beats = None
        if "ch" in s:
            ch = s["ch"]
            if not (isinstance(ch, int) and not isinstance(ch, bool) and ch >= 0):
                rep.add("ch（章の番号・0始まり）は 0 以上の整数にする", where)
            elif not chapters:
                rep.add("config.chapters が空なのに ch がある（章を使うなら config に章の名前を書く）", where)
            elif ch >= len(chapters):
                rep.add(f"ch={ch} は章の外（config.chapters は {len(chapters)} つ・0〜{len(chapters) - 1}）", where)
        if "norail" in s and not isinstance(s["norail"], bool):
            rep.add("norail は true / false にする", where)
        if "term" in s and not (isinstance(s["term"], str) and s["term"].strip()):
            rep.add("term は空でない文字列にする", where)
        if "dur" in s:
            d = s["dur"]
            if not (isinstance(d, list) and all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in d)):
                rep.add("dur は拍ごとの長さ（ms）の数の配列にする", where)
            elif beats and len(d) != beats:
                rep.add(f"dur の数（{len(d)}）が beats={beats} と合わない", where)
        if "thumb" in s:
            t = s["thumb"]
            if not (isinstance(t, int) and not isinstance(t, bool) and t >= 0):
                rep.add("thumb（一覧に撮る拍・0始まり）は 0 以上の整数にする", where)
            elif beats and t >= beats:
                rep.add(f"thumb={t} は拍の外（0 始まり・beats={beats}）", where)
        if "class" in s and not (isinstance(s["class"], str) and re.match(r"^[\w -]+$", s["class"])):
            rep.add("class は追加のクラス名（空白区切りの文字列）にする", where)
        if "attrs" in s:
            a = s["attrs"]
            if not (isinstance(a, dict) and all(isinstance(k, str) and re.match(r"^(data-[\w-]+|style)$", k) and
                                                 (isinstance(v, (str, bool))) for k, v in a.items())):
                rep.add("attrs は data-* か style の属性の辞書（値は文字列か true）にする", where)
        got = {}
        for k in ("html", "style", "script", "hook"):
            if k in s:
                t = text_of(s[k])
                if t is None:
                    rep.add(f"{k} は文字列か、行の配列にする", where)
                else:
                    got[k] = t
        if not got.get("html", "").strip():
            rep.add("html（場面の中身）が要る。<section> の外枠はエンジンが付ける", where)
        h = got.get("html", "")
        for msg in html_problems(h)[:4]:
            rep.add(f"html: {msg}", where)
        if beats:
            for attr in FLOW_ATTRS:
                for v in re.findall(rf'\bdata-{attr}="(\d+)"', h):
                    if int(v) >= beats:
                        rep.add(f'data-{attr}="{v}" は拍の外（0 始まり・beats={beats}）。この要素は一度も効かない', where)
                        break
        if "talk" in s:
            t = s["talk"]
            if not (isinstance(t, list) and all(isinstance(v, str) and v.strip() for v in t)):
                rep.add("talk は話す台本（拍ごとの文字）の配列にする", where)
            elif t and beats and len(t) != beats:
                rep.add(f"talk の数（{len(t)}）が beats={beats} と合わない（拍ごとに1つ。話さない拍は書かずに全体を省く）", where)
        if "hook" in got:
            if re.search(r"\bMS\.hook\s*\(", got["hook"]):
                rep.add("hook に MS.hook(…) は書かない。(b, x) => { … } の本体だけを書く（組み立てが MS.hook を付ける）", where)
            elif (err := node_function(["b", "x"], got["hook"])):
                rep.add(f"hook の文法の誤り（{err}）。hook は (b, x) => {{ … }} の本体だけで、本体の外へ出る書き方（}} で閉じる）もここで落ちる", where)
        if "script" in got:
            if re.search(rf"\bMS\.hook\s*\(\s*[\"']{re.escape(sid)}[\"']", got["script"]):
                rep.add("script に MS.hook は書かない（拍の動きは hook に）", where)
            elif (err := node_function(["MS"], got["script"])):
                rep.add(f"script の文法の誤り（{err}）", where)
        if "style" in got:
            for msg in css_problems(got["style"], sid)[:3]:
                rep.add(f"style の{msg}", where)
        owned[sid] = set(re.findall(r"""\bid=["']([^"']+)["']""", h))
        texts[sid] = (where, got)

    # 場面をまたぐ参照・要素の id の重なり
    ids_ok = [s for s in ids if isinstance(s, str) and s in texts]
    stage_ids = set(re.findall(r"""\bid=["']([^"']+)["']""", shared.get("stage", "")))
    for a, (where, got) in texts.items():
        mine = owned[a]
        for k in ("hook", "script", "style"):
            body = got.get(k, "")
            if not body:
                continue
            for o in ids_ok:
                if o == a:
                    continue
                if refers(body, o):
                    rep.add(f"{k} が別の場面（id {o}）を引いている。場面の中で閉じる（別の場面の要素・id を使わない）", where)
                    break
            else:
                for o in ids_ok:
                    if o == a:
                        continue
                    for el in sorted(owned[o] - mine - stage_ids):
                        if refers(body, el):
                            rep.add(f"{k} が別の場面（id {o}）の要素 #{el} を引いている。場面の中で閉じる", where)
                            break
                    else:
                        continue
                    break
        for m in re.finditer(rf"\bMS{_GAP}\.{_GAP}go{_GAP}\({_GAP}(\d+)", got.get("hook", "") + got.get("script", "")):
            if int(m.group(1)) != ids.index(a):
                rep.add(f"MS.go({m.group(1)}, …) が別の場面を指している（この場面は {ids.index(a)}。自分なら MS.state.S）", where)
                break
    by_el = {}
    for a in ids_ok:
        for el in owned[a]:
            by_el.setdefault(el, []).append(a)
    for el, owners in sorted(by_el.items()):
        if len(owners) > 1:
            rep.add(f"要素の id=\"{el}\" が場面 {'・'.join(owners)} で重なっている（場面ごとに変える）", texts[owners[1]][0])
        elif el in stage_ids:
            rep.add(f"要素の id=\"{el}\" が stage と重なっている", texts[owners[0]][0])
    for a, (where, got) in texts.items():
        if re.search(r"\bMS\.hook\s*\(\s*[\"']" + re.escape(a) + r"[\"']", shared.get("script", "")) and "hook" in got:
            rep.add("共通の script にも MS.hook がある（この場面の hook と二重になる）", where)
    return rep


# --- 組み立て ---------------------------------------------------------------------------------

def _indent(s, n):
    pad = " " * n
    return "\n".join(pad + ln if ln.strip() else ln for ln in s.strip("\n").split("\n"))


def _attr(v):
    return htmllib.escape(str(v), quote=True)


def scene_tag(s):
    cls = "scene" + (" " + s["class"].strip() if s.get("class") else "")
    a = [f'class="{_attr(cls)}"', f'id="{s["id"]}"', f'data-beats="{s["beats"]}"']
    if "ch" in s:
        a.append(f'data-ch="{s["ch"]}"')
    a.append(f'data-title="{_attr(s["title"])}"')
    if s.get("norail"):
        a.append("data-norail")
    if s.get("term"):
        a.append(f'data-term="{_attr(s["term"])}"')
    if "dur" in s:
        a.append('data-dur="' + ",".join(f"{v:g}" if isinstance(v, float) else str(v) for v in s["dur"]) + '"')
    if "thumb" in s:
        a.append(f'data-thumb="{s["thumb"]}"')
    for k, v in (s.get("attrs") or {}).items():
        a.append(k if v is True else f'{k}="{_attr(v)}"')
    return "<section " + " ".join(a) + ">"


def esc_json(obj):
    """<script type="application/json"> に入れる JSON。< > & と U+2028/2029 を \\u エスケープで逃がす（</script> などで HTML の境界を壊さない）"""
    t = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    for ch, rep in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        t = t.replace(ch, rep)
    return t


def assemble(data):
    """検査を通った台本（JSON）→ templates/deck.html の形の台本 HTML。
    場面の hook・script は HTML の <script> に直に埋めず、JSON（id="ms-scenes"）に入れる。エンジンが new Function で場面ごとに作る
    （本体の外へ出る書き方・文法の誤りはその場面だけで落ち、他の場面や共通部に届かない）"""
    cfg = data.get("config") or {}
    title = data.get("title") or "台本"
    style = [t for t in [text_of(data.get("style"))] if t and t.strip()]
    shared_js = text_of(data.get("script"))
    body, code = [], {}
    for s in data["scenes"]:
        st = text_of(s.get("style"))
        if st and st.strip():
            style.append(f'/* ── {s["id"]} ── */\n{st.strip()}')
        c = {k: text_of(s[k]) for k in ("script", "hook") if text_of(s.get(k)) and text_of(s[k]).strip()}
        if c:
            code[s["id"]] = c
        notes = ""
        if s.get("talk"):
            notes = '\n    <aside class="notes">' + "".join(f"<p>{htmllib.escape(t, quote=False)}</p>" for t in s["talk"]) + "</aside>"
        body.append(f"  {scene_tag(s)}\n{_indent(text_of(s['html']), 4)}{notes}\n  </section>")
    stage = text_of(data.get("stage"))
    scenes_tag = f'<script type="application/json" id="ms-scenes">{esc_json(code)}</script>\n' if code else ""
    if shared_js and shared_js.strip():   # 共通の script も JSON で持つ。JS の文脈（文字列・正規表現・コメント）に依らず HTML の境界を壊さない
        scenes_tag += f'<script type="application/json" id="ms-shared">{esc_json(shared_js)}</script>\n'
    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{htmllib.escape(title, quote=False)}</title>\n"
        f'<script type="application/json" id="ms-config">{esc_json(cfg)}</script>\n{scenes_tag}'
        "<style>\n" + "\n\n".join(style) + "\n</style>\n</head>\n<body>\n<div id=\"stage\">\n\n"
        + "\n\n".join(body)
        + ("\n\n" + _indent(stage, 2) if stage and stage.strip() else "")
        + "\n</div>\n</body>\n</html>\n"
    )


def load(path):
    """JSON を読む。読めなければ (None, 'メッセージ')"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except OSError as e:
        return None, f"台本を読めない: {path}（{e.strerror}）"
    except ValueError as e:
        return None, f"台本が JSON として読めない: {e}"
