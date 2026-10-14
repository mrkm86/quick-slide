"""依頼・直しの指示に書かれたページの URL から、本文中の画像を集める（docs/adr/0016-page-images.md）。

- 集めるのは、指示に**ページの URL と「画像・写真・図」などの言葉が両方ある**ときだけ（使うかどうかは台本係が指示から決める）
- ページの HTML を取り、本文（`<article>` か `<main>` があればその中）の画像を、近くの見出し・代替テキスト・図の説明と一緒に拾う
- 取り込みは URL の貼り付けと同じ決まり（`server/image_fetch`。公開のアドレスだけ・転送先も確かめる・大きさの上限）。
  形式（PNG・JPEG・WebP）・画素数・動かないことを確かめ、小さい画像（アイコン・ロゴ）は除く
- 取り込んだ画像は、デッキの台本のフォルダの `attachments/` に置く（台本からの相対 `attachments/<鍵>.<拡張子>`。複製でも写る）
- 使った画像には出典（ページの名前と URL）を書く決まり（台本係への指示。`writer/prompt.py`）
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

log = logging.getLogger("quick_slide.page_images")

URL_RE = re.compile(r"https?://[^\s<>\"'「」『』（）()、。]+")
IMAGE_WORDS = re.compile(r"画像|写真|図|イラスト|挿絵|image|photo|picture", re.I)
MAX_PAGES = 3                      # 1 つの指示から見るページの数
MAX_PAGE_BYTES = 5 * 1024 * 1024   # ページの HTML
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 10                    # 1 つの指示で取り込む画像の数
MAX_CANDIDATES = 30                # 試す画像の数（小さい画像を除くので多めに）
MIN_SIDE = 240                     # これより小さい辺を持つ画像（アイコン・ロゴ・飾り）は除く
MAX_PIXELS = 25_000_000
FORMATS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
_SKIP_NAMES = re.compile(r"logo|icon|avatar|sprite|favicon|badge|banner-ad|/ads?/|pixel|spacer|loading", re.I)


def wanted_urls(text: str | None) -> list[str]:
    """指示がページの画像を求めているなら、そのページの URL（最大 `MAX_PAGES`）。求めていなければ空。"""
    if not text or not IMAGE_WORDS.search(text):
        return []
    out: list[str] = []
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?）)」』")
        if url not in out:
            out.append(url)
    return out[:MAX_PAGES]


class _Page(HTMLParser):
    """ページの題と、本文の画像の候補（src・alt・近くの見出し・図の説明）。"""

    _HEAD = {"h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.og_title = ""
        self.in_title = False
        self.body_depth = 0          # <article>・<main> の中か
        self.heading = ""
        self.in_heading = False
        self.figure: dict[str, Any] | None = None
        self.in_caption = False
        self.images: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "title":
            self.in_title = True
        elif tag == "meta" and a.get("property") == "og:title":
            self.og_title = a.get("content", "")
        elif tag in ("article", "main"):
            self.body_depth += 1
        elif tag in self._HEAD:
            self.in_heading, self.heading = True, ""
        elif tag == "figure":
            self.figure = {"images": []}
        elif tag == "figcaption":
            self.in_caption = True
        elif tag in ("img", "source"):
            src = a.get("data-src") or a.get("data-lazy-src") or a.get("src") or ""
            srcset = a.get("data-srcset") or a.get("srcset") or ""
            if srcset:   # 一番大きい候補
                best = max(((part.strip().split(" ")[0], part.strip().split(" ")[-1]) for part in srcset.split(",") if part.strip()),
                           key=lambda x: float(re.sub(r"[^\d.]", "", x[1]) or 0), default=None)
                if best and best[0]:
                    src = best[0]
            if not src or src.startswith("data:") or src.lower().endswith(".svg"):
                return
            img = {"src": src, "alt": a.get("alt", "").strip(), "heading": self.heading.strip(), "caption": "",
                   "in_body": self.body_depth > 0}
            self.images.append(img)
            if self.figure is not None:
                self.figure["images"].append(img)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif tag in ("article", "main") and self.body_depth:
            self.body_depth -= 1
        elif tag in self._HEAD:
            self.in_heading = False
        elif tag == "figcaption":
            self.in_caption = False
        elif tag == "figure":
            self.figure = None

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        if self.in_heading:
            self.heading += data
        if self.in_caption and self.figure is not None:
            for img in self.figure["images"]:
                img["caption"] = (img["caption"] + data).strip()[:200]


def candidates(html: str, base: str) -> tuple[str, list[dict[str, Any]]]:
    """ページの題と、本文の画像の候補（絶対の URL・重なりを除く・ロゴやアイコンらしい名前を除く）。"""
    p = _Page()
    p.feed(html)
    title = (p.og_title or p.title).strip()[:200]
    body = [i for i in p.images if i["in_body"]]
    out, seen = [], set()
    for img in body or p.images:
        url = urljoin(base, img["src"])
        if not url.startswith(("http://", "https://")) or url in seen or _SKIP_NAMES.search(url):
            continue
        seen.add(url)
        out.append({**img, "url": url})
    return title, out[:MAX_CANDIDATES]


def _check_image(data: bytes) -> str | None:
    """取り込んでよい画像なら拡張子。だめなら None（形式・画素数・小ささ・動く画像）。"""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt, (w, h) = im.format, im.size
            if fmt not in FORMATS or w * h > MAX_PIXELS or min(w, h) < MIN_SIDE or getattr(im, "n_frames", 1) != 1:
                return None
            im.verify()
        return FORMATS[fmt]
    except Exception:   # 壊れた・読めない画像は黙って除く
        return None


def collect(text: str | None, folder: Path, *, fetch=None) -> list[dict[str, Any]]:
    """指示に書かれたページの本文の画像を、`folder`（台本のフォルダの attachments/）に取り込む。

    戻り値は {rel（台本からの相対）, alt, heading, caption, page_title, page_url} の並び。ページや画像を取れなくても止めない
    （取れた分だけ返す。取れなかったことはログに残す）。`fetch` は試験が差し替える口（`image_fetch.fetch` と同じ形）。"""
    from .server import image_fetch
    from .server.errors import ApiError

    get = fetch or image_fetch.fetch
    out: list[dict[str, Any]] = []
    for page_url in wanted_urls(text):
        try:
            html_bytes, final = get(page_url, max_bytes=MAX_PAGE_BYTES, accept="text/html,application/xhtml+xml")
        except ApiError as exc:
            log.info("ページの画像: ページを取れなかった（%s）", exc.code)
            continue
        title, cands = candidates(html_bytes.decode("utf-8", errors="replace"), final)
        for c in cands:
            if len(out) >= MAX_IMAGES:
                return out
            try:
                data, _ = get(c["url"], max_bytes=MAX_IMAGE_BYTES, accept="image/*")
            except ApiError:
                continue
            ext = _check_image(data)
            if ext is None:
                continue
            name = f"page-{hashlib.sha256(data).hexdigest()[:16]}{ext}"
            if not (folder / name).is_file():
                folder.mkdir(parents=True, exist_ok=True)
                (folder / name).write_bytes(data)
            if any(o["rel"] == f"attachments/{name}" for o in out):
                continue
            out.append({"rel": f"attachments/{name}", "alt": c["alt"][:120], "heading": c["heading"][:120],
                        "caption": c["caption"][:200], "page_title": title or page_url, "page_url": page_url})
    return out


def describe(pictures: list[dict[str, Any]]) -> list[str]:
    """台本係に渡す「使ってよい画像」の行（path と、何の画像か・出典）。"""
    lines = []
    for p in pictures:
        what = "・".join(x for x in (p.get("caption"), p.get("alt"), p.get("heading") and f"見出し「{p['heading']}」の近く") if x)
        lines.append(f"- {p['rel']}（{what or '説明なし'}。出典: {p['page_title']} {p['page_url']}）")
    return lines
