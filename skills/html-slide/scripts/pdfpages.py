"""PDF のページを選んで 1 本にまとめる（標準ライブラリだけ）。`html_slide.py assemble` が使う。

書き出しの PDF は deck.html を Chrome で印刷したもの（`write_pdf`）。版の違うページを混ぜた書き出しを作るときに、
回ごとの deck.pdf から決まったページを抜いて、並べ直して 1 本にする（ADR 0024）。

読めるのは、Chrome（Skia）が書く形の PDF だけ:
- 相互参照は表（`xref`）。相互参照のストリーム・オブジェクトのストリーム（PDF 1.5 の圧縮）・暗号化は読まない
- ストリームの長さ（`/Length`）は数か、数を指す参照
読めない形に会ったら `PdfError` を投げる（呼ぶ側は混ぜずに、いまの作りで描き直した PDF を使う）。

まとめ方: 選んだページから参照をたどれるオブジェクトだけを、新しい番号で書き直す（同じ元の同じオブジェクト＝字体などは
1 回だけ書く）。ページの `/Parent` は新しいページの木へ。親から受け継ぐ属性（`/Resources`・`/MediaBox`・`/CropBox`・
`/Rotate`）はページに写す。ほかのページへの参照（リンクの行き先）は `null` にする。文書の構造（タグ）は写さない
（ページの `/StructParents` も外す）。ストリームの中身はバイトのまま写す（描く中身は 1 バイトも変えない）。
"""
from __future__ import annotations

import re
from pathlib import Path

WS = b"\x00\t\n\x0c\r "
DELIM = b"()<>[]{}/%"
INHERITED = (b"/Resources", b"/MediaBox", b"/CropBox", b"/Rotate")
_NUM = re.compile(rb"[+-]?(\d+\.?\d*|\.\d+)")
_INT = re.compile(rb"\d+")


class PdfError(Exception):
    pass


class Ref:
    __slots__ = ("num",)

    def __init__(self, num: int):
        self.num = num


class Raw:
    """数・名前・文字列・true/false/null（書いてあったバイトのまま持つ）。"""
    __slots__ = ("b",)

    def __init__(self, b: bytes):
        self.b = b


class Dict:
    __slots__ = ("items",)

    def __init__(self, items: list):
        self.items = items     # [(名前のバイト, 値)]

    def get(self, key: bytes):
        for k, v in self.items:
            if k == key:
                return v
        return None

    def set(self, key: bytes, value) -> None:
        for i, (k, _) in enumerate(self.items):
            if k == key:
                self.items[i] = (k, value)
                return
        self.items.append((key, value))

    def drop(self, key: bytes) -> None:
        self.items = [(k, v) for k, v in self.items if k != key]


class Parser:
    def __init__(self, data: bytes, pos: int = 0):
        self.d = data
        self.p = pos

    def skip(self) -> None:
        d, n = self.d, len(self.d)
        while self.p < n:
            c = d[self.p]
            if c in WS:
                self.p += 1
            elif c == 0x25:   # % コメント
                while self.p < n and d[self.p] not in b"\r\n":
                    self.p += 1
            else:
                return

    def word(self) -> bytes:
        self.skip()
        s = self.p
        while self.p < len(self.d) and self.d[self.p] not in WS and self.d[self.p] not in DELIM:
            self.p += 1
        return self.d[s:self.p]

    def value(self):
        self.skip()
        d = self.d
        if self.p >= len(d):
            raise PdfError("値の途中で終わっている")
        c = d[self.p:self.p + 1]
        if c == b"<" and d[self.p:self.p + 2] == b"<<":
            self.p += 2
            items = []
            while True:
                self.skip()
                if d[self.p:self.p + 2] == b">>":
                    self.p += 2
                    return Dict(items)
                key = self.value()
                if not isinstance(key, Raw) or not key.b.startswith(b"/"):
                    raise PdfError("辞書のキーが名前ではない")
                items.append((key.b, self.value()))
        if c == b"[":
            self.p += 1
            arr = []
            while True:
                self.skip()
                if d[self.p:self.p + 1] == b"]":
                    self.p += 1
                    return arr
                arr.append(self.value())
        if c == b"(":
            s, depth = self.p, 0
            while self.p < len(d):
                ch = d[self.p]
                if ch == 0x5C:        # \ の次の 1 文字は数えない
                    self.p += 2
                    continue
                if ch == 0x28:
                    depth += 1
                elif ch == 0x29:
                    depth -= 1
                    if depth == 0:
                        self.p += 1
                        return Raw(d[s:self.p])
                self.p += 1
            raise PdfError("文字列が閉じていない")
        if c == b"<":
            e = d.find(b">", self.p)
            if e < 0:
                raise PdfError("16 進の文字列が閉じていない")
            s, self.p = self.p, e + 1
            return Raw(d[s:self.p])
        if c == b"/":
            s = self.p
            self.p += 1
            while self.p < len(d) and d[self.p] not in WS and d[self.p] not in DELIM:
                self.p += 1
            return Raw(d[s:self.p])
        m = _NUM.match(d, self.p)
        if m:
            self.p = m.end()
            if _INT.fullmatch(m.group(0)):
                # 「番号 世代 R」なら参照
                save = self.p
                self.skip()
                m2 = _INT.match(d, self.p)
                if m2:
                    self.p = m2.end()
                    self.skip()
                    if d[self.p:self.p + 1] == b"R" and (self.p + 1 >= len(d) or d[self.p + 1] in WS + DELIM):
                        self.p += 1
                        return Ref(int(m.group(0)))
                self.p = save
            return Raw(m.group(0))
        w = self.word()
        if w in (b"true", b"false", b"null"):
            return Raw(w)
        raise PdfError(f"読めない字句: {w[:20]!r}")


class Doc:
    """1 本の PDF（Chrome が書く形）。オブジェクトは必要になったときに読む。"""

    def __init__(self, data: bytes):
        if not data.startswith(b"%PDF-"):
            raise PdfError("PDF ではない")
        self.d = data
        self.version = data[5:8]
        self.offsets: dict[int, int] = {}
        self.cache: dict[int, tuple] = {}
        at = data.rfind(b"startxref")
        if at < 0:
            raise PdfError("startxref が無い")
        trailer = None
        pos = int(Parser(data, at + 9).word())
        seen = set()
        while pos is not None and pos not in seen:
            seen.add(pos)
            t = self._xref(pos)
            trailer = trailer or t
            prev = t.get(b"/Prev")
            pos = int(prev.b) if isinstance(prev, Raw) else None
        if trailer is None or trailer.get(b"/Encrypt") is not None:
            raise PdfError("暗号化された PDF は読まない")
        root = trailer.get(b"/Root")
        if not isinstance(root, Ref):
            raise PdfError("/Root が無い")
        self.root = root.num
        self.pages: list[tuple[int, dict]] = []   # (ページのオブジェクト番号, 受け継ぐ属性)
        cat = self.obj(root.num)[0]
        pages = cat.get(b"/Pages") if isinstance(cat, Dict) else None
        if not isinstance(pages, Ref):
            raise PdfError("ページの木が無い")
        self._walk(pages.num, {}, set())

    def _xref(self, pos: int) -> Dict:
        p = Parser(self.d, pos)
        if p.word() != b"xref":
            raise PdfError("相互参照が表ではない（PDF 1.5 の圧縮は読まない）")
        while True:
            p.skip()
            if self.d.startswith(b"trailer", p.p):
                p.p += 7
                t = p.value()
                if not isinstance(t, Dict):
                    raise PdfError("trailer が辞書ではない")
                return t
            start, count = int(p.word()), int(p.word())
            p.skip()
            for i in range(count):
                row = self.d[p.p:p.p + 20]
                p.p += 20
                off, gen, kind = row[:10], row[11:16], row[17:18]
                n = start + i
                if kind == b"n" and n not in self.offsets:
                    if int(gen) != 0:
                        raise PdfError("世代が 0 でないオブジェクトは読まない")
                    self.offsets[n] = int(off)
                elif kind != b"n" and kind != b"f":
                    raise PdfError("相互参照の行が読めない")

    def obj(self, num: int) -> tuple:
        """(値, ストリームのバイトか None)。"""
        if num in self.cache:
            return self.cache[num]
        if num not in self.offsets:
            out = (Raw(b"null"), None)
            self.cache[num] = out
            return out
        p = Parser(self.d, self.offsets[num])
        if int(p.word()) != num or int(p.word()) != 0 or p.word() != b"obj":
            raise PdfError(f"オブジェクト {num} の頭が合わない")
        val = p.value()
        p.skip()
        stream = None
        if self.d.startswith(b"stream", p.p):
            if not isinstance(val, Dict):
                raise PdfError("ストリームの前が辞書ではない")
            s = p.p + 6
            if self.d[s:s + 2] == b"\r\n":
                s += 2
            elif self.d[s:s + 1] in (b"\n", b"\r"):
                s += 1
            length = val.get(b"/Length")
            if isinstance(length, Ref):
                length = self.obj(length.num)[0]
            if not isinstance(length, Raw) or not _INT.fullmatch(length.b):
                raise PdfError("ストリームの長さが読めない")
            stream = self.d[s:s + int(length.b)]
            if b"endstream" not in self.d[s + int(length.b):s + int(length.b) + 32]:
                raise PdfError("ストリームの長さと endstream が合わない")
        out = (val, stream)
        self.cache[num] = out
        return out

    def _walk(self, num: int, inherited: dict, seen: set) -> None:
        if num in seen:
            raise PdfError("ページの木が輪になっている")
        seen.add(num)
        node = self.obj(num)[0]
        if not isinstance(node, Dict):
            raise PdfError("ページの木の節が辞書ではない")
        kind = node.get(b"/Type")
        here = dict(inherited)
        for k in INHERITED:
            v = node.get(k)
            if v is not None:
                here[k] = v
        if isinstance(kind, Raw) and kind.b == b"/Pages":
            kids = node.get(b"/Kids")
            if isinstance(kids, Ref):
                kids = self.obj(kids.num)[0]
            for kid in kids if isinstance(kids, list) else []:
                if not isinstance(kid, Ref):
                    raise PdfError("ページの木の子が参照ではない")
                self._walk(kid.num, here, seen)
        elif isinstance(kind, Raw) and kind.b == b"/Page":
            self.pages.append((num, {k: v for k, v in here.items() if node.get(k) is None}))
        else:
            raise PdfError("ページの木に知らない節がある")


def _ser(v) -> bytes:
    if isinstance(v, Raw):
        return v.b
    if isinstance(v, Ref):
        return b"%d 0 R" % v.num
    if isinstance(v, Dict):
        return b"<<" + b"".join(k + b" " + _ser(x) + b"\n" for k, x in v.items) + b">>"
    if isinstance(v, list):
        return b"[" + b" ".join(_ser(x) for x in v) + b"]"
    raise PdfError("書けない値")


def page_count(path) -> int:
    return len(Doc(Path(path).read_bytes()).pages)


def assemble(picks: list[tuple[str | Path, int]], out) -> int:
    """`picks`（PDF のパス・何ページ目か（1 から））の順にページを並べた PDF を `out` に書く。戻り値はページ数。"""
    docs: dict[str, Doc] = {}
    objects: list[bytes] = []          # 新しい番号 - 3 の位置（1 = カタログ・2 = ページの木）
    numbers: dict[tuple[str, int], int] = {}
    kids: list[int] = []
    version = b"1.4"

    def renum(src: str, doc: Doc, v, pages: set):
        if isinstance(v, Ref):
            if v.num in pages:
                return Raw(b"null")     # ほかのページへの参照（リンクの行き先）は持ち込まない
            return Ref(copy(src, doc, v.num, pages))
        if isinstance(v, Dict):
            return Dict([(k, renum(src, doc, x, pages)) for k, x in v.items])
        if isinstance(v, list):
            return [renum(src, doc, x, pages) for x in v]
        return v

    def copy(src: str, doc: Doc, num: int, pages: set) -> int:
        key = (src, num)
        if key in numbers:
            return numbers[key]
        new = len(objects) + 3
        numbers[key] = new
        objects.append(b"")
        val, stream = doc.obj(num)
        val = renum(src, doc, val, pages)
        if stream is None:
            body = _ser(val)
        else:
            val.set(b"/Length", Raw(str(len(stream)).encode()))
            body = _ser(val) + b" stream\n" + stream + b"\nendstream"
        objects[new - 3] = body
        return new

    for path, no in picks:
        src = str(Path(path).resolve())
        if src not in docs:
            docs[src] = Doc(Path(src).read_bytes())
            version = max(version, docs[src].version)
        doc = docs[src]
        if not 1 <= no <= len(doc.pages):
            raise PdfError(f"{Path(path).name} に {no} ページ目が無い（{len(doc.pages)} ページ）")
        pnum, inherited = doc.pages[no - 1]
        others = {n for n, _ in doc.pages if n != pnum}
        page, _ = doc.obj(pnum)
        page = Dict(list(page.items))
        page.drop(b"/Parent")
        page.drop(b"/StructParents")
        for k, v in inherited.items():
            page.set(k, v)
        new = len(objects) + 3
        objects.append(b"")
        numbers[(src, pnum)] = new     # ページ自身への参照（注釈の /P など）は新しいページへ
        page = renum(src, doc, page, others)
        page.set(b"/Parent", Ref(2))
        objects[new - 3] = _ser(page)
        kids.append(new)

    out = Path(out)
    buf = bytearray(b"%PDF-" + version + b"\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    head = [b"<</Type /Catalog\n/Pages 2 0 R>>",
            b"<</Type /Pages\n/Count %d\n/Kids [" % len(kids) + b" ".join(b"%d 0 R" % k for k in kids) + b"]>>"]
    for i, body in enumerate(head + objects, 1):
        offsets.append(len(buf))
        buf += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(buf)
    buf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1)
    for off in offsets:
        buf += b"%010d 00000 n \n" % off
    buf += b"trailer\n<</Size %d\n/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(offsets) + 1, xref)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(bytes(buf))
    tmp.replace(out)
    return len(kids)


def page_streams(path, no: int) -> list[bytes]:
    """そのページの描く中身（`/Contents` のストリームのバイト）。試験で「1 バイトも変えずに写した」を確かめる。"""
    doc = Doc(Path(path).read_bytes())
    page = doc.obj(doc.pages[no - 1][0])[0]
    contents = page.get(b"/Contents")
    refs = contents if isinstance(contents, list) else [contents]
    return [doc.obj(r.num)[1] or b"" for r in refs if isinstance(r, Ref)]
