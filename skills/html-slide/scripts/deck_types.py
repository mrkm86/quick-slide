"""資料の型。座標は型が決め、台本は中身だけを書く。

型ごとに「中身の検査・文字の大きさの決定・HTML の組み立て・動きの既定」を持つ。PPTX のことは知らない
（出す HTML は references/contract.md の決まりに沿う）。

- 画面は 1920×1080。中身の型は左上に見出し（kicker・headline・sub）を置き、その下 BODY_BOTTOM までを中身に使う
- 文字の幅は、描き出しと同じ字体（既定は Noto Sans JP。無ければ游ゴシック）を PIL で測る。入らない文字は DADS の段を
  1つずつ下り、下限まで下りても入らなければ DeckError（台本を短くする）。最終的なはみ出しの判定は描き出し係の Chrome の検査が行うので、ここは目安
- 大きさ・行高・余白・角丸は DADS のスライド用ローカルデザインシステム（skills/dads/references/slide-local.md）の段だけを使う（ADR 0020）
- 動きは Slide.anim が属性にする。`steps: false` では全部 step 0 にし、元の段の順を data-delay に移す。
  handout モード（still）では動きの属性を付けない（全部が最初から出ている・2026-10-06）
- 強調は台本の文字の中で `**語**` と書く（差し色 accent の em になる）
- 色は CSS 変数（--primary・--primary-mark・--rule など。役は deck.py の palette）で書く。SVG の塗りだけはテーマの色を直に書く（s.pal）
"""
from __future__ import annotations

import functools
import html as _html
import math
import pathlib
import re

import deck_art as ART
import deck_icons as ICON

W, H = 1920, 1080
X0 = 120                 # 左右の余白
CW = W - 2 * X0          # 中身の幅 1680
HEAD_TOP = 80
BODY_GAP = 40            # 見出しと中身の間（余白の段 4）
BODY_BOTTOM = 952        # 中身の下端（下の文字とページ番号は 1008 から。上下の余白 64 を引いた 1016 の手前）
TAKE_H, TAKE_GAP = 88, 40    # 締めの帯（takeaway）の高さと、中身との間（ADR 0019）
STEP_GAP = 600           # 段を data-delay に移すときの 1段ぶんの遅れ（ms）
SAFE = 0.94              # 行の見積もりで使う幅の割合（ブラウザの禁則と文節の折り返しのぶんの余裕）
MIN_PX = 22              # 型が出す文字の下限（注記 22。本文は 24 以上を型ごとに渡す）
FULL = 9999              # 角丸 full（高さの半分以上なら半円になる。PPTX でも角丸の最大値に写る）

# --- DADS のスライド用ローカルデザインシステム（skills/dads/tokens/slide.json と同じ値。tests が見比べる）---
SCALE = (200, 160, 120, 96, 80, 64, 57, 48, 45, 36, 32, 28, 26, 24, 22, 20, 16)   # 文字の段（80 以上は拡張・16 は透かしだけ）
LINE_HEIGHTS = (1.0, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.75)                         # 行高の段
SPACE = {1: 8, 2: 16, 3: 24, 4: 40, 5: 64}                                         # 余白の段（基準 8）
COLS, COL, GUTTER = 12, 96, 48                                                     # 格子: 12 列・列 96・溝 48・左右の余白 X0
RADII = (0, 8, 12, 16, 24, 32, FULL)                                               # 角丸の段
ELEVATION_2 = "0 2px 12px 2px rgba(0, 0, 0, 0.1), 0 1px 6px 0 rgba(0, 0, 0, 0.3)"  # 浮かせる印（番号の丸）だけ。影はこれだけ
SCRIM_ALPHA = 0.86          # 全面写真の上の文字の箱（ink の半透明。白い文字・差し色が白い写真の上でも 4.5:1）
# 写真の上に文字を重ねる型の、文字の下の暗幕の濃さ（グラデーションなら文字がある範囲での最小）。deck.py が白い写真を仮定して検査する
QUOTE_ALPHA = 0.86          # 全面の写真に重ねる引用の暗幕（一様）
OVERLAYS = {"photo（全面）": SCRIM_ALPHA, "cover・section（hero・dark）": SCRIM_ALPHA, "quote（全面）": QUOTE_ALPHA}
HERO_VEIL = 0.94            # 全面の表紙の題の下の幕（背景の色の半透明）
# 写真の上に「背景の色の幕」を敷いて濃い文字を置く型。deck.py が一番暗い（黒い）写真を仮定して検査する
LIGHT_OVERLAYS = {"cover・section（hero・light）": HERO_VEIL}
CALLOUT_W = 528    # グラフの右の要点の枠の幅（格子の 4 列）
CALLOUT_PAD, BORDER = 40, 2     # 箱の内側の余白（段 4）と枠の太さ
CALLOUT_IW = CALLOUT_W - 2 * (CALLOUT_PAD + BORDER)   # 要点の枠の内幅（枠 2px・左右の padding 40）


def span(n: int) -> int:
    """格子の n 列ぶんの幅（列 96・溝 48）。"""
    return COL * n + GUTTER * (n - 1)


def col_x(i: int) -> int:
    """格子の i 列目（1 から）の左端。"""
    return X0 + (COL + GUTTER) * (i - 1)


FX = ("rise", "fade", "pop", "wipe-right", "wipe-left", "wipe-up", "wipe-down", "wheel")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
DEFAULT_FONTS = {
    "regular": "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/YuGothR.ttc",
    "bold": "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/YuGothB.ttc",
}
FONTS = dict(DEFAULT_FONTS)
IMAGE_KEYS = {"path", "prompt", "focus", "alt"}
FOCUS_WORDS = {"center": "50% 50%", "left": "0% 50%", "right": "100% 50%", "top": "50% 0%", "bottom": "50% 100%"}
COMMON_KEYS = {"type", "talk", "transition", "steps", "fx", "id", "role"}


class DeckError(Exception):
    """台本の誤り（rc=2）。メッセージはどのスライドの何が悪いかを日本語で言う。"""


# --- 文字 --------------------------------------------------------------------------

def use_fonts(regular: str, bold: str) -> None:
    """測る字体を差し替える（deck.py がテーマと設定から決めて呼ぶ）。"""
    for p in (regular, bold):
        if not pathlib.Path(p).is_file():
            raise DeckError(f"字体ファイルが無い: {p}（設定の fonts で場所を指定する）")
    FONTS.update(regular=regular, bold=bold)
    _font.cache_clear()


@functools.lru_cache(maxsize=128)
def _font(size: int, bold: bool):
    from PIL import ImageFont
    return ImageFont.truetype(FONTS["bold" if bold else "regular"], size, index=0)


def plain(text) -> str:
    return str(text or "").replace("**", "")


def esc_plain(s) -> str:
    """属性・ノート・<title> 用（改行はそのまま）。"""
    return _html.escape(str(s or ""))


def esc(s) -> str:
    """画面に出す文字（改行 \\n は <br>。行数の見積もりも改行を数える）。"""
    return esc_plain(s).replace("\n", "<br>")


def rich(text) -> str:
    """`**語**` を <em> にする（閉じていない ** は文字のまま）。改行（\\n）は <br>（折り返しの位置を台本で決める）。"""
    return re.sub(r"\*\*(.+?)\*\*", r"<em>\1</em>", esc(text), flags=re.S)   # esc が改行を <br> にする。強調は改行をまたいでよい


def text_width(text, size, bold=False) -> float:
    return _font(int(size), bold).getlength(plain(text))


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.,:%/+\-]*|\s+|.", re.S)


def count_lines(text, size, width, bold=False) -> int:
    """改行（\\n）で区切った行ごとに数えて足す。"""
    return sum(_count_lines(seg, size, width, bold) for seg in plain(text).split("\n"))


def _count_lines(text, size, width, bold=False) -> int:
    """width に折り返したときの行数（英数字の語は分けない・ほかは1文字ずつ折れる前提）。1語が幅を越えたら 99。"""
    limit = width * SAFE
    f = _font(int(size), bold)
    n, cur = 1, 0.0
    for tok in _TOKEN.findall(plain(text)):
        w = f.getlength(tok)
        if w > limit and not tok.isspace():
            return 99                      # 英数字の語は途中で折れず、箱からはみ出す（縮めるか rc=2）
        if cur and cur + w > limit and not tok.isspace():
            n, cur = n + 1, 0.0
        cur += 0 if (tok.isspace() and cur == 0) else w
    return n


def snap(size) -> int:
    """size 以下で一番大きい文字の段（段より小さければ一番小さい段）。"""
    return next((v for v in SCALE if v <= size), SCALE[-1])


def step_down(size, n: int = 1, floor: int = 0) -> int:
    """size から段を n つ下りた大きさ（floor 未満にはならない）。"""
    i = SCALE.index(snap(size))
    return max(SCALE[min(i + n, len(SCALE) - 1)], snap(floor) if floor else 0)


def step_up(size, n: int, hi: int) -> int:
    """size から段を n つ上った大きさ（hi を越えない・size より小さくならない）。"""
    i = SCALE.index(snap(size))
    return max(snap(size), min(SCALE[max(i - n, 0)], snap(hi)))


def fit(text, size, width, *, what, lines=None, height=None, lh=1.5, min_size=None, bold=False, tidy=False):
    """width × (lines 行 か height px) に収まる (文字の大きさ, 行数)。DADS の段を1つずつ下りる。下限まで下りても入らなければ DeckError。

    size は段（SCALE）に丸める。下限は min_size（省けば size から2段下）と MIN_PX の大きいほう。
    tidy=True（見出し・題）は、14% までの縮小で1行減らせるなら減らす（「〜に／する」のような1語だけの行を避ける）。
    """
    assert lh in LINE_HEIGHTS, f"{what}: 行高 {lh} は DADS の段（{LINE_HEIGHTS}）にない"
    size = snap(size)
    floor = snap(max(min_size or step_down(size, 2), MIN_PX))
    cands = [v for v in SCALE if floor <= v <= size] or [floor]
    for i, s in enumerate(cands):
        n = count_lines(text, s, width, bold)
        if (lines is None or n <= lines) and (height is None or n * s * lh <= height + 0.5):
            if tidy and n > 1:
                for t in cands[i + 1:]:
                    if t < s * 0.86 - 0.5:
                        break
                    if count_lines(text, t, width, bold) < n:
                        return t, n - 1
            return s, n
    room = f"{lines} 行" if lines is not None else f"高さ {height:.0f}px"
    raise DeckError(f"{what}が長すぎて入らない（{size}px から {floor}px まで下りても {room} を越える。"
                    f"幅 {width:.0f}px）: {plain(text)}")


def px(v) -> str:
    return f"{v:.0f}px"


def box(x, y, w=None, h=None, extra="") -> str:
    s = f"left:{px(x)}; top:{px(y)};"
    if w is not None:
        s += f" width:{px(w)};"
    if h is not None:
        s += f" height:{px(h)};"
    return s + (" " + extra if extra else "")


# --- 1枚の文脈 ---------------------------------------------------------------------

class Slide:
    """1枚ぶんの台本・色・動きの記録。型の関数はこれを受け取って HTML の部品の列を返す。"""

    def __init__(self, no: int, spec: dict, pal: dict, flat: bool, script_dir: pathlib.Path, assets: list,
                 placeholder: bool = False, slots: list | None = None, warnings: list | None = None,
                 overlays: dict | None = None, still: bool = False, image_look: str | None = None,
                 gen_dir: pathlib.Path | None = None):
        import imagegen as G                     # 生成の枠の描く指示と鍵（imagegen が deck_types を import するので、ここで読む）
        self.no, self.spec, self.pal, self.flat = no, spec, pal, flat
        self.image_look = G.look_of(image_look)  # 生成の枠の画風（ADR 0011）
        self.chars = G.characters_of(spec.get("_characters"), script_dir, no)   # 最初の生成の枠に参照画像として渡す
        self.chars_used = False
        self.still = still                       # 動きを付けない（handout）。段は数える（talk の照合は talk モードと同じ）
        self.type = spec.get("type")
        self.script_dir = script_dir
        self.gen_dir = gen_dir                   # 生成の絵の置き場（キャッシュの区画・ADR 0014）。無ければ台本のそばの images/
        self.assets = assets                     # (元のパス, assets/ の中の名前) を deck.py がコピーする
        # 画像が無ければ仮の枠を描く（下書き用）。False なら rc=2。"generated" は生成の枠（prompt だけの枠）だけ仮にする（fill の前の検査）
        self.placeholder = placeholder
        self.slots = [] if slots is None else slots          # 画像の枠の一覧（deck.image_slots が返す）
        self.warnings = [] if warnings is None else warnings  # 仮の枠を描いたことなど
        self.overlays = {} if overlays is None else overlays  # 使った文字の幕（OVERLAYS・LIGHT_OVERLAYS の名前 → 枚目）。deck.py が検査する
        self.steps: set[int] = set()             # 型が使った段（1 以上）。flat でも記録する（talk の照合に使う）
        self.grow = 0                            # 中身が少ないときに大きく組む段の数（build_slide が決める・GROW_STEPS）
        self.need: float | None = None           # 型が見積もった中身の高さ（px）。大きく組めるかの判断に使う
        self.avail: float | None = None          # 中身を置ける高さ（見出しの下から BODY_BOTTOM まで）
        # 中身の下端。締めの帯（takeaway）があれば、その分だけ上がる（帯は BODY_BOTTOM の上に置く）
        self.bottom = BODY_BOTTOM - (TAKE_H + TAKE_GAP if spec.get("takeaway") else 0)
        self.caps: dict[str, int] = {}           # 並ぶ項目の同じ役の文字（カードの題など）の大きさの上限（build_slide がそろえる）
        self.seen: dict[str, list[int]] = {}     # 役ごとに、組んだ文字の大きさ
        self.fx = spec.get("fx")
        if self.fx is not None and self.fx not in FX:
            self.err(f"fx は {' / '.join(FX)} のどれか（{self.fx!r}）")

    def err(self, msg: str):
        raise DeckError(f"{self.no}枚目（{self.type}）: {msg}")

    def fit(self, role: str, text, size, width, under: str | None = None, **kw):
        """並ぶ項目の文字の fit。役（role）ごとに大きさを記録し、そろえる上限（caps）があれば、そこから測る。

        under は本文の上の題の役。本文は題より大きくしない（DADS: 箱の題は常に本文以上。題が長くて下りたのに
        本文だけ大きく残るのを防ぐ）。"""
        if under:
            ref = self.caps.get(under) or min(self.seen.get(under) or [0]) or None
            if ref:
                size = min(size, ref)
        if role in self.caps:
            size, kw["tidy"] = min(size, self.caps[role]), False
        v, n = fit(text, size, width, **kw)
        self.seen.setdefault(role, []).append(v)
        return v, n

    def g(self, v: int, hi: int) -> int:
        """文字の大きさ v を、大きく組む段の数だけ上る（hi を越えない。0 段なら元のまま）。hi は役の上限（slide-local.md の表）。"""
        return step_up(v, self.grow, hi) if self.grow else v

    def gap(self, v: int) -> int:
        """間隔 v を、大きく組む段の数に応じて 8 の倍数のまま広げる（0 段なら元のまま）。"""
        return int(round(v * (1, 1.25, 1.5)[min(self.grow, 2)] / 8)) * 8

    # 動き ---
    def anim(self, step: int, fx: str = "rise", delay: int = 0, name: str | None = None, item: bool = False) -> str:
        """data-step・data-fx・data-delay・data-name の属性。item=True は並びの1つ（台本の fx で差し替える）。"""
        if item and self.fx:
            fx = self.fx
        if step >= 1:
            self.steps.add(step)
        if self.still:
            return f'data-name="{esc_plain(name)}"' if name else ""
        if self.flat:
            step, delay = 0, step * STEP_GAP + delay
        a = f'data-step="{step}" data-fx="{fx}"'
        if delay:
            a += f' data-delay="{delay}"'
        if name:
            a += f' data-name="{esc_plain(name)}"'
        return a

    @property
    def clicks(self) -> int:
        return len(self.steps)

    # 台本の読み取り ---
    def text(self, key, required=False, default=None, spec=None, where=""):
        spec = self.spec if spec is None else spec
        v = spec.get(key, default)
        if v is None or v == "":
            if required:
                self.err(f"{where}{key} が無い")
            return default
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        if not isinstance(v, str):
            self.err(f"{where}{key} は文字で書く（{type(v).__name__}）")
        return v.strip()

    def items(self, key, lo, hi, fields, required=("title",), spec=None, where=""):
        """並び（文字だけの要素は {"title": 文字}）。数・未知のキー・必須を確かめる。"""
        spec = self.spec if spec is None else spec
        raw = spec.get(key)
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            self.err(f"{where}{key} は配列で書く")
        if not lo <= len(raw) <= hi:
            self.err(f"{where}{key} は {lo}〜{hi} 個（{len(raw)} 個）")
        out = []
        for i, it in enumerate(raw, 1):
            if isinstance(it, (str, int, float)) and not isinstance(it, bool):
                it = {fields[0]: str(it)}
            if not isinstance(it, dict):
                self.err(f"{where}{key} の {i} 個目は文字かオブジェクトで書く")
            extra = sorted(set(it) - set(fields))
            if extra:
                self.err(f"{where}{key} の {i} 個目に知らないキー: {', '.join(extra)}（使えるのは {', '.join(fields)}）")
            for r in required:
                if it.get(r) in (None, ""):
                    self.err(f"{where}{key} の {i} 個目に {r} が無い")
            out.append(it)
        return out

    def image_spec(self, spec, key) -> dict:
        """台本の画像の書き方をそろえる: 文字ならパス、オブジェクトなら {path, prompt, focus, alt}。"""
        if isinstance(spec, str):
            spec = {"path": spec}
        if not isinstance(spec, dict):
            self.err(f"{key} はファイルのパスか、{{path, prompt, focus, alt}} のオブジェクトで書く")
        extra = sorted(set(spec) - IMAGE_KEYS)
        if extra:
            self.err(f"{key} に知らないキー: {', '.join(extra)}（使えるのは {', '.join(sorted(IMAGE_KEYS))}）")
        for k in IMAGE_KEYS:
            if k in spec and (not isinstance(spec[k], str) or not spec[k].strip()):
                self.err(f"{key}.{k} は空でない文字で書く")
        if not spec.get("path") and not spec.get("prompt"):
            self.err(f"{key} には path か prompt のどちらかを書く")
        return {k: spec[k].strip() for k in IMAGE_KEYS if spec.get(k)}

    def focus_css(self, focus, key) -> str:
        """focus → object-position の %（"center"・"right top"・"center 30%"・"40% 30%"）。px は rc=2。

        描き出し係（extract.mjs）は % しか読まない（px は中央に戻る）ので、ここで % に直してから HTML に書く。
        """
        if focus is None:
            return ""
        toks = focus.split()
        bad = lambda: self.err(f"{key}.focus は center・left・right・top・bottom を1〜2語か \"40% 30%\" の形"
                               f"（% は 0〜100。px は使えない）: {focus}")
        if not 1 <= len(toks) <= 2:
            bad()
        xs, ys = {"left": 0, "center": 50, "right": 100}, {"top": 0, "center": 50, "bottom": 100}
        if len(toks) == 1:
            if toks[0] in FOCUS_WORDS:
                return FOCUS_WORDS[toks[0]]
            bad()
        a, b = toks
        if a in ys and a != "center" and b in xs:        # "top left" は "left top" と同じ
            a, b = b, a

        def pct(tok, words):
            if tok in words:
                return float(words[tok])
            m = re.fullmatch(r"(\d{1,3}(?:\.\d+)?)%", tok)
            if not m or not 0 <= float(m.group(1)) <= 100:
                bad()
            return float(m.group(1))
        x, y = pct(a, xs), pct(b, ys)
        return f"{x:g}% {y:g}%"

    def find_image(self, im: dict, key: str) -> tuple[pathlib.Path | None, pathlib.Path, str]:
        """(在るファイル or None, 置き場, 出どころ)。出どころは path（台本のパス）・placed（置き場 images/s{NN}-{枠}.png に
        置いた絵。.jpg・.webp も見る）・generated（prompt だけの枠。置き場は images/gen-<鍵>.png で、img が決める）。"""
        path = im.get("path")
        if path:
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", path) and not re.match(r"^[a-zA-Z]:\\", path):
                self.err(f"{key} に URL は使えない（ローカルのファイルを置いてパスで書く）: {path}")
            p = pathlib.Path(path).expanduser()
            p = p if p.is_absolute() else self.script_dir / p
            if p.suffix.lower() not in IMAGE_EXTS:
                self.err(f"{key} の画像は {' / '.join(IMAGE_EXTS)} のどれか: {p.name}")
            return (p if p.is_file() else None), p, "path"
        stem = self.script_dir / "images" / f"s{self.no:02d}-{key}"
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            if stem.with_suffix(ext).is_file():
                return stem.with_suffix(ext), stem.with_suffix(".png"), "placed"
        return None, stem.with_suffix(".png"), "generated"

    def img(self, spec, key, cls, x, y, w, h, *, anim="", extra="", clear=None, fit_mode="cover") -> str:
        """画像の枠1つぶんの HTML。枠を slots に記録し、画像を assets/ に登録する。

        clear は画像の上に文字を重ねる側（「左下」など）。prompt_full に「そこを空ける」と書く。
        prompt だけの枠（生成）は、描く指示（画風・キャラを足したもの）から鍵を作り、キャッシュの区画の gen-<鍵>.png を見る
        （ADR 0011・0014）。区画に無く、前の置き場（台本のそばの images/gen-<鍵>.png）にあれば、区画へ写して使う（元は消さない）。
        画像が無いとき: placeholder なら灰色の仮の枠（検品の H-3 に掛かる「ここに画像」）と警告、そうでなければ DeckError。
        """
        import imagegen as G
        im = self.image_spec(spec, key)
        pos = self.focus_css(im.get("focus"), key)
        found, home, origin = self.find_image(im, key)
        prompt = im.get("prompt", "")
        full = ""
        if prompt:
            full = prompt.rstrip("。") + "。文字・ロゴ・透かしを描かない" + (f"。{clear}は文字を重ねるので、物を置かずに空ける" if clear else "") + "。"
        gen = {}
        if origin == "generated":
            chars = [] if self.chars_used else self.chars
            self.chars_used = self.chars_used or bool(chars)
            text = G.text_of(full, self.image_look, self.pal, chars)
            shape = G.shape_of(w, h)
            k = G.key_of(text, shape, chars)
            gpath = (self.gen_dir or self.script_dir / "images") / f"gen-{k}.png"
            adopt_gen(self.script_dir / "images" / gpath.name, gpath)
            found = gpath if gpath.is_file() else None
            gen = {"key": k, "gen_path": str(gpath), "gen_text": text, "shape": shape, "image_look": self.image_look,
                   "characters": [{"name": c["name"], "pose": c["pose"], "path": str(c["path"])} for c in chars]}
            home = gpath
        gw, gh = gen_size(w, h)
        ew, eh = exact_size(w, h)
        if fit_mode == "contain":
            crop = "切り抜かない（縮めて枠に収める・余りは地の色）"
        else:                                    # 画像がまだ無い: 切り抜くかは描いた寸法で決まる。断定しない
            crop = f"画像が無いので未定（{gw}x{gh} で作ると{crop_text(gw, gh, w, h, pos)}）"
        self.slots.append({"slide": self.no, "type": self.type, "slot": key, "width": round(w), "height": round(h),
                           "aspect": round(w / h, 3), "size": f"{gw}x{gh}", "size_exact": f"{ew}x{eh}", "fit": fit_mode,
                           "crop": crop, "note": SLOT_NOTE if fit_mode == "cover" else "端を切らずに見せる（図・スクショ・地図向き）",
                           "prompt": prompt, "prompt_full": full, "focus": im.get("focus"), "alt": im.get("alt", ""),
                           "clear": clear, "origin": origin, "path": str(found or home), "default_path": str(home),
                           "exists": found is not None, **gen})
        style = box(x, y, w, h, extra) + (f" object-position:{pos};" if pos else "")
        if found is None:
            if not (self.placeholder is True or (self.placeholder == "generated" and origin == "generated")):
                if origin == "generated":
                    self.err(f"{key} の画像がまだ無い（prompt から描く枠。deck が Codex で描いて埋める。`fill` だけでも描ける）: {home}")
                self.err(f"{key} の画像が無い: {home}")
            self.warnings.append(f"{self.no}枚目（{self.type}）の画像「{key}」が無いので仮の枠を描いた（{home}）")
            label = placeholder_text(prompt, w, h)
            return (f'<div class="img-ph" style="{style}" {anim} data-name="仮の画像">'
                    f'<div style="font-size:{label[1]}px">{esc(label[0])}</div></div>')
        try:
            from PIL import Image
            with Image.open(found) as im_:
                iw, ih = im_.size
        except Exception:
            self.err(f"{key} の画像が読めない: {found}")
        scale = (max if fit_mode == "cover" else min)(w / iw, h / ih)
        if scale > 1.25:                        # 枠の 0.8 倍より小さい画像は引き伸ばしてぼける
            self.warnings.append(f"{self.no}枚目（{self.type}）の画像「{key}」が小さい（{iw}×{ih}。枠 {round(w)}×{round(h)} に "
                                 f"{scale:.2f} 倍に引き伸ばす。{ew}×{eh} くらいで用意する）")
        self.slots[-1].update(pixels=f"{iw}x{ih}")
        if fit_mode == "cover":
            self.slots[-1]["crop"] = crop_text(iw, ih, w, h, pos)    # 在る画像の実寸で判定する
        ext = found.suffix.lower()
        name = f"s{self.no:02d}-{key}{'.jpg' if ext == '.jpeg' else ext}"
        self.assets.append((found, name))
        return f'<img class="{cls}" src="assets/{name}" alt="{esc_plain(im.get("alt", ""))}" style="{style}" {anim} />'


def adopt_gen(old: pathlib.Path, new: pathlib.Path) -> None:
    """前の置き場（台本のそば）の生成の絵と横の .json を、キャッシュの区画へ写す。区画にあれば何もしない。"""
    if new.is_file() or old == new or not old.is_file():
        return
    import shutil
    new.parent.mkdir(parents=True, exist_ok=True)
    if old.with_suffix(".json").is_file():
        shutil.copy2(old.with_suffix(".json"), new.with_suffix(".json"))
    shutil.copy2(old, new)


GEN_SIZES = ((1024, 1024), (1536, 1024), (1024, 1536))   # gpt-image-2 の定型の大きさ（目安）


def crop_text(iw, ih, w, h, pos="") -> str:
    """iw×ih の画像を w×h の枠に object-fit: cover で入れたときの切れ方（「左右を計 50% 切り抜く（中央を残す）」など）。"""
    r = math.log((iw / ih) / (w / h))
    if abs(r) < 0.01:
        return "切り抜かない"
    cut = 1 - math.exp(-abs(r))                 # 長い方の辺のうち切れる割合
    where = "左右" if r > 0 else "上下"
    keep = f"focus（{pos}）の位置を残す" if pos else "中央を残す"
    return f"{where}を計 {cut * 100:.0f}% 切り抜く（{keep}）"
SLOT_NOTE = "縦横比が違っても枠に合わせて切り抜く。大事なものは focus の側に置く（生成の寸法は当てにしない）"


def exact_size(w, h) -> tuple[int, int]:
    """枠と同じ縦横比で、辺が 16 の倍数・長辺 1920 以上（枠が小さければ 1024 以上）・2560×1440 以下の大きさ（目安）。"""
    long_ = min(max(1024, max(w, h) * 1.07), 2560 if w >= h else 1536)
    k = long_ / max(w, h)
    return round(w * k / 16) * 16, round(h * k / 16) * 16


def gen_size(w, h) -> tuple[int, int]:
    """枠の縦横比に一番近い、画像生成の大きさ（比の対数の差が最小）。"""
    return min(GEN_SIZES, key=lambda g: abs(math.log((w / h) / (g[0] / g[1]))))


def placeholder_text(prompt: str, w: float, h: float) -> tuple[str, int]:
    """仮の枠の文字と大きさ。prompt の頭を、枠に入るだけ（3行まで）。小さな枠は「ここに画像」だけ。"""
    size = snap(max(MIN_PX, min(28, round(w / 14))))
    lines = max(1, min(3, int((h - 32) / (size * 1.5))))
    head = "【仮の画像】ここに画像"
    if prompt and w >= 360:
        body = prompt
        while body and count_lines(f"{head}: {body}…", size, w - 64) > lines:
            body = body[:-1]
        if body:
            return f"{head}: {body}{'' if body == prompt else '…'}", size
    while size > SCALE[-1] and count_lines(head, size, w - 32) > lines:
        size = step_down(size)
    return head, size


# --- 部品 --------------------------------------------------------------------------

def header(s: Slide, default_headline=None, width=CW, default_kicker=None, top=HEAD_TOP, big=False):
    """左上の見出し（kicker・headline・sub）。(HTML, 中身の上端) を返す。top は見出しの上端（上に写真の帯を置く型が下げる）。

    見出しは Dsp-64B-140（下限 45）。big は図解の型の大見出し Ext-80B-140（下限 64・ADR 0019）。kicker は 28B-150・sub は 32N-150
    """
    kicker = s.text("kicker", default=default_kicker)
    head = s.text("headline", required=default_headline is None, default=default_headline)
    sub = s.text("sub")
    # big: 図解の型（式・放射・対）の大きな見出し（ADR 0019）。言い切りの1文を、本文より先に目に入れる
    hs, hn = fit(head, 80 if big else 64, width, what="見出し（headline）", lines=2, lh=1.4,
                 min_size=64 if big else 45, bold=True, tidy=True)
    parts = [f'<div class="head" style="width:{px(width)}; top:{px(top)}">']
    h = 0.0
    if kicker:
        ks, _ = fit(kicker, 28, width, what="kicker", lines=1, lh=1.5, min_size=28, bold=True)
        parts.append(f'<div class="kicker" style="font-size:{ks}px" data-name="小見出し">{esc(kicker)}</div>')
        h += ks * 1.5 + SPACE[1]
    parts.append(f'<h1 class="headline" style="font-size:{hs}px; line-height:1.4" {s.anim(0, "rise", 0, "見出し")}>{rich(head)}</h1>')
    h += hn * hs * 1.4
    if sub:
        ss, sn = fit(sub, 32, width, what="sub", lines=2, lh=1.5, min_size=28)
        parts.append(f'<div class="sub" style="font-size:{ss}px" {s.anim(0, "rise", 150, "リード")}>{rich(sub)}</div>')
        h += SPACE[2] + sn * ss * 1.5
    parts.append("</div>")
    return "".join(parts), top + h + BODY_GAP


def source_line(s: Slide, y=BODY_BOTTOM + 8, on_dark=False, width=CW):
    """出典・注記（下端の上）。無ければ空。on_dark は写真の暗幕の上（白の文字）。width は置ける幅（写真の脇なら狭い）。"""
    src = s.text("source")
    if not src:
        return ""
    fs, _ = fit(src, 22, width, what="source", lines=1, lh=1.5, min_size=22)
    col = " color:#ffffff;" if on_dark else ""
    return f'<div class="source" style="top:{px(y)}; width:{px(width)}; font-size:{fs}px;{col}" data-name="出典">{esc(src)}</div>'


def chevron(w, h, depth, first, fill, stroke=None, sw=0) -> str:
    """矢羽根の形（SVG の polygon）。first は左の切り欠きなし。stroke があれば、辺の内側に収まる線で縁取る（面が地と 3:1 にならないとき）。"""
    m = sw / 2
    pts = [(m, m), (w - depth, m), (w - m, h / 2), (w - depth, h - m), (m, h - m)]
    if not first:
        pts.append((depth + m, h / 2))
    p = " ".join(f"{x:.0f},{y:.0f}" for x, y in pts)
    line = f' stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round"' if stroke else ""
    return (f'<svg width="{w:.0f}" height="{h:.0f}" viewBox="0 0 {w:.0f} {h:.0f}">'
            f'<polygon points="{p}" fill="{fill}"{line}/></svg>')


def num_label(v, decimals: int | None = None) -> str:
    """数の表示（整数は桁区切り、小数はそのまま）。decimals を渡すと小数の桁をそろえる（6.0 と 4.6 を並べるとき）。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if decimals:
        return f"{v:,.{decimals}f}"
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,}"


# --- 型 ----------------------------------------------------------------------------
# 各型は (Slide) -> list[str]。表紙・章扉・写真は自分で全面を組み、ほかは header() の下に中身を置く。

SPLITS = {"1/2": 960, "2/5": 768, "1/3": 640}   # 写真の幅（photo の left・right）
CLOSING_IMG_W = 768                             # 写真つきの締めの、右の写真の幅
BAND_H = 480                                    # 横長の帯の写真の高さ（photo の band）
ANNOTATE_IMG_W, MARK_D = span(8), 56          # 注釈つきの画像の幅（格子の 8 列）・番号の丸印の直径
KPI_BAND_H = 296                                # 数字の上の写真の帯の高さ（kpi の image）
HERO_SOLID, HERO_FADE = 920, 512      # 全面の写真の幕: ベタの幅と、写真に溶かす幅
HERO_TW = 744                          # 幕の上の文字の幅


def hero_layer(s: Slide, name: str):
    """全面の写真と、文字を置く側（text_side）の幕。(部品, 文字の x, 文字の幅, 暗い幕か) を返す。

    tone: light（既定）は背景の色の幕（HERO_VEIL）に濃い文字、dark は ink の幕（SCRIM_ALPHA）に白い文字。
    幕の端は2色の linear-gradient で写真に溶かす。濃さは deck.py が一番悪い写真（light は黒・dark は白）を仮定して検査する
    """
    side = s.spec.get("text_side", "left")
    if side not in ("left", "right"):
        s.err("text_side は left か right")
    tone = s.spec.get("tone", "light")
    if tone not in ("light", "dark"):
        s.err("tone は light（明るい幕に濃い文字）か dark（暗い幕に白い文字）")
    clear = ("左" if side == "left" else "右") + "の45%（文字を置く）"
    parts = [s.img(s.spec.get("image"), "image", "photo", 0, 0, W, H, anim=s.anim(0, "fade", 0, name), clear=clear)]
    sx, gx = (0, HERO_SOLID) if side == "left" else (W - HERO_SOLID, W - HERO_SOLID - HERO_FADE)
    direction = "to right" if side == "left" else "to left"
    c0, c1 = ("--veil", "--veil-0") if tone == "light" else ("--scrim", "--scrim-0")
    s.overlays.setdefault(f"cover・section（hero・{tone}）", s.no)
    parts.append(f'<div class="veil" style="{box(sx, 0, HERO_SOLID, H)} background-color:var({c0});" data-name="文字の下の幕"></div>')
    parts.append(f'<div class="veil-edge" style="{box(gx, 0, HERO_FADE, H)} background-image:linear-gradient({direction}, '
                 f'var({c0}), var({c1}));" data-name="幕の端"></div>')
    return parts, (144 if side == "left" else W - 144 - HERO_TW), HERO_TW, tone == "dark"


def icon_of(s: Slide, it: dict, where: str) -> str | None:
    """項目の icon（deck_icons の名前）。無ければ None、知らない名前は rc=2。"""
    name = it.get("icon")
    if name in (None, ""):
        return None
    if not isinstance(name, str) or name not in ICON.ICONS:
        s.err(f"{where}の icon {name!r} は無い（使えるのは {', '.join(ICON.names())}）")
    return name


def icon_disc(name: str, d: float, icon: float, *, ground="var(--tint)", ink="var(--primary-text)", extra="") -> str:
    """丸い地の上のアイコン（d px の丸・icon px のアイコン）。アイコンの色は 4.5:1（地の上で文字と同じ扱い・ADR 0020）。"""
    return (f'<div class="icon-disc" style="width:{d:g}px; height:{d:g}px; border-radius:{FULL}px; '
            f'background-color:{ground};{extra}">{ICON.svg(name, icon, ink)}</div>')


CHIP_H = 40               # 札の高さ（24B・行高 1.0・上下 8）。強調の札・状態の札で使う（ADR 0021）
HL_BADGE = "注目"          # 強調（accent: true）の札の既定の語。台本の badge で替える


def chip(text: str, kind: str = "on", icon: str | None = None, icon_color: str = "var(--on-accent)") -> str:
    """札（DADS のチップラベル）。24B・角 full・文字は必ず語で添える（色だけで状態を示さない）。

    kind: on＝差し色の面に白抜き（強調）／done＝主役の面に白抜き（済）／todo＝白に枠（これから）／line＝地に枠
    icon があれば、語の前に 24px のアイコンを置く（アイコンは語と組む）。
    """
    if icon:
        return (f'<div class="chip {kind}">{ICON.svg(icon, 24, icon_color)}<div class="chip-t">{esc(text)}</div></div>')
    return f'<div class="chip {kind}">{esc(text)}</div>'


def badge_of(s: Slide, it: dict, hl: bool, where: str) -> str | None:
    """強調の項目の札の語（台本の badge。無ければ「注目」）。強調でない項目に badge を書いたら rc=2。"""
    b = s.text("badge", spec=it)
    if b and not hl:
        s.err(f"{where}の badge は強調する項目（accent: true）にだけ書く")
    if not hl:
        return None
    if text_width(b or HL_BADGE, 24, True) > 192:
        s.err(f"{where}の badge は短く（全角6字くらいまで）: {b}")
    return b or HL_BADGE


def art_layer(s: Slide, x: int, w: int, name: str) -> list[str]:
    """layout: graphic の飾り（deck_art）。x から右端まで・上下いっぱいに、層ごとに少しずつ遅れて出る（クリックは増やさない）。"""
    motif = s.spec.get("motif") or ART.default_motif(str(s.spec.get("_motif_seed", "")))
    if motif not in ART.MOTIFS:
        s.err(f"motif は {ART.MOTIF_WORDS} のどれか")
    out = []
    for i, layer in enumerate(ART.art_layers(motif, w, H)):
        if layer:
            out.append(f'<svg class="art" viewBox="0 0 {w} {H}" width="{w}" height="{H}" style="position:absolute; {box(x, 0, w, H)}" '
                       f'{s.anim(0, "fade" if i == 0 else "pop", 100 + 180 * i, f"{name}{i + 1}")}>{layer}</svg>')
    return out


def _graphic_keys(s: Slide, layout: str):
    if layout == "graphic" and s.spec.get("image"):
        s.err("layout: graphic は画像の代わりに図形で飾る置き方。image と一緒に書かない（写真なら split か hero）")
    if "motif" in s.spec and layout != "graphic":
        s.err("motif は layout: graphic のときだけ")


def t_cover(s: Slide):
    """表紙: ラベル・題・副題・日付や組織。image があれば右の 880px に写真（layout: split）か、全面の写真（layout: hero）。
    layout: graphic は画像の代わりに、右の 880px をテーマの色の図形で飾る（motif で図柄を選ぶ。deck_art）。

    hero は写真を全面に敷き、text_side（left 既定・right）に背景の色の幕（HERO_VEIL の濃さ）を重ねて、その上に題を置く。
    幕の端は2色の linear-gradient で写真に溶かす。幕の濃さは deck.py が「一番暗い（黒い）写真」を仮定して検査する
    """
    label, title, subtitle = s.text("label"), s.text("title", required=True), s.text("subtitle")
    meta = s.spec.get("meta") or []
    if isinstance(meta, str):
        meta = [meta]
    if not isinstance(meta, list) or len(meta) > 3:
        s.err("meta は文字か、3つまでの文字の配列で書く")
    layout = s.spec.get("layout", "split")
    if layout not in ("split", "hero", "graphic"):
        s.err("layout は split（右に写真・既定）か hero（全面の写真）か graphic（写真の代わりに図形で飾る）")
    _graphic_keys(s, layout)
    if layout == "hero" and not s.spec.get("image"):
        s.err("layout: hero には image が要る")
    if layout != "hero" and ("text_side" in s.spec or "tone" in s.spec):
        s.err("text_side・tone は layout: hero のときだけ")
    parts = []
    tw, tx, dark = 1600, 160, False
    if layout == "hero":
        parts, tx, tw, dark = hero_layer(s, "表紙の写真")
    elif s.spec.get("image"):
        parts.append(s.img(s.spec["image"], "image", "photo", 1040, 0, 880, H, anim=s.anim(0, "fade", 0, "表紙の写真")))
        tw = 816
    elif layout == "graphic":
        parts += art_layer(s, 1040, 880, "表紙の図形")
        tw = 816
    ts, _ = fit(title, 96, tw, what="題（title）", lines=3, lh=1.4, min_size=64, bold=True, tidy=True)   # Ext-96B-140（80 → 64 へ下りる）
    inner = []
    if label:
        ls, _ = fit(label, 28, tw, what="label", lines=1, lh=1.5, min_size=28, bold=True)
        inner.append(f'<div class="cover-label" style="font-size:{ls}px" {s.anim(0, "fade", 0, "ラベル")}>{esc(label)}</div>')
    inner.append(f'<div class="cover-title" style="font-size:{ts}px" {s.anim(0, "rise", 150, "題")}>{rich(title)}</div>')
    inner.append(f'<div class="cover-rule" {s.anim(0, "wipe-right", 450, "題の下線")}></div>')
    if subtitle:
        ss, _ = fit(subtitle, 32, tw, what="副題（subtitle）", lines=2, lh=1.5, min_size=28)
        ink = "; color:var(--ink)" if layout == "hero" and not dark else ""   # 明るい幕の上は muted だと黒い写真で 4.5 に届かない
        inner.append(f'<div class="cover-subtitle" style="font-size:{ss}px{ink}" {s.anim(0, "rise", 600, "副題")}>{rich(subtitle)}</div>')
    od = " on-dark" if dark else (" on-light" if layout == "hero" else "")     # 全面の写真の幕の上: 暗い幕は白い文字・明るい幕は幕の上で 4.5:1 の色
    parts.append(f'<div class="cover-text{od}" style="{box(tx, 96, tw, 720)}">' + "".join(inner) + "</div>")
    if meta:
        rows = "".join(f'<div style="font-size:{fit(str(m), 28, tw, what="meta", lines=1, lh=1.5, min_size=24)[0]}px">{esc(m)}</div>' for m in meta)
        parts.append(f'<div class="cover-meta{od}" style="{box(tx, 856, tw)}" {s.anim(0, "fade", 800, "日付と組織")}>{rows}</div>')
    return parts


def t_agenda(s: Slide):
    """目次: 番号つきの2〜6項目。highlight（1から）で今の章だけを強める。"""
    head, top = header(s, default_headline="本日お話しすること")
    its = s.items("items", 2, 6, ("title", "note"))
    hl = s.spec.get("highlight")
    if hl is not None and (not isinstance(hl, int) or not 1 <= hl <= len(its)):
        s.err(f"highlight は 1〜{len(its)} の整数")
    rh = min(136, int((BODY_BOTTOM - top) / len(its)) // 8 * 8)
    rows = []
    for i, it in enumerate(its):
        note = s.text("note", spec=it)
        nw = text_width(note, 28) + 40 if note else 0
        if nw > 640:
            s.err(f"items の {i + 1} 個目の note が長い（20字くらいまで）: {note}")
        ts, _ = fit(it["title"], 45, CW - 120 - 48 - nw, what=f"items の {i + 1} 個目", lines=1, lh=1.4, min_size=32, bold=True)
        cls = "agenda-row" + ("" if hl is None else (" on" if hl == i + 1 else " dim"))
        pad = "padding:0 24px;" if hl is not None else ""
        rows.append(f'<div class="{cls}" style="height:{px(rh)}; {pad}" {s.anim(0, "rise", 150 + 120 * i, f"目次{i + 1}")}>'
                    f'<div class="agenda-no">{i + 1:02d}</div>'
                    f'<div class="agenda-title" style="font-size:{ts}px">{rich(it["title"])}</div>'
                    + (f'<div class="agenda-note">{esc(note)}</div>' if note else "") + "</div>")
    return [head, f'<div class="agenda" style="{box(X0, top, CW)}">' + "".join(rows) + "</div>", source_line(s)]


def t_section(s: Slide):
    """章扉: 番号・章題・副題。image が無ければ左に primary の帯、あれば右半分に写真（layout: split）か全面の写真（hero）。
    layout: graphic は画像の代わりに、右半分をテーマの色の図形で飾る（文字の置き方は split の写真と同じ）。"""
    title, subtitle, label = s.text("title", required=True), s.text("subtitle"), s.text("label")
    number = s.text("number", default=str(s.spec.get("_auto_number", "")))
    if number.isdigit():
        number = f"{int(number):02d}"
    layout = s.spec.get("layout", "split")
    if layout not in ("split", "hero", "graphic"):
        s.err("layout は split（右半分に写真・既定）か hero（全面の写真）か graphic（写真の代わりに図形で飾る）")
    _graphic_keys(s, layout)
    if layout == "hero" and not s.spec.get("image"):
        s.err("layout: hero には image が要る")
    if layout != "hero" and ("text_side" in s.spec or "tone" in s.spec):
        s.err("text_side・tone は layout: hero のときだけ")
    parts = []
    dark = False
    if layout == "hero":
        parts, x, tw, dark = hero_layer(s, "章扉の写真")
        size, no_cls = 64, "primary"
    elif s.spec.get("image"):
        parts.append(s.img(s.spec["image"], "image", "photo", 960, 0, 960, H, anim=s.anim(0, "fade", 0, "章扉の写真")))
        x, tw, size, no_cls = X0, 720, 64, "primary"
    elif layout == "graphic":
        parts += art_layer(s, 960, 960, "章扉の図形")
        x, tw, size, no_cls = X0, 720, 64, "primary"
    else:
        parts.append('<div class="sec-panel" style="width:640px" data-name="章扉の帯"></div>')
        side = []
        if label:
            ls, _ = fit(label, 28, 440, what="label", lines=1, lh=1.5, min_size=28, bold=True)
            side.append(f'<div class="sec-label on-primary" style="font-size:{ls}px">{esc(label)}</div>')
        if number:
            ns, _ = fit(number, 200, 440, what="number", lines=1, lh=1.0, min_size=96, bold=True)
            side.append(f'<div class="sec-no on-primary" style="font-size:{ns}px" {s.anim(0, "fade", 0, "章の番号")}>{esc(number)}</div>')
        parts.append(f'<div class="sec-text" style="{box(X0, 0, 440, H)}">' + "".join(side) + "</div>")
        x, tw, size, no_cls, label, number = 760, 1040, 80, None, None, None
    ts, _ = fit(title, size, tw, what="章題（title）", lines=3, lh=1.4, min_size=64 if size == 80 else 45, bold=True, tidy=True)
    inner = []
    if label:
        ls, _ = fit(label, 28, tw, what="label", lines=1, lh=1.5, min_size=28, bold=True)
        inner.append(f'<div class="sec-label primary" style="font-size:{ls}px">{esc(label)}</div>')
    if number:
        ns, _ = fit(number, 160, tw, what="number", lines=1, lh=1.0, min_size=96, bold=True)
        inner.append(f'<div class="sec-no {no_cls}" style="font-size:{ns}px; margin-bottom:24px" {s.anim(0, "fade", 0, "章の番号")}>{esc(number)}</div>')
    inner.append(f'<div class="sec-title" style="font-size:{ts}px" {s.anim(0, "rise", 200, "章題")}>{rich(title)}</div>')
    if subtitle:
        ss, _ = fit(subtitle, 32, tw, what="副題（subtitle）", lines=2, lh=1.5, min_size=28)
        inner.append(f'<div class="sec-subtitle" style="font-size:{ss}px" {s.anim(0, "rise", 400, "章の副題")}>{rich(subtitle)}</div>')
    od = " on-dark" if dark else (" on-light" if layout == "hero" else "")     # 全面の写真の幕の上: 暗い幕は白い文字・明るい幕は幕の上で 4.5:1 の色
    if layout == "hero" and not dark:                 # 明るい幕の上は副題も ink（muted は黒い写真で 4.5 に届かない）
        inner = [x_.replace('class="sec-subtitle" style="', 'class="sec-subtitle" style="color:var(--ink); ') for x_ in inner]
    parts.append(f'<div class="sec-text{od}" style="{box(x, 0, tw, H)}">' + "".join(inner) + "</div>")
    return parts


def t_statement(s: Slide):
    """主張: 1文を大きく（Ext-96B-140・80〜64 へ下りる）。body で補足を2〜3行。"""
    head = s.text("headline", required=True)
    kicker, body = s.text("kicker"), s.text("body")
    tw = 1496
    hs, _ = fit(head, 96, tw, what="主張（headline）", lines=3, lh=1.4, min_size=64, bold=True, tidy=True)
    inner = []
    if kicker:
        ks, _ = fit(kicker, 28, tw, what="kicker", lines=1, lh=1.5, min_size=28, bold=True)
        inner.append(f'<div class="kicker" style="font-size:{ks}px" data-name="小見出し">{esc(kicker)}</div>')
    inner.append(f'<div style="display:flex; align-items:stretch" {s.anim(0, "rise", 0, "主張")}>'
                 f'<div class="stmt-bar" style="position:static; flex:none; margin-right:40px"></div>'
                 f'<div class="stmt-text" style="font-size:{hs}px">{rich(head)}</div></div>')
    if body:
        bs, _ = fit(body, 32, tw, what="補足（body）", lines=3, lh=1.6, min_size=28)
        inner.append(f'<div class="stmt-body" style="font-size:{bs}px; margin-left:56px" {s.anim(0, "rise", 500, "補足")}>{rich(body)}</div>')
    return [f'<div class="stmt" style="{box(X0, HEAD_TOP, 1560, BODY_BOTTOM - HEAD_TOP)}">' + "".join(inner) + "</div>",
            source_line(s)]


def _unit_size(vs: int) -> int:
    """数字の大きさ vs に添える単位の大きさ（数字のおよそ 4 割の段・24 以上）。"""
    return max(snap(vs * 0.4), 24)


def t_big_number(s: Slide):
    """大きな数字: 数字＋単位＋説明。side に補足を2〜3（数字と説明）。数字は1クリック目、補足は2クリック目。"""
    head, top = header(s)
    value = s.text("value", required=True)
    unit, label, note = s.text("unit"), s.text("label"), s.text("note")
    side = s.items("side", 0, 3, ("text", "value"), required=("text",))
    mw = 920 if side else CW
    for vs in (v for v in SCALE if 96 <= v <= 200):          # Ext-200B-100 から、数字と単位が1行に入る段まで下りる
        uw = text_width(unit, _unit_size(vs), True) + 16 if unit else 0
        if count_lines(value, vs, mw - uw, True) == 1:
            break
    else:
        s.err(f"数字（value）が長すぎて入らない（200px から 96px まで下りても 1 行を越える。幅 {mw - uw:.0f}px）: {plain(value)}")
    us = _unit_size(vs)
    main = [f'<div class="bn-row"><div class="bn-value" style="font-size:{vs}px">{esc(value)}</div>'
            + (f'<div class="bn-unit" style="font-size:{us}px">{esc(unit)}</div>' if unit else "") + "</div>"]
    if label:
        ls, _ = fit(label, 36, mw, what="説明（label）", lines=2, lh=1.4, min_size=28, bold=True)
        main.append(f'<div class="bn-label" style="font-size:{ls}px">{rich(label)}</div>')
    if note:
        ns, _ = fit(note, 28, mw, what="note", lines=2, lh=1.5, min_size=24)
        main.append(f'<div class="bn-item-text" style="font-size:{ns}px; margin-top:16px">{esc(note)}</div>')
    parts = [head, f'<div class="bn-main" style="{box(X0, top, mw, BODY_BOTTOM - top)}">'
             f'<div {s.anim(1, "pop", 0, "大きな数字")}>' + "".join(main) + "</div></div>"]
    if side:
        rows = []
        for i, it in enumerate(side):
            v = s.text("value", spec=it)
            vs = 57
            if v:
                vs, _ = fit(v, 57, 640, what=f"side の {i + 1} 個目の value", lines=1, lh=1.4, min_size=45, bold=True)
            fs, _ = fit(it["text"], 28, 640, what=f"side の {i + 1} 個目", lines=3, lh=1.5, min_size=24)
            last = "border-bottom:2px solid var(--rule);" if i == len(side) - 1 else ""
            rows.append(f'<div class="bn-item" style="{last}" {s.anim(2, "rise", 150 * i, f"補足{i + 1}", item=True)}>'
                        + (f'<div class="bn-item-value" style="font-size:{vs}px">{esc(v)}</div>' if v else "")
                        + f'<div class="bn-item-text" style="font-size:{fs}px">{rich(it["text"])}</div></div>')
        parts.append(f'<div class="bn-side" style="{box(1120, top, 680, BODY_BOTTOM - top)}">' + "".join(rows) + "</div>")
    parts.append(source_line(s))
    return parts


def inner_w(w: float, hl: bool = False) -> float:
    """箱（カード・列・指標）の内幅。内側の余白 40・外周の枠 2px（強めた箱は 4px）。"""
    return w - 2 * (CALLOUT_PAD + (4 if hl else BORDER))


def box_pad(hl: bool = False) -> float:
    """箱の上下の余白と枠の合計。"""
    return 2 * (CALLOUT_PAD + (4 if hl else BORDER))


def t_cards(s: Slide):
    """カード: 2〜4枚を横に並べる（小見出し・見出し・本文）。1枚ずつクリックで出る。accent: true で1枚を強める。

    カードの高さは中身の一番多い1枚に合わせ（最低 MIN_CARD_H）、中身の場所の上下中央に置く。幅は格子（溝 48）で割る。
    """
    head, top = header(s)
    its = s.items("items", 2, 4, ("title", "label", "body", "accent", "image", "icon", "badge"))
    imaged = [bool(it.get("image")) for it in its]
    if any(imaged) and not all(imaged):
        s.err("items の image は、書くなら全部のカードに書く（並びがそろわない）")
    n, gap = len(its), GUTTER
    cw = (CW - gap * (n - 1)) / n
    avail = s.bottom - top
    big = n <= 3
    cards, tallest = [], 400.0                 # カードの最低の高さ（.card の min-height）
    for i, it in enumerate(its):
        label, body = s.text("label", spec=it), s.text("body", spec=it)
        hl = bool(it.get("accent"))
        iw = inner_w(cw, hl)
        room = avail - box_pad(hl) - 32
        inner = ['<div class="card-bar"></div>']
        h = box_pad(hl) + 8 + 24                # 上下の余白と枠・上の短い線とその下の間
        badge = badge_of(s, it, hl, f"items の {i + 1} 個目")
        if badge:                               # 強調は色だけで示さない: 頭に札（badge か「注目」）
            inner.insert(0, chip(badge))
            room -= CHIP_H + 16
            h += CHIP_H + 16
        icon = icon_of(s, it, f"items の {i + 1} 個目")
        if icon and it.get("image"):
            s.err("カードの icon と image は一緒に書かない（どちらか）")
        if icon:                                # 上の短い線の代わりにアイコン（ADR 0019）
            inner = [icon_disc(icon, 96, 56, extra=" margin-bottom:24px;")] if not badge else [inner[0], icon_disc(icon, 96, 56, extra=" margin-bottom:24px;")]
            room -= 96 - 8
            h += 96 - 8
        if it.get("image"):                     # カードの上に 16:9 の写真（全部のカードで同じ比）
            ih = iw * 9 / 16
            pic = s.img(it["image"], f"item{i + 1}", "card-img", 0, 0, iw, ih, extra="position:static;")
            inner = [pic] if not badge else [inner[0], pic]
            room -= ih + 24 - 32
        if label:
            ls, _ = s.fit("card-label", label, 28, iw, what=f"items の {i + 1} 個目の label", lines=1, lh=1.5, min_size=28, bold=True)
            inner.append(f'<div class="card-label" style="font-size:{ls}px">{esc(label)}</div>')
            room -= ls * 1.5 + 8
            h += ls * 1.5 + 8
        ts, tn = s.fit("card-title", it["title"], s.g(45 if big else 36, 45), iw, what=f"items の {i + 1} 個目の title", lines=2, lh=1.4,
                     min_size=32, bold=True, tidy=True)
        inner.append(f'<div class="card-title" style="font-size:{ts}px">{rich(it["title"])}</div>')
        room -= tn * ts * 1.4 + 16
        h += tn * ts * 1.4 + 16
        if body:
            bs, bn = s.fit("card-body", body, s.g(32 if big else 28, 36), iw, under="card-title", what=f"items の {i + 1} 個目の body", height=room, lh=1.6, min_size=24)
            inner.append(f'<div class="card-body" style="font-size:{bs}px">{rich(body)}</div>')
            h += bn * bs * 1.6
        tallest = max(tallest, h)
        cls = "card hl" if hl else "card"
        cards.append(f'<div class="{cls}" style="width:{px(cw)}" '
                     f'{s.anim(i + 1, "rise", 0, f"カード{i + 1}", item=True)}>' + "".join(inner) + "</div>")
    s.need, s.avail = tallest, avail
    return [head, f'<div class="center" style="{box(X0, top, CW, avail)}"><div class="row" style="gap:{gap}px">'
            + "".join(cards) + "</div></div>", source_line(s)]


def t_flow(s: Slide):
    """矢羽根: 3〜5段（課題→原因→解決→効果 など）。段ごとに見出しと本文。最後（か accent: true の段）を強める。"""
    head, top = header(s)
    its = s.items("items", 3, 5, ("label", "title", "body", "accent", "badge"), required=("label",))
    n, gap, ah, depth = len(its), 8, 120, 48
    cw = (CW - gap * (n - 1)) / n
    avail = s.bottom - top
    marked = any(it.get("accent") for it in its)
    tint, accent = s.pal["tint"], s.pal["accent-strong"]     # 段の地は主役の色の淡い地（縁は primary-mark の線）。強める段は差し色で、文字を載せるので文字を載せる面の色
    room = avail - ah - 32
    big = n <= 4
    cols, tallest = [], 0.0
    for i, it in enumerate(its):
        hl = bool(it.get("accent")) if marked else i == n - 1
        lx = 24 if i == 0 else depth
        lw = cw - lx - depth
        ls, _ = s.fit("flow-label", it["label"], 36, lw, what=f"items の {i + 1} 個目の label", lines=1, lh=1.4, min_size=24, bold=True)
        tw = cw - 24
        inner, left = [], room
        badge = badge_of(s, it, hl, f"items の {i + 1} 個目") if it.get("badge") else None   # 強める段の札は、書いたときだけ（最後の段の既定は輪郭で示す）
        if badge:
            inner.append(chip(badge))
            left -= CHIP_H + 16
        title, body = s.text("title", spec=it), s.text("body", spec=it)
        if title:
            ts, tn = s.fit("flow-title", title, s.g(36 if big else 32, 45), tw, what=f"items の {i + 1} 個目の title", lines=2, lh=1.4,
                         min_size=28, bold=True, tidy=True)
            inner.append(f'<div class="flow-title" style="font-size:{ts}px">{rich(title)}</div>')
            left -= tn * ts * 1.4 + 16
        if body:
            bs, bn = s.fit("flow-body", body, s.g(28, 36), tw, under="flow-title", what=f"items の {i + 1} 個目の body", height=left, lh=1.6, min_size=24)
            inner.append(f'<div class="flow-body" style="font-size:{bs}px">{rich(body)}</div>')
            left -= bn * bs * 1.6
        tallest = max(tallest, room - left)
        color = "on-accent" if hl else "primary"
        # 強める段は濃い面＋太い輪郭（4px の ink）。面の濃さだけで示さない
        head_svg = chevron(cw, ah, depth, i == 0, accent, s.pal["ink"], 4) if hl else chevron(cw, ah, depth, i == 0, tint, s.pal["primary-mark"], 2)
        cols.append(
            f'<div class="flow-item" style="width:{px(cw)}" {s.anim(i + 1, "rise", 0, f"矢羽根{i + 1}", item=True)}>'
            f'<div class="flow-head" style="height:{ah}px">'
            + head_svg
            + f'<div class="flow-label {color}" style="{box(lx, 0, lw, ah)} font-size:{ls}px">{esc(it["label"])}</div></div>'
            + ('<div class="flow-text" style="padding:0 16px 0 8px">' + "".join(inner) + "</div>" if inner else "")
            + "</div>")
    s.need, s.avail = ah + (32 + tallest if tallest else 0), avail
    return [head, f'<div class="center" style="{box(X0, top, CW, avail)}"><div class="row" style="gap:{gap}px; align-items:flex-start">'
            + "".join(cols) + "</div></div>", source_line(s)]


def t_compare(s: Slide):
    """比較: 左右2列（before/after・A/B）。accent（right・left・none）の側を強める。左→右の順にクリックで出る。"""
    head, top = header(s)
    acc = s.spec.get("accent", "right")
    if acc not in ("right", "left", "none"):
        s.err("accent は right / left / none のどれか")
    arrow = s.spec.get("arrow", True)
    gap = 144 if arrow else GUTTER
    pw, avail = (CW - gap) / 2, s.bottom - top
    panels, tallest = [], 416.0                # 列の最低の高さ（.cmp の min-height）
    for i, side in enumerate(("left", "right")):
        sp = s.spec.get(side)
        if not isinstance(sp, dict):
            s.err(f"{side} が無い（label・title・points のオブジェクト）")
        extra = sorted(set(sp) - {"label", "title", "points", "image", "badge"})
        if extra:
            s.err(f"{side} に知らないキー: {', '.join(extra)}")
        if bool(sp.get("image")) != bool((s.spec.get("left") or {}).get("image") if isinstance(s.spec.get("left"), dict) else False):
            s.err("left.image と right.image は、書くなら両方に書く（ビフォー・アフターの写真）")
        label, title = s.text("label", spec=sp, where=f"{side}."), s.text("title", spec=sp, where=f"{side}.")
        pts = s.items("points", 0, 5, ("text",), required=("text",), spec=sp, where=f"{side}.")
        hl = acc == side
        room = avail - box_pad(hl)
        iw = inner_w(pw, hl)
        inner, h = [], box_pad(hl)
        if sp.get("image"):                         # 列の上に 16:9 の写真（ビフォー・アフター）。場所が足りなければ低くする
            ih = min(iw * 9 / 16, (avail - box_pad(hl)) * 0.45)
            inner.append(s.img(sp["image"], side, "card-img", 0, 0, iw, ih, extra="position:static;"))
            room -= ih + 24
        badge = badge_of(s, sp, hl, f"{side}.") if hl or sp.get("badge") else None   # 強める列は札（badge か「注目」）を頭に（色だけで示さない）
        if label or badge:
            row, rh = [], CHIP_H if badge else 0
            if label:
                ls, _ = s.fit("cmp-label", label, 28, iw - 48 - (text_width(badge, 24, True) + 64 if badge else 0), what=f"{side}.label", lines=1, lh=1.0, min_size=24, bold=True)
                row.append(f'<div class="cmp-chip" style="font-size:{ls}px">{esc(label)}</div>')
                rh = max(rh, ls + 16)
            if badge:
                row.append(chip(badge))
            inner.append(f'<div class="chips">{"".join(row)}</div>')
            room -= rh + 24
            h += rh + 24
        if title:
            ts, tn = s.fit("cmp-title", title, 45, iw, what=f"{side}.title", lines=2, lh=1.4, min_size=32, bold=True, tidy=True)
            inner.append(f'<div class="cmp-title" style="font-size:{ts}px">{rich(title)}</div>')
            room -= tn * ts * 1.4 + 16
            h += tn * ts * 1.4 + 16
        if pts:
            per = room / len(pts) - 16
            for j, p in enumerate(pts):
                fs, fn_ = s.fit("cmp-point", p["text"], s.g(32, 36), iw - 40, under="cmp-title", what=f"{side}.points の {j + 1} 個目", height=per, lh=1.5, min_size=24)
                h += 16 + fn_ * fs * 1.5
                dot_y = (fs * 1.5 - 16) / 2
                inner.append(f'<div class="cmp-point"><div class="cmp-dot" style="margin-top:{dot_y:.0f}px"></div>'
                             f'<div class="cmp-text" style="font-size:{fs}px">{rich(p["text"])}</div></div>')
        if not inner:
            s.err(f"{side} が空")
        tallest = max(tallest, h)
        cls = "cmp hl" if hl else "cmp"
        if i == 1 and arrow:
            fill = s.pal["accent-mark"] if acc == "right" else s.pal["bar"]
            panels.append(f'<div class="cmp-gap" style="width:{gap}px"><svg width="56" height="80" viewBox="0 0 56 80" '
                          f'{s.anim(2, "wipe-right", 0, "比較の矢印")}><polygon points="0,0 56,40 0,80" fill="{fill}"/></svg></div>')
        elif i == 1:
            panels.append(f'<div style="width:{gap}px; flex:none"></div>')
        panels.append(f'<div class="{cls}" style="width:{px(pw)}" '
                      f'{s.anim(i + 1, "rise", 150 * i, "左の列" if i == 0 else "右の列", item=True)}>' + "".join(inner) + "</div>")
    s.need, s.avail = tallest, avail
    return [head, f'<div class="center" style="{box(X0, top, CW, avail)}"><div class="row">' + "".join(panels) + "</div></div>",
            source_line(s)]


def t_bar_chart(s: Slide):
    """棒グラフ: 値から高さを計算して図形で描く。highlight の棒を accent に。棒はまとめて1クリックで下から出る。"""
    head, top = header(s)
    bars = s.items("bars", 2, 8, ("label", "value", "highlight", "display"), required=("label", "value"))
    for i, b in enumerate(bars):
        if isinstance(b["value"], bool) or not isinstance(b["value"], (int, float)) or b["value"] < 0:
            s.err(f"bars の {i + 1} 個目の value は 0 以上の数で書く（{b['value']!r}）")
    unit, note = s.text("unit", default=""), s.text("note")
    top_v = max(b["value"] for b in bars)
    vmax = s.spec.get("max", top_v if top_v > 0 else 1)     # 全部 0 でも軸の上限は正にする
    if isinstance(vmax, bool) or not isinstance(vmax, (int, float)) or vmax < top_v or vmax <= 0:
        s.err(f"max は棒の最大値（{top_v}）以上の数")
    callout = s.spec.get("callout")
    if callout is not None and (not isinstance(callout, dict) or not callout.get("value")):
        s.err("callout は value（と text）のオブジェクト")
    right = col_x(9) - GUTTER if callout else X0 + CW     # 要点の枠は格子の右 4 列
    base = s.bottom - (112 if note else 72)
    vh = 36 * 1.4 + 8
    plot_h = base - top - vh - 8
    if plot_h < 200:
        s.err("見出しが高すぎてグラフの場所が足りない（見出しを短くするか sub を外す）")
    slot = (right - X0) / len(bars)
    bw = min(160, slot * 0.56)
    parts = [head, f'<div class="baseline" style="{box(X0, base, right - X0)}" {s.anim(0, "fade", 200, "グラフの軸")}></div>']
    for i, b in enumerate(bars):
        hl = bool(b.get("highlight"))
        shown = s.text("display", spec=b) or f"{num_label(b['value'])}{unit}"
        vs, _ = fit(shown, 36, slot - 16, what=f"bars の {i + 1} 個目の表示", lines=1, lh=1.4, min_size=24, bold=True)
        bh = max(4.0, b["value"] / vmax * plot_h)
        gx = X0 + i * slot
        color = "; color:var(--accent-text)" if hl else ""
        parts.append(f'<div class="bar-group" style="{box(gx, base - bh - vh, slot, bh + vh)}" '
                     f'{s.anim(1, "wipe-up", 120 * i, f"棒{i + 1}")}>'
                     f'<div class="bar-value" style="font-size:{vs}px{color}" data-name="値{i + 1}">{esc(shown)}</div>'
                     f'<div class="bar{" hl" if hl else ""}" style="width:{px(bw)}; height:{px(bh)}"></div></div>')
        ls, _ = fit(b["label"], 28, slot - 16, what=f"bars の {i + 1} 個目の label", lines=1, lh=1.5, min_size=22, bold=hl)
        parts.append(f'<div class="bar-label{" hl" if hl else ""}" style="{box(gx, base + 16, slot)} font-size:{ls}px" '
                     f'{s.anim(0, "fade", 200, f"項目{i + 1}")}>{esc(b["label"])}</div>')
    if note:
        ns, _ = fit(note, 22, right - X0, what="note", lines=1, lh=1.5, min_size=22)
        parts.append(f'<div class="bar-unit" style="{box(X0, base + 64, right - X0)} font-size:{ns}px" data-name="注記">{esc(note)}</div>')
    if callout:
        parts.append(callout_box(s, callout, top, base - top, s.anim(1, "fade", 120 * len(bars) + 300, "グラフの要点")))
    parts.append(source_line(s))
    return parts


def callout_box(s: Slide, callout: dict, top: float, height: float, anim: str) -> str:
    """グラフの右の要点の枠（数字＋説明）。格子の右 4 列・外周は差し色の線 2px（淡い地と地を 3:1 で分ける）。"""
    cv, ct = s.text("value", spec=callout), s.text("text", spec=callout)
    cs, _ = fit(cv, 96, CALLOUT_IW, what="callout.value", lines=1, lh=1.2, min_size=57, bold=True)
    inner = f'<div class="callout-value" style="font-size:{cs}px">{esc(cv)}</div>'
    if ct:
        ts, _ = fit(ct, 28, CALLOUT_IW, what="callout.text", lines=6, lh=1.6, min_size=24)
        inner += f'<div class="callout-text" style="font-size:{ts}px">{rich(ct)}</div>'
    return f'<div class="callout" style="{box(col_x(9), top, CALLOUT_W, height)}" {anim}>{inner}</div>'


def t_table(s: Slide):
    """表: 見出しの行＋2〜7行。highlight（列の番号・1から）の列を強める。表は1クリックでまとめて出る。"""
    head, top = header(s)
    cols = s.spec.get("columns")
    rows = s.spec.get("rows")
    if not isinstance(cols, list) or not 2 <= len(cols) <= 5:
        s.err("columns は 2〜5 個の配列")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 7:
        s.err("rows は 1〜7 行の配列")
    for i, r in enumerate(rows, 1):
        if not isinstance(r, list) or len(r) != len(cols):
            s.err(f"rows の {i} 行目の数が columns（{len(cols)} 列）と合わない")
    hl = s.spec.get("highlight")
    if hl is not None and (not isinstance(hl, int) or not 1 <= hl <= len(cols)):
        s.err(f"highlight は 1〜{len(cols)} の整数")
    weights = s.spec.get("widths") or [1.4] + [1] * (len(cols) - 1)
    if not isinstance(weights, list) or len(weights) != len(cols) or not all(isinstance(v, (int, float)) and v > 0 for v in weights):
        s.err("widths は columns と同じ数の正の数")
    total = sum(weights)
    cws = [CW * v / total for v in weights]
    note_h = 40 if s.text("source") else 0
    rh = min(112, int((s.bottom - top - note_h) / (len(rows) + 1)) // 8 * 8)
    fs0 = 28 if rh >= 64 else 24                       # 表のセルは Std-28N-150（下限 24）。行が低ければ 24
    max_lines = max(1, int((rh - 16) / (fs0 * 1.5)))
    out = []
    for r, row in enumerate([cols] + rows):
        cells = []
        for c, v in enumerate(row):
            v = num_label(v) if not isinstance(v, str) else v
            on = hl == c + 1                                  # 強める列は太字と太い縦の罫（色の地だけにしない）
            bold = r == 0 or c == 0 or on
            fs, _ = fit(v, fs0, cws[c] - (56 if on else 48), what=f"表の {r} 行目 {c + 1} 列目" if r else f"columns の {c + 1} 個目",
                        lines=max_lines, lh=1.5, min_size=24, bold=bold)
            align = "flex-start" if c == 0 else "center"
            tal = "left" if c == 0 else "center"
            weight = " font-weight:700;" if bold else ""
            cells.append(f'<div class="td{" hl" if on else ""}" style="width:{px(cws[c])}; justify-content:{align}">'
                         f'<div style="font-size:{fs}px; text-align:{tal};{weight}">{rich(v)}</div></div>')
        out.append(f'<div class="tr{" th" if r == 0 else ""}" style="height:{px(rh)}">' + "".join(cells) + "</div>")
    return [head, f'<div class="table" style="{box(X0, top, CW)}" {s.anim(1, "fade", 0, "表")}>' + "".join(out) + "</div>",
            source_line(s, top + rh * (len(rows) + 1) + 16)]


def t_photo(s: Slide):
    """写真: 全面（full）か左右（left・right）か横長の帯（band）。主張の文字は写真の上に重ねる（full）か、写真の外の地に置く。

    band は上に横長の写真の帯（1920×BAND_H・パノラマ）を置き、その下に見出しと本文。下の文字・ページ番号も出す

    left・right は split で写真の幅を選ぶ: 1/2（960・既定）・2/5（768）・1/3（640）。写真が脇役で文字が主役なら細く
    """
    layout = s.spec.get("layout", "full")
    if layout not in ("full", "left", "right", "band"):
        s.err("layout は full / left / right / band のどれか")
    split = s.spec.get("split", "1/2")
    if split not in SPLITS:
        s.err(f"split は {' / '.join(SPLITS)} のどれか")
    if layout in ("full", "band") and "split" in s.spec:
        s.err("split は layout: left・right のときだけ")
    if layout == "band":
        return _photo_band(s)
    head = s.text("headline", required=True)
    kicker, body, caption = s.text("kicker"), s.text("body"), s.text("caption")
    if layout == "full":
        tw = 992
        hs, _ = fit(head, 57, tw, what="見出し（headline）", lines=3, lh=1.4, min_size=45, bold=True)
        inner = []
        if kicker:
            ks, _ = fit(kicker, 28, tw, what="kicker", lines=1, lh=1.5, min_size=28, bold=True)
            inner.append(f'<div class="kicker" style="font-size:{ks}px; color:#ffffff">{esc(kicker)}</div>')
        inner.append(f'<h1 class="headline" style="font-size:{hs}px; line-height:1.4">{rich(head)}</h1>')
        if body:
            bs, _ = fit(body, 32, tw, what="補足（body）", lines=3, lh=1.5, min_size=28)
            inner.append(f'<div class="sub" style="font-size:{bs}px">{rich(body)}</div>')
        if caption:
            cs, _ = fit(caption, 22, tw, what="caption", lines=1, lh=1.5, min_size=22)
            inner.append(f'<div class="sub" style="font-size:{cs}px; margin-top:16px">{esc(caption)}</div>')
        s.overlays.setdefault("photo（全面）", s.no)
        return [s.img(s.spec.get("image"), "image", "photo", 0, 0, W, H, anim=s.anim(0, "fade", 0, "写真"), clear="左下"),
                f'<div class="scrim" style="left:{X0}px; bottom:{X0}px; width:1104px" {s.anim(0, "rise", 400, "写真の主張")}>'
                + "".join(inner) + "</div>"]
    iw = SPLITS[split]
    tw = W - iw - 2 * X0
    ix, tx = (0, iw + X0) if layout == "left" else (W - iw, X0)
    hs, _ = fit(head, 57, tw, what="見出し（headline）", lines=4, lh=1.4, min_size=45, bold=True)
    inner = []
    if kicker:
        ks, _ = fit(kicker, 28, tw, what="kicker", lines=1, lh=1.5, min_size=28, bold=True)
        inner.append(f'<div class="kicker" style="font-size:{ks}px">{esc(kicker)}</div>')
    inner.append(f'<h1 class="headline" style="font-size:{hs}px; line-height:1.4">{rich(head)}</h1>')
    if body:
        bs, _ = fit(body, 32, tw, what="補足（body）", lines=6, lh=1.5, min_size=28)
        inner.append(f'<div class="sub" style="font-size:{bs}px; margin-top:24px">{rich(body)}</div>')
    parts = [s.img(s.spec.get("image"), "image", "photo", ix, 0, iw, H, anim=s.anim(0, "fade", 0, "写真")),
             f'<div class="photo-text" style="{box(tx, HEAD_TOP, tw, BODY_BOTTOM - HEAD_TOP)}" {s.anim(0, "rise", 300, "写真の主張")}>'
             + "".join(inner) + "</div>"]
    if caption:
        cs, _ = fit(caption, 22, tw, what="caption", lines=1, lh=1.5, min_size=22)
        parts.append(f'<div class="caption" style="{box(tx, BODY_BOTTOM + 8, tw)} font-size:{cs}px" data-name="写真の出典">{esc(caption)}</div>')
    return parts


def _photo_band(s: Slide):
    """横長の帯の写真（上）＋見出しと本文（下）。"""
    s.chrome, s.no_band = True, True             # 下の文字・ページ番号は出す。上の帯の印は写真に重ねない
    parts = [s.img(s.spec.get("image"), "image", "photo", 0, 0, W, BAND_H, anim=s.anim(0, "fade", 0, "帯の写真"))]
    caption, body = s.text("caption"), s.text("body")
    if caption:
        cs, _ = fit(caption, 22, CW, what="caption", lines=1, lh=1.5, min_size=22)
        parts.append(f'<div class="caption" style="{box(X0, BAND_H + 8, CW)} font-size:{cs}px; text-align:right" '
                     f'data-name="写真の出典">{esc(caption)}</div>')
    head, top = header(s, top=BAND_H + 64)
    parts.append(head)
    if body:
        room = BODY_BOTTOM - (top - BODY_GAP + 24)
        bs, _ = fit(body, 32, CW, what="本文（body）", height=min(room, 32 * 1.6 * 3), lh=1.6, min_size=28)
        parts.append(f'<div class="sub" style="{box(X0, top - BODY_GAP + 24, CW)} position:absolute; font-size:{bs}px; '
                     f'line-height:1.6; color:var(--ink)" {s.anim(0, "rise", 300, "本文")}>{rich(body)}</div>')
    return parts


def t_quote(s: Slide):
    """引用: 言葉を大きく、誰の言葉か（by・position）を下に。position は肩書き（role は章の ID に使うので別の名前）。

    layout: plain（既定）／full（全面の写真に一様な暗幕 QUOTE_ALPHA を敷き、白い文字）／portrait（左に人物の丸い写真）
    """
    q, by, role, kicker = s.text("quote", required=True), s.text("by"), s.text("position"), s.text("kicker")
    layout = s.spec.get("layout", "plain")
    if layout not in ("plain", "full", "portrait"):
        s.err("layout は plain / full / portrait のどれか")
    if layout != "plain" and not s.spec.get("image"):
        s.err(f"layout: {layout} には image が要る")
    if layout == "plain" and s.spec.get("image"):
        s.err("image を使うなら layout を full か portrait にする")
    pre, qx, tw = [], 200, 1480
    if layout == "full":
        s.chrome = False                         # 写真の上に下の文字・ページ番号を出さない
        s.overlays.setdefault("quote（全面）", s.no)
        pre = [s.img(s.spec["image"], "image", "photo", 0, 0, W, H, anim=s.anim(0, "fade", 0, "引用の写真"), clear="全体（中央に引用を重ねる）"),
               f'<div class="veil" style="{box(0, 0, W, H)} background-color:var(--scrim-quote);" data-name="引用の下の幕"></div>']
    elif layout == "portrait":
        d = 320
        pre = [s.img(s.spec["image"], "image", "face", X0, (H - d) / 2, d, d, extra=f"position:absolute; border-radius:{FULL}px;",
                     anim=s.anim(0, "fade", 0, "引用の主の写真"))]
        qx, tw = X0 + d + 96, W - (X0 + d + 96) - X0
    qs, _ = fit(q, 57, tw, what="引用（quote）", lines=4, lh=1.5, min_size=36, bold=True, tidy=True)
    inner = []
    if kicker:
        ks, _ = fit(kicker, 28, tw, what="kicker", lines=1, lh=1.5, min_size=28, bold=True)
        inner.append(f'<div class="kicker" style="font-size:{ks}px" data-name="小見出し">{esc(kicker)}</div>')
    inner.append(f'<div class="qmark" {s.anim(0, "fade", 0, "引用符")}>“</div>')
    inner.append(f'<div class="quote-text" style="font-size:{qs}px" {s.anim(0, "rise", 150, "引用")}>{rich(q)}</div>')
    if by or role:
        bs, _ = fit(f"— {by}　{role}", 32, tw, what="by と position", lines=1, lh=1.4, min_size=24, bold=True)
        rs = step_down(bs, 1, 24) if bs > 24 else bs   # 肩書きは名前より1段小さく
        inner.append(f'<div class="quote-by" {s.anim(0, "fade", 600, "引用の主")}>'
                     + (f'<div class="quote-name" style="font-size:{bs}px">— {esc(by)}</div>' if by else "")
                     + (f'<div class="quote-role" style="font-size:{rs}px">{esc(role)}</div>' if role else "") + "</div>")
    od = " on-dark" if layout == "full" else ""
    return pre + [f'<div class="quote{od}" style="{box(qx, HEAD_TOP, tw, BODY_BOTTOM - HEAD_TOP)}">' + "".join(inner) + "</div>",
                  source_line(s, on_dark=layout == "full")]


def _list_rows(s: Slide, its, width, room, numbered, step_each):
    """箇条書きの行（印か番号＋title＋body）。room に入らなければ DeckError。

    題は Std-45B-140（下限 32）・本文は Std-28N-160。印は 16px の四角（primary-mark）・番号は 64px の丸・アイコンは 56px（リードアイコン）
    """
    gap = s.gap(40)
    iconed = [bool(it.get("icon")) for it in its] if not numbered else []
    if any(iconed) and not all(iconed):
        s.err("items の icon は、書くなら全部の項目に書く（印の幅がそろわない）")
    lead = 96 if numbered else 72 if any(iconed) else 48
    per = (room - gap * (len(its) - 1)) / len(its)
    rows, need = [], gap * (len(its) - 1)
    for i, it in enumerate(its):
        tw = width - lead
        ts, tn = s.fit("li-title", it["title"], s.g(45, 45), tw, what=f"items の {i + 1} 個目の title", lines=2, lh=1.4, min_size=32, bold=True, tidy=True)
        inner = f'<div class="li-title" style="font-size:{ts}px">{rich(it["title"])}</div>'
        h = tn * ts * 1.4
        body = s.text("body", spec=it)
        if body:
            bs, bn = s.fit("li-body", body, s.g(28, 36), tw, under="li-title", what=f"items の {i + 1} 個目の body", height=per - tn * ts * 1.4 - 8, lh=1.6, min_size=24)
            inner += f'<div class="li-body" style="font-size:{bs}px">{rich(body)}</div>'
            h += 8 + bn * bs * 1.6
        elif tn * ts * 1.4 > per:
            s.err(f"items が多すぎて入らない（{len(its)} 個）")
        need += max(h, 64 if numbered else 0)    # 番号の丸（.li-no）は 64px
        icon = None if numbered else icon_of(s, it, f"items の {i + 1} 個目")
        mark = (f'<div class="li-no">{i + 1}</div>' if numbered
                else ICON.svg(icon, 56, "var(--primary-text)").replace("<svg ", f'<svg class="li-icon" style="margin-top:{(ts * 1.4 - 56) / 2:.0f}px" ', 1)
                if icon else f'<div class="li-mark" style="margin-top:{(ts * 1.4 - 16) / 2:.0f}px"></div>')
        a = s.anim(i + 1, "rise", 0, f"項目{i + 1}", item=True) if step_each else s.anim(0, "rise", 300 + 150 * i, f"項目{i + 1}")
        margin = f"margin-top:{gap}px;" if i else ""
        rows.append(f'<div class="li" style="{margin}" {a}>{mark}<div class="li-text">{inner}</div></div>')
    s.need, s.avail = need, room
    return "".join(rows)


def t_bullets(s: Slide):
    """箇条書き: 2〜5項目（見出しと本文）。image があれば脇に写真（side: right 既定・left、split: 1/3 既定・2/5・1/2）。
    1項目ずつクリックで出る。"""
    head, top = header(s)
    its = s.items("items", 2, 5, ("title", "body", "icon"))
    width, parts, lx = CW, [head], X0
    side, split = s.spec.get("side", "right"), s.spec.get("split", "1/3")
    if side not in ("left", "right"):
        s.err("side は right か left（写真を置く側）")
    if split not in SPLITS:
        s.err(f"split は {' / '.join(SPLITS)} のどれか")
    if s.spec.get("image"):
        pw = {"1/3": span(4), "2/5": span(5), "1/2": span(6)}[split]      # 写真は格子の 4・5・6 列
        px_ = X0 + CW - pw if side == "right" else X0
        parts.append(s.img(s.spec["image"], "image", "side-img", px_, top, pw, s.bottom - top, anim=s.anim(0, "fade", 200, "写真")))
        width = CW - pw - 96
        lx = X0 if side == "right" else X0 + pw + 96
    elif "side" in s.spec or "split" in s.spec:
        s.err("side・split は image があるときだけ")
    parts.append(f'<div class="list" style="{box(lx, top, width, s.bottom - top)}">'
                 + _list_rows(s, its, width, s.bottom - top, False, True) + "</div>")
    parts.append(source_line(s))
    return parts


def t_closing(s: Slide):
    """締め: 次の一手（番号つき1〜4）と連絡先。contact だけ・items だけでもよい。

    image があれば右に写真（幅 CLOSING_IMG_W・上下いっぱい）を置き、次の一手と連絡先は左に縦に積む。下の文字・ページ番号は出さない
    """
    has_img = bool(s.spec.get("image"))
    left_w = W - CLOSING_IMG_W - X0 - 96 if has_img else CW
    head, top = header(s, width=left_w)
    its = s.items("items", 0, 4, ("title", "body"))
    contact = s.spec.get("contact")
    if not its and not contact:
        s.err("items か contact のどちらかは書く")
    avail = BODY_BOTTOM - top
    parts = [head]
    width = CW
    if has_img:
        s.chrome = False                         # 写真の上にページ番号を出さない
        parts.append(s.img(s.spec["image"], "image", "photo", W - CLOSING_IMG_W, 0, CLOSING_IMG_W, H,
                           anim=s.anim(0, "fade", 0, "締めの写真")))
        return parts + _closing_stack(s, its, contact, top, left_w)
    if contact:
        if isinstance(contact, str):
            contact = {"lines": [contact]}
        if isinstance(contact, list):
            contact = {"lines": contact}
        if not isinstance(contact, dict):
            s.err("contact は文字・文字の配列・{label, name, lines} のどれか")
        extra = sorted(set(contact) - {"label", "name", "lines"})
        if extra:
            s.err(f"contact に知らないキー: {', '.join(extra)}")
        lines = contact.get("lines") or []
        if isinstance(lines, str):
            lines = [lines]
        cw = span(5) if its else span(7)
        inner = f'<div class="contact-label">{esc(contact.get("label") or "お問い合わせ")}</div>'
        if contact.get("name"):
            ns, _ = fit(contact["name"], 36, cw - 2 * CALLOUT_PAD, what="contact.name", lines=2, lh=1.4, min_size=28, bold=True)
            inner += f'<div class="contact-strong" style="font-size:{ns}px">{esc(contact["name"])}</div>'
        for ln in lines:
            ls, _ = fit(str(ln), 28, cw - 2 * CALLOUT_PAD, what="contact.lines", lines=1, lh=1.5, min_size=24)
            inner += f'<div class="contact-line" style="font-size:{ls}px">{esc(ln)}</div>'
        cx = X0 + CW - cw if its else X0           # 連絡先だけなら左に寄せる（右に寄せると左が空く）
        parts.append(f'<div class="center" style="{box(cx, top, cw, avail)}">'
                     f'<div class="contact" {s.anim(0, "fade", 300 + 150 * len(its), "連絡先")}>{inner}</div></div>')
        width = CW - cw - 96
    if its:
        parts.append(f'<div class="list" style="{box(X0, top, width, avail)}">'
                     + _list_rows(s, its, width, avail, True, False) + "</div>")
    parts.append(source_line(s))
    return parts


def _contact(s: Slide, contact, cw):
    """連絡先の箱の中身と、その高さの見積もり。"""
    if isinstance(contact, str):
        contact = {"lines": [contact]}
    if isinstance(contact, list):
        contact = {"lines": contact}
    if not isinstance(contact, dict):
        s.err("contact は文字・文字の配列・{label, name, lines} のどれか")
    extra = sorted(set(contact) - {"label", "name", "lines"})
    if extra:
        s.err(f"contact に知らないキー: {', '.join(extra)}")
    lines = contact.get("lines") or []
    if isinstance(lines, str):
        lines = [lines]
    inner = f'<div class="contact-label">{esc(contact.get("label") or "お問い合わせ")}</div>'
    h = 2 * CALLOUT_PAD + 28 * 1.5 + 24
    if contact.get("name"):
        ns, nn = fit(contact["name"], 36, cw - 2 * CALLOUT_PAD, what="contact.name", lines=2, lh=1.4, min_size=28, bold=True)
        inner += f'<div class="contact-strong" style="font-size:{ns}px">{esc(contact["name"])}</div>'
        h += nn * ns * 1.4 + 8
    for ln in lines:
        ls, _ = fit(str(ln), 28, cw - 2 * CALLOUT_PAD, what="contact.lines", lines=1, lh=1.5, min_size=24)
        inner += f'<div class="contact-line" style="font-size:{ls}px">{esc(ln)}</div>'
        h += ls * 1.5
    return inner, h


def _closing_stack(s: Slide, its, contact, top, width):
    """写真つきの締めの左の列: 上に次の一手、下に連絡先。"""
    parts = []
    bottom = BODY_BOTTOM
    if contact:
        inner, ch = _contact(s, contact, width)
        if bottom - ch < top:                    # 連絡先は下から積む。見出しの下端（中身の上端）より上に出たら重なる
            s.err(f"連絡先が長すぎて見出しに重なる（連絡先 {ch:.0f}px・入るのは {bottom - top:.0f}px。lines を減らすか見出しを短くする）")
        parts.append(f'<div class="contact" style="{box(X0, bottom - ch, width)} position:absolute;" '
                     f'{s.anim(0, "fade", 300 + 150 * len(its), "連絡先")}>{inner}</div>')
        bottom -= ch + 40
    if its:
        if bottom - top < 160:
            s.err("見出しと連絡先で場所が埋まり、次の一手が入らない（見出しか連絡先を短くする）")
        parts.append(f'<div class="list" style="{box(X0, top, width, bottom - top)}">'
                     + _list_rows(s, its, width, bottom - top, True, False) + "</div>")
    parts.append(source_line(s, width=width))     # 出典も左の列の幅で（右の写真に重ねない）
    return parts


def t_timeline(s: Slide):
    """年表: 3〜6の時期を横の線に並べる（when・title・body）。current（1から）を強める。1クリックで左から出る。

    線と時期のまとまりを中身の場所の上下中央に置く（線は1本目の丸の中心の高さ）。
    """
    head, top = header(s)
    its = s.items("items", 3, 6, ("when", "title", "body"), required=("when", "title"))
    cur = s.spec.get("current")
    if cur is not None and (not isinstance(cur, int) or not 1 <= cur <= len(its)):
        s.err(f"current は 1〜{len(its)} の整数")
    avail = s.bottom - top
    slot = CW / len(its)
    tw = slot - 48
    big = len(its) <= 4
    wl = 36 * 1.4                              # 時期の行の高さ。箱の高さに書くので、縮めた時期があっても丸と線の高さはそろう
    cr = CHIP_H + 8 if cur else 0              # 「いま」の札の行（current があるとき、全部の時期で同じ高さを空ける）
    dot = 48                                   # 丸の枠（いまの二重丸が 48・ふつうの丸は 32 で、枠の中で縦の中央にそろえる）
    room = avail - cr - wl - 16 - dot - 24
    cols, tallest = [], 0.0
    for i, it in enumerate(its):
        ws, _ = s.fit("tl-when", it["when"], 36, tw, what=f"items の {i + 1} 個目の when", lines=1, lh=1.4, min_size=24, bold=True)
        ts, tn = s.fit("tl-title", it["title"], s.g(36 if big else 32, 45), tw, what=f"items の {i + 1} 個目の title", lines=2, lh=1.4,
                     min_size=28, bold=True, tidy=True)
        now = cur == i + 1
        mark = '<div class="tl-dot hl"><div class="tl-dot-in"></div></div>' if now else '<div class="tl-dot"></div>'   # いまは二重丸
        inner = ((f'<div class="chip on" style="margin-bottom:8px">いま</div>' if now else f'<div style="height:{cr}px"></div>' if cur else "")
                 + f'<div class="tl-when" style="font-size:{ws}px; height:{wl:.1f}px">{esc(it["when"])}</div>'
                 f'<div class="tl-dotbox" style="margin-top:16px; height:{dot}px">{mark}</div>'
                 f'<div class="tl-title" style="font-size:{ts}px">{rich(it["title"])}</div>')
        h = tn * ts * 1.4
        body = s.text("body", spec=it)
        if body:
            bs, bn = s.fit("tl-body", body, s.g(28 if big else 24, 32), tw, under="tl-title", what=f"items の {i + 1} 個目の body", height=room - h - 8, lh=1.6, min_size=24)
            inner += f'<div class="tl-body" style="font-size:{bs}px">{rich(body)}</div>'
            h += 8 + bn * bs * 1.6
        tallest = max(tallest, h)
        cls = "tl-item hl" if cur == i + 1 else "tl-item"
        cols.append((cls, inner, i))
    group = cr + wl + 16 + dot + 24 + tallest
    gtop = top + max(0.0, (avail - group) / 2)
    line_y = gtop + cr + wl + 16 + dot / 2       # 丸の枠（.tl-dotbox・48px）の中心
    s.need, s.avail = group, avail
    parts = [head, f'<div class="tl-line" style="{box(X0, line_y - 2, CW)}" {s.anim(1, "wipe-right", 0, "年表の線")}></div>']
    for cls, inner, i in cols:
        parts.append(f'<div class="{cls}" style="{box(X0 + i * slot, gtop, tw)}" '
                     f'{s.anim(1, "rise", 300 + 150 * i, f"時期{i + 1}")}>{inner}</div>')
    parts.append(source_line(s))
    return parts


def t_summary(s: Slide):
    """要約: 結論1文（見出し）と、それを支える根拠2〜3。根拠はまとめて1クリックで出る。kicker は省くと「結論」。"""
    head, top = header(s, default_kicker="結論")
    pts = s.items("points", 2, 3, ("title", "body", "value"))
    label = s.text("label", default="根拠")
    n, gap = len(pts), GUTTER
    cw = (CW - gap * (n - 1)) / n
    avail = s.bottom - top
    room = avail - 8 - 24 - 28 * 1.5 - 16
    cols, tallest = [], 0.0
    for i, p in enumerate(pts):
        inner = f'<div class="sum-bar"></div><div class="sum-no">{esc(label)}{i + 1}</div>'
        left = room
        v = s.text("value", spec=p)
        if v:
            vs, _ = s.fit("sum-value", v, s.g(64, 64), cw, what=f"points の {i + 1} 個目の value", lines=1, lh=1.4, min_size=45, bold=True)
            inner += f'<div class="sum-value" style="font-size:{vs}px">{esc(v)}</div>'
            left -= vs * 1.4 + 8
        ts, tn = s.fit("sum-title", p["title"], s.g(36, 45), cw, what=f"points の {i + 1} 個目の title", lines=3, lh=1.4, min_size=28, bold=True)
        inner += f'<div class="sum-title" style="font-size:{ts}px">{rich(p["title"])}</div>'
        left -= tn * ts * 1.4 + 16
        body = s.text("body", spec=p)
        if body:
            bs, bn = s.fit("sum-body", body, s.g(28, 36), cw, under="sum-title", what=f"points の {i + 1} 個目の body", height=left, lh=1.6, min_size=24)
            left -= bn * bs * 1.6
            inner += f'<div class="sum-body" style="font-size:{bs}px">{rich(body)}</div>'
        tallest = max(tallest, room - left)
        cols.append(f'<div class="sum-col" style="width:{px(cw)}" {s.anim(1, "rise", 150 * i, f"根拠{i + 1}", item=True)}>'
                    f'{inner}</div>')
    s.need, s.avail = 8 + 24 + 28 * 1.5 + tallest, avail
    return [head, f'<div class="center" style="{box(X0, top, CW, avail)}"><div class="row" style="gap:{gap}px; align-items:flex-start">'
            + "".join(cols) + "</div></div>", source_line(s)]


TRENDS = {"up": "▲ ", "down": "▼ ", "flat": "→ "}


def t_kpi(s: Slide):
    """指標の並び: 同格の数字3〜4（ラベル・数字・単位・前年比などの増減）。まとめて1クリックで出る。accent: true の1つを強める。

    image があれば、上に横長の写真の帯（高さ KPI_BAND_H・現場の写真）を敷き、見出しと数字はその下に置く
    """
    pre = []
    if s.spec.get("image"):
        s.no_band = True                         # 上の帯（アクセントの印）は写真に重ねない
        pre = [s.img(s.spec["image"], "image", "photo", 0, 0, W, KPI_BAND_H, anim=s.anim(0, "fade", 0, "現場の写真"))]
        head, top = header(s, top=KPI_BAND_H + 48)
    else:
        head, top = header(s)
    its = s.items("items", 3, 4, ("label", "value", "unit", "delta", "trend", "note", "accent", "badge"), required=("label", "value"))
    n, gap = len(its), GUTTER
    cw = (CW - gap * (n - 1)) / n
    for i, it in enumerate(its):
        if it.get("trend") is not None and it["trend"] not in TRENDS:
            s.err(f"items の {i + 1} 個目の trend は up / down / flat のどれか")
    avail = s.bottom - top
    vmax = (96 if n == 3 else 80) if not pre else (80 if n == 3 else 64)
    for drop in range(0, 6):                    # カードの高さが入るまで、全部のカードの文字を同じだけ段を下りる
        tiles, tallest = _kpi_tiles(s, its, cw, vmax, drop)
        if tallest <= avail:
            break
    else:
        s.err(f"数字のカードが高すぎて入らない（{tallest:.0f}px > {avail:.0f}px。label・note を短くするか見出しを1行にする）")
    return pre + [head, f'<div class="center" style="{box(X0, top, CW, avail)}"><div class="row" style="gap:{gap}px">'
                  + "".join(tiles) + "</div></div>", source_line(s)]


def _kpi_tiles(s: Slide, its, cw, vmax, drop):
    """kpi のカード（文字は段を drop だけ下りる）。(HTML の列, 一番高いカードの高さの見積もり) を返す。

    ラベル・増減は Std-28B-150・数字は Ext-96B-100（4 つなら 80）・単位は数字のおよそ 4 割・注記は Std-22N-150
    """
    tiles, tallest = [], 0.0
    for i, it in enumerate(its):
        where = f"items の {i + 1} 個目"
        label, value, unit = s.text("label", spec=it), s.text("value", spec=it), s.text("unit", spec=it)
        delta, note, trend = s.text("delta", spec=it), s.text("note", spec=it), it.get("trend")
        hl = bool(it.get("accent"))
        iw = inner_w(cw, hl)
        ls, ln = fit(label, step_down(28, drop, 24), iw, what=f"{where}の label", lines=2, lh=1.5, min_size=24, bold=True)
        vsz = step_down(vmax, drop, 57)
        for vs in (v for v in SCALE if v <= vsz):          # 数字と単位が1行に入る段まで下りる
            uw = text_width(unit, _unit_size(vs), True) + 16 if unit else 0
            if count_lines(value, vs, iw - uw, True) == 1:
                break
            if vs <= 45:
                s.err(f"{where}の value が長すぎて入らない（{vsz}px から 45px まで下りても 1 行を越える。幅 {iw - uw:.0f}px）: {plain(value)}")
        us = _unit_size(vs)
        h = box_pad(hl) + ln * ls * 1.5 + 16 + vs * (1.0 if vs >= 96 else 1.4)
        vlh = 1.0 if vs >= 96 else 1.4
        badge = badge_of(s, it, hl, where)          # 強める指標は札（badge か「注目」）を頭に
        if badge:
            h += CHIP_H + 16
        inner = (chip(badge) if badge else "") + (f'<div class="kpi-label" style="font-size:{ls}px">{esc(label)}</div>'
                 f'<div class="bn-row" style="margin-top:16px"><div class="kpi-value" style="font-size:{vs}px; line-height:{vlh}">{esc(value)}</div>'
                 + (f'<div class="kpi-unit" style="font-size:{us}px; line-height:{vlh}">{esc(unit)}</div>' if unit else "") + "</div>")
        if delta:
            d = TRENDS.get(trend, "") + delta
            ds, _ = fit(d, step_down(28, drop, 24), iw, what=f"{where}の delta", lines=1, lh=1.5, min_size=24, bold=True)
            inner += f'<div class="kpi-delta" style="font-size:{ds}px">{esc(d)}</div>'
            h += 16 + ds * 1.5
        if note:
            ns, nn = fit(note, 22, iw, what=f"{where}の note", lines=2, lh=1.5, min_size=22)
            inner += f'<div class="kpi-note" style="font-size:{ns}px">{esc(note)}</div>'
            h += 8 + nn * ns * 1.5
        tallest = max(tallest, h)
        cls = "kpi hl" if hl else "kpi"
        tiles.append(f'<div class="{cls}" style="width:{px(cw)}" {s.anim(1, "pop", 150 * i, f"指標{i + 1}", item=True)}>{inner}</div>')
    return tiles, tallest


def _nice_max(v: float) -> float:
    """軸の上限: v 以上の「1・2・2.5・5 × 10 の累乗」。"""
    if v <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if m * e >= v:
            return m * e
    return 10 * e


def t_line_chart(s: Slide):
    """折れ線: 3〜12 の時点に 1〜3 本の線。highlight の線（無ければ1本目）を accent に。線はまとめて1クリックで左から出る。"""
    head, top = header(s)
    labels = s.spec.get("labels")
    if not isinstance(labels, list) or not 3 <= len(labels) <= 12 or not all(isinstance(x, (str, int)) for x in labels):
        s.err("labels は 3〜12 個の文字の配列（横軸の時点）")
    labels = [str(x) for x in labels]
    series = s.items("series", 1, 3, ("name", "values", "highlight"), required=("values",))
    for i, sr in enumerate(series, 1):
        vals = sr["values"]
        if not isinstance(vals, list) or len(vals) != len(labels):
            s.err(f"series の {i} 本目の values は labels と同じ {len(labels)} 個")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            s.err(f"series の {i} 本目の values は数で書く")
        if len(series) > 1 and not sr.get("name"):
            s.err(f"線が2本以上のときは series の {i} 本目に name（凡例）を書く")
    unit, note = s.text("unit", default=""), s.text("note")
    allv = [v for sr in series for v in sr["values"]]
    vmin = s.spec.get("min", 0 if min(allv) >= 0 else None)
    if vmin is None or isinstance(vmin, bool) or not isinstance(vmin, (int, float)) or vmin > min(allv):
        s.err(f"min は値の最小（{min(allv)}）以下の数（負の値があるときは必ず書く）")
    vmax = s.spec.get("max", vmin + 4 * _nice_max((max(allv) - vmin) / 4))   # 目盛り4つが切りのよい刻みになる上限
    if isinstance(vmax, bool) or not isinstance(vmax, (int, float)) or vmax < max(allv) or vmax <= vmin:
        s.err(f"max は値の最大（{max(allv)}）以上の数")
    dec = max((len(repr(float(v)).split(".")[1].rstrip("0")) for v in allv if not float(v).is_integer()), default=0)  # 値の小数の桁をそろえる
    show = s.spec.get("value_labels", "all" if len(series) == 1 and len(labels) <= 8 else "last")
    if show not in ("all", "last", "none"):
        s.err("value_labels は all / last / none のどれか")
    callout = s.spec.get("callout")
    if callout is not None and (not isinstance(callout, dict) or not callout.get("value")):
        s.err("callout は value（と text）のオブジェクト")
    right = col_x(9) - GUTTER if callout else X0 + CW     # 要点の枠は格子の右 4 列
    pl = X0 + 112
    multi = len(series) > 1
    # 線の端に系列名（と、value_labels が none でなければ最後の値）を右に出す（名前は2行まで・幅 240 まで）。
    # 名前が長くて入らないときだけ、凡例に線種と点の形も写す
    es, end_w, end_n = 28, 0.0, [1] * len(series)
    end_labels = multi
    last_txt = [f"{num_label(sr['values'][-1], dec)}{unit}" for sr in series]
    if multi:
        names_w = max(text_width(sr["name"], 28, True) for sr in series)
        vals_w = max(text_width(x, 28, True) for x in last_txt) if show != "none" else 0
        ew = min(max(names_w, vals_w) + 8, 240)
        try:
            es = min(fit(sr["name"], 28, ew, what="series の name", lines=2, lh=1.4, min_size=22, bold=True)[0] for sr in series)
            while show != "none" and max(text_width(x, es, True) for x in last_txt) > (ew - 8) * SAFE and es > 22:
                es = step_down(es)                 # 最後の値（単位つき）も端の幅に入る段まで下りる
            if show != "none" and max(text_width(x, es, True) for x in last_txt) > (ew - 8) * SAFE:
                raise DeckError("最後の値が端の幅に入らない")
            end_n = [count_lines(sr["name"], es, ew, True) for sr in series]
        except DeckError:
            end_labels = False
        end_w = ew if end_labels else 0
    if end_labels:                                 # 端の名前を全部縦に並べた高さが、見出しの下から軸までに入らなければ凡例にする
        need_h = sum((end_n[k] + (show != "none")) * es * 1.4 for k in range(len(series))) + 8 * (len(series) - 1)
        if need_h > s.bottom - (112 if note else 72) - top:
            end_labels, end_w = False, 0
    n_lab = len(labels)
    rm = max(0.0, (end_w + 32 - (right - pl) / (2 * n_lab)) / (1 - 1 / (2 * n_lab))) if end_labels else 0.0
    if end_labels and rm > (right - pl) * 0.3:
        end_labels, rm = False, 0.0
    legend_h = 48 if multi and not end_labels else 0
    pt = top + legend_h + 56
    base = s.bottom - (112 if note else 72)
    ph = base - pt
    if ph < 220:
        s.err("見出しが高すぎてグラフの場所が足りない（見出しを短くするか sub を外す）")
    pw = right - pl - rm
    slot = pw / len(labels)
    xs = [pl + slot * (i + 0.5) for i in range(len(labels))]

    def y_of(v):
        return base - (v - vmin) / (vmax - vmin) * ph

    parts = [head]
    ticks = 4
    for k in range(ticks + 1):
        v = vmin + (vmax - vmin) * k / ticks
        y = y_of(v)
        if k:
            parts.append(f'<div class="grid-line" style="{box(pl, y - 1, pw, 2)}" data-name="目盛り線{k}"></div>')
        parts.append(f'<div class="tick" style="{box(X0, y - 18, 96)}" data-name="目盛り{k}">{esc(num_label(round(v, 2)))}</div>')
    parts.append(f'<div class="baseline" style="{box(pl, base, pw)}" {s.anim(0, "fade", 200, "グラフの軸")}></div>')
    if unit:
        parts.append(f'<div class="tick" style="{box(X0, pt - 56, 304)} text-align:left" data-name="単位">（{esc(unit)}）</div>')
    for i, lb in enumerate(labels):
        ls, _ = fit(lb, 28, slot - 8, what=f"labels の {i + 1} 個目", lines=1, lh=1.5, min_size=22)
        parts.append(f'<div class="bar-label" style="{box(xs[i] - slot / 2, base + 16, slot)} font-size:{ls}px" '
                     f'{s.anim(0, "fade", 200, f"時点{i + 1}")}>{esc(lb)}</div>')
    marked = any(sr.get("highlight") for sr in series)
    pad = 16
    order = sorted(range(len(series)), key=lambda k: bool(series[k].get("highlight")) if marked else k == 0)
    # 系列は色だけで区別しない: 点の形（丸・四角・ひし形）と線種（実線・破線・点線）を台本の順に変える。強めない線は ink・muted（どちらも rule 以上）
    shapes = ("circle", "square", "diamond")
    looks = {}
    plain_i = 0
    for rank, k in enumerate(order):
        hl = bool(series[k].get("highlight")) if marked else k == 0
        sw = 7 if hl else 5
        dash = (None, f"{sw * 3} {sw * 2}", f"{sw} {sw * 1.2:g}")[k]
        color = s.pal["accent-mark"] if hl else (s.pal["ink"], s.pal["muted"])[plain_i % 2]
        plain_i += 0 if hl else 1
        looks[k] = {"hl": hl, "sw": sw, "dash": dash, "color": color, "shape": shapes[k], "rank": rank}

    def marker(shape, x, y, color, fill, sw=5):
        if shape == "circle":
            return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="{fill}" stroke="{color}" stroke-width="{sw}"/>'
        if shape == "square":
            return f'<rect x="{x - 9:.1f}" y="{y - 9:.1f}" width="18" height="18" fill="{fill}" stroke="{color}" stroke-width="{sw}" stroke-linejoin="miter"/>'
        pts = f"{x:.1f},{y - 13:.1f} {x + 13:.1f},{y:.1f} {x:.1f},{y + 13:.1f} {x - 13:.1f},{y:.1f}"
        return f'<polygon points="{pts}" fill="{fill}" stroke="{color}" stroke-width="{sw}" stroke-linejoin="miter"/>'

    if multi and not end_labels:
        # 凡例（端に名前が入らないときだけ）。見本は線種と点の形も写す。1行に並べ、全部が幅に入る段までそろえて下りる
        gs = 28
        while sum(text_width(sr["name"], gs) + 128 for sr in series) > pw * SAFE:
            gs = step_down(gs)
            if gs < 22:
                s.err("series の name（凡例）が長すぎて1行に並ばない（短くする）")
        items = ""
        for k, sr in enumerate(series):
            lk = looks[k]
            dash = f' stroke-dasharray="{lk["dash"]}"' if lk["dash"] else ""
            items += (f'<div class="legend-item"><svg width="64" height="24" viewBox="0 0 64 24" style="flex:none">'
                      f'<line x1="0" y1="12" x2="64" y2="12" stroke="{lk["color"]}" stroke-width="{lk["sw"]}"{dash}/>'
                      + marker(lk["shape"], 32, 12, lk["color"], s.pal["bg"]) + f'</svg>'
                      f'<div class="legend-name" style="font-size:{gs}px">{esc(sr["name"])}</div></div>')
        parts.append(f'<div class="legend" style="{box(pl, top, pw)}" data-name="凡例">{items}</div>')
    ends = []                                      # 線の端の系列名 (系列, y)。あとで縦の重なりをほどく
    for k in order:
        sr, lk = series[k], looks[k]
        color, hl, rank = lk["color"], lk["hl"], lk["rank"]
        pts = [(xs[i] - pl + pad, y_of(v) - pt + pad) for i, v in enumerate(sr["values"])]
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        dots = "".join(marker(lk["shape"], x, y, color, color if hl else s.pal["bg"]) for x, y in pts)
        dash = f' stroke-dasharray="{lk["dash"]}"' if lk["dash"] else ""
        cap = "butt" if lk["dash"] else "round"
        parts.append(f'<svg class="line-svg" style="{box(pl - pad, pt - pad, pw + 2 * pad, ph + 2 * pad)}" '
                     f'viewBox="0 0 {pw + 2 * pad:.0f} {ph + 2 * pad:.0f}" {s.anim(1, "wipe-right", 400 * rank, f"線{k + 1}")}>'
                     f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="{lk["sw"]}"{dash} '
                     f'stroke-linejoin="round" stroke-linecap="{cap}"/>{dots}</svg>')
        idx = range(len(labels)) if show == "all" else [len(labels) - 1] if show == "last" else []
        for i in idx:
            if multi and end_labels and i == len(labels) - 1:
                continue                           # 最後の値は線の端の名前に添える
            v = sr["values"][i]
            shown = f"{num_label(v, dec)}{unit if show == 'last' else ''}"
            vs, _ = fit(shown, 28, slot + 40, what=f"series の {k + 1} 本目の値", lines=1, lh=1.4, min_size=22, bold=True)
            col = "; color:var(--accent-text)" if hl else ""
            parts.append(f'<div class="pt-value" style="{box(xs[i] - slot / 2 - 20, y_of(v) - 56, slot + 40)} font-size:{vs}px{col}" '
                         f'{s.anim(1, "fade", 400 * rank + 500 + 40 * i, f"値{k + 1}-{i + 1}")}>{esc(shown)}</div>')
        if multi and end_labels:
            ends.append([k, y_of(sr["values"][-1]) - (end_n[k] + (show != "none")) * es * 1.4 / 2])
    if ends:                                       # 端の名前どうしが縦に重ならないようにずらす（上から詰めて、下にはみ出したら上へ戻す）
        ends.sort(key=lambda e: e[1])
        hh_ = lambda k: (end_n[k] + (show != "none")) * es * 1.4
        ends[0][1] = max(ends[0][1], top)                   # 上端は見出しの下より上に出さない（入る高さは上で確かめた）
        for a_, b_ in zip(ends, ends[1:]):
            b_[1] = max(b_[1], a_[1] + hh_(a_[0]) + 8)
        over = ends[-1][1] + hh_(ends[-1][0]) - base
        if over > 0:
            ends[-1][1] -= over
            for a_, b_ in zip(reversed(ends[:-1]), reversed(ends[1:])):
                a_[1] = min(a_[1], b_[1] - hh_(a_[0]) - 8)
        for k, y in ends:
            lk = looks[k]
            col = "var(--accent-text)" if lk["hl"] else "var(--ink)"
            last = f'<div>{esc(num_label(series[k]["values"][-1], dec))}{esc(unit)}</div>' if show != "none" else ""
            parts.append(f'<div class="end-name" style="{box(xs[-1] + 24, y, end_w)} font-size:{es}px; color:{col}" '
                         f'{s.anim(1, "fade", 400 * lk["rank"] + 500, f"線の名前{k + 1}")}><div>{esc(series[k]["name"])}</div>{last}</div>')
    if note:
        ns, _ = fit(note, 22, right - X0, what="note", lines=1, lh=1.5, min_size=22)
        parts.append(f'<div class="bar-unit" style="{box(X0, base + 64, right - X0)} font-size:{ns}px" data-name="注記">{esc(note)}</div>')
    if callout:
        parts.append(callout_box(s, callout, top, base - top, s.anim(1, "fade", 400 * len(series) + 600, "グラフの要点")))
    parts.append(source_line(s))
    return parts


def t_team(s: Slide):
    """体制・人: 2〜6人（写真か頭文字の丸・名前・役割・ひとこと）。4人までは1列、5〜6人は3人ずつ2段。まとめて1クリックで出る。"""
    head, top = header(s)
    ms = s.items("members", 2, 6, ("name", "role", "note", "image", "initials"), required=("name",))
    n = len(ms)
    cols = n if n <= 4 else 3
    two = n > 4
    gap = GUTTER
    cw = (CW - gap * (cols - 1)) / cols
    av = 120 if two else 160
    rows, cur = [], []
    for i, m in enumerate(ms):
        where = f"members の {i + 1} 人目"
        name, role, note = s.text("name", spec=m), s.text("role", spec=m), s.text("note", spec=m)
        if m.get("image"):
            face = s.img(m["image"], f"member{i + 1}", "face", 0, 0, av, av, extra=f"border-radius:{FULL}px; position:static;")
        else:
            ini = s.text("initials", spec=m) or name[0]
            if len(ini) > 2:
                s.err(f"{where}の initials は2文字まで")
            face = (f'<div class="face-ini" style="width:{av}px; height:{av}px; border-radius:{FULL}px; '
                    f'font-size:{snap(av * 0.4)}px">{esc(ini)}</div>')                # 160 の丸に 64・120 の丸に 48
        ns, _ = fit(name, 32 if two else 36, cw, what=f"{where}の name", lines=1, lh=1.4, min_size=28, bold=True)
        inner = face + f'<div class="member-name" style="font-size:{ns}px">{esc(name)}</div>'
        if role:
            rs, _ = fit(role, 28, cw, what=f"{where}の role", lines=1, lh=1.5, min_size=24, bold=True)
            inner += f'<div class="member-role" style="font-size:{rs}px">{esc(role)}</div>'
        if note:
            bs, _ = fit(note, 28, cw, what=f"{where}の note", lines=2 if two else 3, lh=1.5, min_size=24)
            inner += f'<div class="member-note" style="font-size:{bs}px">{rich(note)}</div>'
        cur.append(f'<div class="member" style="width:{px(cw)}" {s.anim(1, "rise", 120 * i, f"人{i + 1}", item=True)}>{inner}</div>')
        if len(cur) == cols:
            rows.append(cur)
            cur = []
    if cur:
        rows.append(cur)
    body = "".join(f'<div class="row" style="gap:{gap}px; justify-content:center; align-items:flex-start;'
                   f'{" margin-top:40px;" if r else ""}">' + "".join(row) + "</div>" for r, row in enumerate(rows))
    return [head, f'<div class="center" style="{box(X0, top, CW, BODY_BOTTOM - top)}">{body}</div>', source_line(s)]


def t_matrix(s: Slide):
    """2×2 の位置づけ: 縦横の軸（label・low・high）と四隅の名前、点（x・y は 0〜100）。点はまとめて1クリックで出る。

    quadrants は 左上・右上・左下・右下 の順。focus（1〜4・同じ順）の象限を淡く塗る。callout があれば右に要点。
    """
    head, top = header(s)
    axes = {}
    for k in ("x", "y"):
        a = s.spec.get(k)
        if not isinstance(a, dict) or not a.get("label"):
            s.err(f"{k} は label（と low・high）のオブジェクト")
        extra = sorted(set(a) - {"label", "low", "high"})
        if extra:
            s.err(f"{k} に知らないキー: {', '.join(extra)}")
        axes[k] = {kk: s.text(kk, spec=a, where=f"{k}.") or "" for kk in ("label", "low", "high")}
    quads = s.items("quadrants", 0, 4, ("title", "body"))
    if quads and len(quads) != 4:
        s.err("quadrants は 左上・右上・左下・右下 の4つ（書くなら4つとも）")
    focus = s.spec.get("focus")
    if focus is not None and (not isinstance(focus, int) or not 1 <= focus <= 4):
        s.err("focus は 1〜4（左上・右上・左下・右下）")
    its = s.items("items", 1, 8, ("label", "x", "y", "accent"), required=("label", "x", "y"))
    for i, it in enumerate(its, 1):
        for k in ("x", "y"):
            v = it[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
                s.err(f"items の {i} 個目の {k} は 0〜100 の数")
    callout = s.spec.get("callout")
    if callout is not None and (not isinstance(callout, dict) or not callout.get("text")):
        s.err("callout は text（と title）のオブジェクト")
    lw = 200                                     # 縦軸の言葉の列
    px0 = X0 + lw + 24
    right = col_x(9) - GUTTER if callout else X0 + CW     # 要点の枠は格子の右 4 列
    pt, pb = top + 8, s.bottom - 64
    pw, ph = right - px0, pb - pt
    if ph < 300:
        s.err("見出しが高すぎて図の場所が足りない（見出しを短くするか sub を外す）")
    hw, hh = pw / 2, ph / 2
    parts = [head]
    if focus:
        fx_, fy_ = px0 + hw * ((focus - 1) % 2), pt + hh * ((focus - 1) // 2)
        parts.append(f'<div class="mx-focus" style="{box(fx_, fy_, hw, hh)}" data-name="注目の象限"></div>')
    parts.append(f'<div class="mx-frame" style="{box(px0, pt, pw, ph)}" {s.anim(0, "fade", 150, "図の枠")}></div>')
    parts.append(f'<div class="mx-axis" style="{box(px0 + hw - 1, pt, 2, ph)}" data-name="縦の区切り"></div>')
    parts.append(f'<div class="mx-axis" style="{box(px0, pt + hh - 1, pw, 2)}" data-name="横の区切り"></div>')
    # 四隅の名前は外側の角に置く（上の象限は上端、下の象限は下端、左右は外側に寄せる）。点はその帯を避けた内側に置く
    # 読む順（DOM の順）は 軸 → 項目 → 象限名。象限名は先に組み、あとで項目のあとに並べる
    band, quad_parts = 0, []
    for i, q in enumerate(quads):
        qw = hw - 48
        ts, _ = fit(q["title"], 28, qw, what=f"quadrants の {i + 1} 個目", lines=1, lh=1.5, min_size=24, bold=True)
        inner = f'<div class="mx-qtitle{" accent" if focus == i + 1 else ""}" style="font-size:{ts}px">{esc(q["title"])}</div>'
        qh = ts * 1.5
        if q.get("body"):
            bs, _ = fit(q["body"], 24, qw, what=f"quadrants の {i + 1} 個目の body", lines=1, lh=1.5, min_size=22)
            inner += f'<div class="mx-qbody" style="font-size:{bs}px">{esc(q["body"])}</div>'
            qh += 8 + bs * 1.5
        band = max(band, qh + 16)
        qx = px0 + hw * (i % 2) + 24
        qy = pt + 16 if i < 2 else pb - 16 - qh
        al = "left" if i % 2 == 0 else "right"
        quad_parts.append(f'<div class="mx-quad" style="{box(qx, qy, qw, qh)} text-align:{al}" '
                          f'{s.anim(0, "fade", 300, f"象限{i + 1}")}>{inner}</div>')
    ix0, ix1 = px0 + 40, right - 40               # 点を置く内側（四隅の名前の帯を避ける）
    iy0, iy1 = pt + band + 24, pb - band - 24
    ax, ay = axes["x"], axes["y"]
    axis_parts, item_parts = [], []
    # 縦軸: 左の列に 上=high・中=label・下=low
    yl = [(ay["high"], pt, "flex-start"), (f"↑ {ay['label']}", pt + hh - 40, "center"), (ay["low"], pb - 80, "flex-end")]
    for k, (txt, y, just) in enumerate(yl):
        if txt:
            ys, _ = fit(txt, 26, lw, what="y の文字", lines=2, lh=1.5, min_size=22, bold=k == 1)
            cls = ' class="bold"' if k == 1 else ""
            axis_parts.append(f'<div class="mx-ylab" style="{box(X0, y, lw, 80)} justify-content:{just}">'
                         f'<div{cls} style="font-size:{ys}px">{esc(txt)}</div></div>')
    # 横軸: 下に 左=low・中=label・右=high
    third = pw / 3
    for k, (txt, al) in enumerate([(ax["low"], "left"), (f"{ax['label']} →", "center"), (ax["high"], "right")]):
        if txt:
            xs_, _ = fit(txt, 26, third - 16, what="x の文字", lines=1, lh=1.5, min_size=22, bold=k == 1)
            axis_parts.append(f'<div class="mx-xlab{" bold" if k == 1 else ""}" style="{box(px0 + third * k, pb + 16, third)} '
                         f'text-align:{al}; font-size:{xs_}px">{esc(txt)}</div>')
    hit = lambda a, b: a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
    walls = [(px0 + hw - 1, pt, px0 + hw + 1, pb), (px0, pt + hh - 1, right, pt + hh + 1)]   # 縦横の区切り線。名前は線をまたがない
    # 点の位置は台本で決まる。点どうしが重なれば rc=2。名前は、全部の点・置いた名前・区切り線を避けて、点の横で上下に動かす
    sizes, dots = [], []
    for i, it in enumerate(its):
        cx, cy = ix0 + (ix1 - ix0) * it["x"] / 100, iy1 - (iy1 - iy0) * it["y"] / 100
        ls, _ = fit(it["label"], 28, 320, what=f"items の {i + 1} 個目の label", lines=1, lh=1.5, min_size=24, bold=True)
        r = 20 if it.get("accent") else 16          # 強める点は二重の輪（外 40・内 16）で、色だけでなく形でも示す
        box_ = (cx - r, cy - r, cx + r, cy + r)
        for j, o in enumerate(dots):
            if hit(box_, o):
                s.err(f"items の {i + 1} 個目「{it['label']}」の点が、{j + 1} 個目の点と重なる（x・y を離す）")
        sizes.append((cx, cy, ls, r))
        dots.append(box_)
    labels = []                                    # 置いた名前の外接の四角
    for i, it in enumerate(its):
        cx, cy, ls, r = sizes[i]
        hl = bool(it.get("accent"))
        tw = text_width(it["label"], ls, True) + 8
        h = ls * 1.5
        dot_box = dots[i]
        others = dots[:i] + dots[i + 1:]
        default_left = it["x"] > 70               # 右端に近い点は、名前を点の左に出す
        choice = None
        dys = [0] + [d for k in range(1, int(h // 8) + 1) for d in (-8 * k, 8 * k)]        # 名前を点の横で、上下に 8px ずつずらして探す（点の高さは動かさない）
        for dy, left in ((dy, lf) for dy in dys for lf in (default_left, not default_left)):
            lx = cx - r - 12 - tw if left else cx + r + 12     # 点と名前の間は 12
            ly = cy - h / 2 + dy
            lab = (lx, ly, lx + tw, ly + h)
            if lx < px0 + 8 or lab[2] > right - 8 or ly < pt + 8 or lab[3] > pb - 8:
                continue
            if any(hit(lab, o) for o in labels + others + walls) or hit(lab, dot_box):
                continue
            choice = (left, lx, ly, lab)
            break
        if choice is None:
            s.err(f"items の {i + 1} 個目「{it['label']}」の名前が、ほかの項目と重なって置けない（x・y を離すか、名前を短くする）")
        left, lx, ly, lab = choice
        labels.append(lab)
        x0, y0 = min(dot_box[0], lx), min(dot_box[1], ly)
        x1, y1 = max(dot_box[2], lab[2]), max(dot_box[3], lab[3])
        dot = (f'<div class="mx-dot hl" style="{box(cx - r - x0, cy - r - y0, 2 * r, 2 * r)}"><div class="mx-dot-in"></div></div>' if hl else
               f'<div class="mx-dot" style="{box(cx - r - x0, cy - r - y0, 2 * r, 2 * r)}"></div>')
        item_parts.append(f'<div class="mx-item" style="{box(x0, y0, x1 - x0, y1 - y0)}" '
                          f'{s.anim(1, "pop", 150 * i, f"点{i + 1}")}>' + dot +
                          f'<div class="mx-label{" accent" if hl else ""}" style="{box(lx - x0, ly - y0, tw)} '
                          f'font-size:{ls}px; text-align:{"right" if left else "left"}">{esc(it["label"])}</div></div>')
    parts += axis_parts + item_parts + quad_parts
    if callout:
        ct, tt = s.text("text", spec=callout), s.text("title", spec=callout)
        inner = ""
        if tt:
            ts, _ = fit(tt, 36, CALLOUT_IW, what="callout.title", lines=2, lh=1.4, min_size=28, bold=True)
            inner += f'<div class="callout-title" style="font-size:{ts}px">{rich(tt)}</div>'
        bs, _ = fit(ct, 28, CALLOUT_IW, what="callout.text", lines=8, lh=1.6, min_size=24)
        inner += f'<div class="callout-text" style="font-size:{bs}px">{rich(ct)}</div>'
        parts.append(f'<div class="callout" style="{box(col_x(9), top, CALLOUT_W, pb - top)}" '
                     f'{s.anim(1, "fade", 150 * len(its) + 300, "図の要点")}>{inner}</div>')
    parts.append(source_line(s))
    return parts


def t_gallery(s: Slide):
    """写真の並び: 2〜4枚を横に並べ、各枚の下に短いキャプション。1枚ずつクリックで出る。全部同じ縦横比の枠。

    layout: collage は3枚を大1枚（左 58%）＋小2枚（右に縦に2つ）の非対称に組む。主役の写真が1枚あるとき
    """
    head, top = header(s)
    layout = s.spec.get("layout", "row")
    if layout not in ("row", "collage"):
        s.err("layout は row（横に並べる・既定）か collage（大1枚＋小2枚）")
    its = s.items("items", 3 if layout == "collage" else 2, 3 if layout == "collage" else 4, ("image", "caption"), required=("image",))
    if layout == "collage":
        return [head] + _collage(s, its, top) + [source_line(s)]
    n, gap = len(its), GUTTER
    cw = (CW - gap * (n - 1)) / n
    avail = BODY_BOTTOM - top
    caps = [s.text("caption", spec=it) for it in its]
    cap_h = (16 + 2 * 26 * 1.5) if any(caps) else 0
    fh = min(avail - cap_h, cw * 1.5)              # 縦長すぎる枠にしない（2:3 まで）
    if fh < 200:
        s.err("見出しが高すぎて写真の場所が足りない（見出しを短くするか sub を外す）")
    y0 = top + (avail - fh - cap_h) / 2
    parts = [head]
    for i, it in enumerate(its):
        x = X0 + i * (cw + gap)
        inner = s.img(it["image"], f"image{i + 1}", "gal-img", 0, 0, cw, fh, extra="position:static;")
        if caps[i]:
            cs, _ = fit(caps[i], 26, cw, what=f"items の {i + 1} 個目の caption", lines=2, lh=1.5, min_size=22)
            inner += f'<div class="gal-cap" style="font-size:{cs}px">{rich(caps[i])}</div>'
        parts.append(f'<div class="gal-item" style="{box(x, y0, cw)}" {s.anim(i + 1, "rise", 0, f"写真{i + 1}", item=True)}>{inner}</div>')
    parts.append(source_line(s))
    return parts


def _collage(s: Slide, its, top):
    """大1枚（左）＋小2枚（右に縦に2つ）。キャプションは各写真の下に1行。"""
    gap = GUTTER
    lw = span(7)                                  # 大きい写真は格子の 7 列・小さい写真は 5 列
    rw = CW - lw - gap
    avail = BODY_BOTTOM - top
    caps = [s.text("caption", spec=it) for it in its]
    cap = 16 + 26 * 1.5 if any(caps) else 0
    fh = avail - cap                              # 大きい写真の高さ
    sh = (avail - gap - 2 * cap) / 2              # 小さい写真の高さ
    if sh < 140:
        s.err("見出しが高すぎて写真の場所が足りない（見出しを短くするか sub を外す）")
    boxes = [(X0, top, lw, fh), (X0 + lw + gap, top, rw, sh), (X0 + lw + gap, top + sh + cap + gap, rw, sh)]
    parts = []
    for i, (it, (x, y, w, h)) in enumerate(zip(its, boxes)):
        inner = s.img(it["image"], f"image{i + 1}", "gal-img", 0, 0, w, h, extra="position:static;")
        if caps[i]:
            cs, _ = fit(caps[i], 26, w, what=f"items の {i + 1} 個目の caption", lines=1, lh=1.5, min_size=22)
            inner += f'<div class="gal-cap" style="font-size:{cs}px">{rich(caps[i])}</div>'
        parts.append(f'<div class="gal-item" style="{box(x, y, w)}" {s.anim(i + 1, "rise", 0, f"写真{i + 1}", item=True)}>{inner}</div>')
    return parts


def t_figure(s: Slide):
    """図: スクリーンショット・図面・地図などを**切らずに**見せる（object-fit: contain）。下にキャプション。"""
    head, top = header(s)
    cap = s.text("caption")
    cap_h = 56 if cap else 0
    fh = BODY_BOTTOM - top - cap_h
    if fh < 240:
        s.err("見出しが高すぎて図の場所が足りない（見出しを短くするか sub を外す）")
    img = s.img(s.spec.get("image"), "image", "figure-img", X0, top, CW, fh, anim=s.anim(0, "fade", 150, "図"), fit_mode="contain")
    slot = s.slots[-1]
    bottom = top + fh                              # 図の下端（画像が無いときは枠の下端）
    if slot.get("pixels"):                         # 縮めて収めた後の高さと、focus の縦の位置から、実際の下端を出す
        iw, ih = map(int, slot["pixels"].split("x"))
        dh = ih * min(CW / iw, fh / ih)
        pos = s.focus_css(slot.get("focus"), "image") or "50% 50%"
        py = float(pos.split()[1].rstrip("%")) / 100
        bottom = top + (fh - dh) * py + dh
    parts = [head, img]
    if cap:
        cs, _ = fit(cap, 26, CW, what="caption", lines=1, lh=1.5, min_size=22)
        parts.append(f'<div class="fig-cap" style="{box(X0, bottom + 16, CW)} font-size:{cs}px" '
                     f'{s.anim(0, "fade", 400, "図の説明")}>{rich(cap)}</div>')
    parts.append(source_line(s))
    return parts


def _annotate_rows(s: Slide, its, nw, drop):
    """注釈の説明の行（題は 36B・本文は 24N から、段を drop だけ下りる）。(HTML の列, 使う高さの見積もり) を返す。"""
    rows, used, gap = [], 0.0, 24
    for i, it in enumerate(its):
        title, body = it["title"], s.text("body", spec=it)
        ts, tn = fit(title, step_down(36, drop, 28), nw - 88, what=f"items の {i + 1} 個目の title", lines=2, lh=1.4,
                     min_size=28, bold=True)
        inner = f'<div class="li-title" style="font-size:{ts}px">{rich(title)}</div>'
        h = tn * ts * 1.4
        if body:
            bs, bn = fit(body, 24, nw - 88, what=f"items の {i + 1} 個目の body", lines=3, lh=1.5, min_size=24)
            inner += f'<div class="li-body" style="font-size:{bs}px">{rich(body)}</div>'
            h += 8 + bn * bs * 1.5
        used += max(MARK_D, h) + (gap if i else 0)
        rows.append(f'<div class="li" style="{f"margin-top:{gap}px;" if i else ""}" {s.anim(i + 1, "rise", 120, f"説明{i + 1}")}>'
                    f'<div class="li-no" style="width:56px; height:56px; font-size:26px; margin-right:24px">{i + 1}</div>'
                    f'<div class="li-text">{inner}</div></div>')
    return rows, used


def t_annotate(s: Slide):
    """注釈つきの画像: 写真や図の上に番号の丸印、右に番号つきの説明。印と説明を1つずつクリックで出す。

    印の位置は items の x・y（0〜100 の %）。fit: cover（既定・枠いっぱいに切り抜く。% は枠に対して）／
    contain（図・画面の写しを切らずに縮める。% は実際に描いた画像に対して）
    """
    head, top = header(s)
    its = s.items("items", 1, 6, ("x", "y", "title", "body"), required=("x", "y", "title"))
    for i, it in enumerate(its, 1):
        for k in ("x", "y"):
            v = it[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
                s.err(f"items の {i} 個目の {k} は 0〜100 の数（画像の左上からの %）")
    fit_mode = s.spec.get("fit", "cover")
    if fit_mode not in ("cover", "contain"):
        s.err("fit は cover（枠いっぱいに切り抜く・既定）か contain（切らずに縮める）")
    iw, ih = ANNOTATE_IMG_W, BODY_BOTTOM - top
    img = s.img(s.spec.get("image"), "image", "side-img" if fit_mode == "cover" else "figure-img", X0, top, iw, ih,
                anim=s.anim(0, "fade", 0, "画像"), fit_mode=fit_mode)
    rx, ry, rw, rh = X0, top, iw, ih             # 印の % を測る四角（contain なら実際に描いた画像）
    slot = s.slots[-1]
    if fit_mode == "contain" and slot.get("pixels"):
        pw_, ph_ = map(int, slot["pixels"].split("x"))
        k = min(iw / pw_, ih / ph_)
        rw, rh = pw_ * k, ph_ * k
        pos = s.focus_css(slot.get("focus"), "image") or "50% 50%"
        fx_, fy_ = (float(v.rstrip("%")) / 100 for v in pos.split())
        rx, ry = X0 + (iw - rw) * fx_, top + (ih - rh) * fy_
    parts = [head, img]
    for i, it in enumerate(its):
        cx, cy = rx + rw * it["x"] / 100, ry + rh * it["y"] / 100
        parts.append(f'<div class="mk-dot" style="{box(cx - MARK_D / 2, cy - MARK_D / 2, MARK_D, MARK_D)}" '
                     f'{s.anim(i + 1, "pop", 0, f"印{i + 1}")}>{i + 1}</div>')
    nx, nw = X0 + iw + GUTTER, CW - iw - GUTTER
    for drop in range(0, 5):                       # 説明の列（高さ ih）に件数ぶんが入るまで、全部の題を同じだけ段を下りる
        rows, used = _annotate_rows(s, its, nw, drop)
        if used <= ih:
            break
    else:
        s.err(f"説明が多すぎて入らない（{used:.0f}px > {ih:.0f}px。件数を減らすか body を短くする）")
    parts.append(f'<div class="list" style="{box(nx, top, nw, ih)}">' + "".join(rows) + "</div>")
    parts.append(source_line(s))
    return parts



# --- 図解の型（ADR 0019）: 式・放射・対。見出しは大きく、箱の中は名詞＋短い補足 -------------------------------

def _term(s: Slide, it: dict, role: str, width: float, where: str, *, result=False) -> tuple[str, float]:
    """式の箱の中身（アイコン・題・補足）と高さの見積もり。題は Std-45B-140（答えは 57B）・補足は Std-26N-150。"""
    inner, h = [], 0.0
    icon = icon_of(s, it, where)
    if icon:
        inner.append(icon_disc(icon, 128, 64, ground="var(--on-primary)" if result else "var(--tint)",
                               extra=" margin-bottom:24px;"))
        h += 152
    ts, tn = s.fit(f"{role}-title", it["title"], s.g(57 if result else 45, 57), width, what=f"{where}の title", lines=2, lh=1.4,
                   min_size=32, bold=True, tidy=True)
    inner.append(f'<div class="eq-title" style="font-size:{ts}px">{rich(it["title"])}</div>')
    h += tn * ts * 1.4
    body = s.text("body", spec=it)
    if body:
        bs, bn = s.fit(f"{role}-body", body, s.g(26, 28), width - (48 if result else 0), what=f"{where}の body", lines=2, lh=1.5,
                       min_size=24, bold=result)       # 答えの札は太字で、左右に 24px ずつの余白
        cls = "eq-chip" if result else "eq-body"
        inner.append(f'<div class="{cls}" style="font-size:{bs}px">{rich(body)}</div>')
        h += 16 + bn * bs * 1.5 + (16 if result else 0)
    return "".join(inner), h


def t_equation(s: Slide):
    """式: 2〜4つの要素を「＋」でつなぎ、「＝」の先に答え（result）を置く。要素は1つずつ、答えは最後にクリックで出る。"""
    head, top = header(s, big=True)
    its = s.items("items", 2, 4, ("title", "body", "icon"))
    res = s.spec.get("result")
    if not isinstance(res, dict):
        s.err("result（答え）は {title, body, icon} のオブジェクトで書く")
    res = s.items("result", 1, 1, ("title", "body", "icon"), spec={"result": [res]})[0]
    n, op = len(its), 88
    bw = (CW - op * n) / (n + 1)
    iw = bw - 64
    avail = s.bottom - top
    cells, tallest = [], 0.0
    for i, it in enumerate(its):
        inner, h = _term(s, it, "eq", iw, f"items の {i + 1} 個目")
        tallest = max(tallest, h)
        if i:
            cells.append(f'<div class="eq-op" style="width:{op}px" {s.anim(i + 1, "pop", 0, f"＋{i}")}>＋</div>')
        cells.append(f'<div class="card eq-box" style="width:{px(bw)}" {s.anim(i + 1, "rise", 100, f"要素{i + 1}", item=True)}>{inner}</div>')
    inner, h = _term(s, res, "eq-res", iw, "result ", result=True)
    tallest = max(tallest, h)
    cells.append(f'<div class="eq-op" style="width:{op}px" {s.anim(n + 1, "pop", 0, "＝")}>＝</div>')
    cells.append(f'<div class="eq-box eq-res" style="width:{px(bw)}" {s.anim(n + 1, "pop", 150, "答え")}>{inner}</div>')
    box_h = max(360.0, tallest + 2 * 40 + 8)
    if box_h > avail:
        s.err(f"中身が多すぎて入らない（箱の高さ {box_h:.0f}px・入るのは {avail:.0f}px。題・補足を短くする）")
    s.need, s.avail = box_h, avail
    return [head, f'<div class="center" style="{box(X0, top, CW, avail)}"><div class="row" style="height:{px(box_h)}">'
            + "".join(cells) + "</div></div>", source_line(s)]


def t_hub(s: Slide):
    """放射: 中心（center）と、その周りの 3〜6 個（左右に分けて並べ、中心と線でつなぐ）。中心が先に出て、周りは1クリックで出る。"""
    head, top = header(s, big=True)
    cen = s.spec.get("center")
    if not isinstance(cen, dict):
        s.err("center（中心）は {title, body, icon} のオブジェクトで書く")
    cen = s.items("center", 1, 1, ("title", "body", "icon"), spec={"center": [cen]})[0]
    its = s.items("items", 3, 6, ("title", "body", "icon", "tag"))
    avail = s.bottom - top
    n = len(its)
    rows = (n + 1) // 2
    gap = 24
    ch = min(200.0, (avail - gap * (rows - 1)) / rows)
    if ch < 144:
        s.err(f"項目が多すぎて入らない（1つの高さ {ch:.0f}px。見出しを短くするか項目を減らす）")
    cw, d = 576, 320
    cy = top + avail / 2
    cx = X0 + CW / 2
    parts = [head]
    inner = ""
    icon = icon_of(s, cen, "center ")
    if icon:
        inner += ICON.svg(icon, 96, "var(--primary-text)")
    cs_, _ = s.fit("hub-center", cen["title"], 32, d - 72, what="center の title", lines=2, lh=1.4, min_size=24, bold=True,
                   tidy=True)                    # 1文字だけの行を作らない（「しく／み」）
    inner += f'<div class="hub-c-title" style="font-size:{cs_}px">{rich(cen["title"])}</div>'
    body = s.text("body", spec=cen)
    if body:
        bs, _ = fit(body, 22, d - 72, what="center の body", lines=2, lh=1.5, min_size=22)
        inner += f'<div class="hub-c-body" style="font-size:{bs}px">{rich(body)}</div>'
    center = (f'<div class="card hub-center" style="{box(cx - d / 2, cy - d / 2, d, d)} border-radius:{FULL}px;" '
                 f'{s.anim(0, "pop", 200, "中心")}>{inner}</div>')
    parts.append(center)                      # 読む順は 中心 → 周り（DOM の順）
    lines = []
    for i, it in enumerate(its):
        side = 0 if i < rows else 1
        j = i if side == 0 else i - rows
        rows_here = rows if side == 0 else n - rows
        y0 = top + (avail - (rows_here * ch + (rows_here - 1) * gap)) / 2 + j * (ch + gap)
        x0 = X0 if side == 0 else X0 + CW - cw
        icon = icon_of(s, it, f"items の {i + 1} 個目")
        tw = cw - 64 - 2 * BORDER - (96 + 24 if icon else 0)
        ts, _ = s.fit("hub-title", it["title"], s.g(36, 36), tw, what=f"items の {i + 1} 個目の title", lines=1, lh=1.4,
                      min_size=28, bold=True)
        txt = f'<div class="hub-title" style="font-size:{ts}px">{rich(it["title"])}</div>'
        body = s.text("body", spec=it)
        if body:
            bs, _ = s.fit("hub-body", body, s.g(24, 28), tw, what=f"items の {i + 1} 個目の body", lines=1, lh=1.5, min_size=22)
            txt += f'<div class="hub-body" style="font-size:{bs}px">{rich(body)}</div>'
        tag = s.text("tag", spec=it)
        if tag:
            gs, _ = s.fit("hub-tag", tag, 22, tw - 32, what=f"items の {i + 1} 個目の tag", lines=1, lh=1.5, min_size=22, bold=True)
            txt += f'<div class="hub-tag" style="font-size:{gs}px">{esc(tag)}</div>'
        disc = icon_disc(icon, 96, 56, extra=" flex:none; margin-right:24px;") if icon else ""
        parts.append(f'<div class="card hub-item" style="{box(x0, y0, cw, ch)}" {s.anim(1, "rise", 200 + 120 * i, f"周り{i + 1}")}>'
                     f'{disc}<div class="hub-text">{txt}</div></div>')
        # 線: 箱の内側の縁の中ほどから、中心の円の縁へ（中心へ向かう向き）
        ex0 = x0 + cw if side == 0 else x0
        ey0 = y0 + ch / 2
        dx, dy = ex0 - cx, ey0 - cy
        r = math.hypot(dx, dy) or 1
        ex, ey = cx + dx / r * (d / 2 + 6), cy + dy / r * (d / 2 + 6)
        k = 90 if side == 0 else -90
        lines.append(f'<path d="M {ex0:.1f} {ey0:.1f} C {ex0 + k:.1f} {ey0:.1f}, {ex - k * 0.6:.1f} {ey:.1f}, {ex:.1f} {ey:.1f}" '
                     f'style="fill:none; stroke:var(--rule); stroke-width:2"/>')           # 接続線は rule・2px
    parts.insert(2, f'<svg class="hub-lines" width="{W}" height="{H}" viewBox="0 0 {W} {H}" {s.anim(1, "fade", 100, "つなぐ線")}>'
                 + "".join(lines) + "</svg>")
    s.need, s.avail = avail, avail
    parts.append(source_line(s))
    return parts


def t_pairs(s: Slide):
    """対: よくない例 → よい例 を 2〜4 組（label・before・after・icon）。1組ずつクリックで出る。4組は 2×2、2組は横に2つ、3組は縦に3つ。"""
    head, top = header(s, big=True)
    its = s.items("items", 2, 4, ("label", "before", "after", "icon"), required=("label", "before", "after"))
    bad, good = s.text("bad_label", default="NG"), s.text("good_label", default="OK")
    for w_, v in (("bad_label", bad), ("good_label", good)):
        if text_width(v, 24, True) > 128:
            s.err(f"{w_} は短く（「NG」「OK」「before」「after」くらい）")
    n = len(its)
    cols = 1 if n == 3 else 2
    rows = (n + cols - 1) // cols
    gap = GUTTER if cols > 1 else 24
    rgap = 24
    avail = s.bottom - top
    pw = (CW - gap * (cols - 1)) / cols
    ph = min(360.0, (avail - rgap * (rows - 1)) / rows)
    if ph < 144:
        s.err(f"組が多すぎて入らない（1組の高さ {ph:.0f}px。見出しを短くするか組を減らす）")
    compact = ph < 264                          # 低い組（3組を縦に並べたとき）: 札を上に寄せ、アイコンを小さく
    lw, aw = 184, 56
    pwi, phi = pw - 2 * BORDER, ph - 2 * BORDER     # 箱の中の絶対配置は枠の内側から測る
    bw = (pwi - 40 - lw - 24 - 40 - 2 * 24 - aw) / 2
    by = 56 if compact else 64                    # 札（40）を箱の上に重ならずに置く（読む順は 札 → 文）
    bh = phi - by - (24 if compact else 40)
    gy = top + (avail - (rows * ph + (rows - 1) * rgap)) / 2
    parts = [head]
    for i, it in enumerate(its):
        x0 = X0 + (i % cols) * (pw + gap)
        y0 = gy + (i // cols) * (ph + rgap)
        icon = icon_of(s, it, f"items の {i + 1} 個目")
        ls, _ = s.fit("pair-label", it["label"], 36, lw, what=f"items の {i + 1} 個目の label", lines=1, lh=1.4,
                      min_size=28, bold=True)
        left = ((ICON.svg(icon, 48 if compact else 64, "var(--primary-text)") if icon else "")
                + f'<div class="pair-label" style="font-size:{ls}px">{esc(it["label"])}</div>')
        bx1 = 40 + lw + 24
        bx2 = bx1 + bw + 24 + aw + 24
        cells = []
        for k_, (key, cls, tag, mark, color) in enumerate((("before", "pair-bad", bad, "cross", "var(--error-2)"),
                                                            ("after", "pair-good", good, "check", "var(--success-2)"))):
            fs, _ = s.fit(f"pair-{key}", it[key], s.g(28, 32), bw - 48, what=f"items の {i + 1} 個目の {key}", height=bh - (24 if compact else 48),
                          lh=1.5, min_size=24, bold=key == "after")
            bx = bx1 if k_ == 0 else bx2
            cells.append(f'<div class="pair-tag {cls}-tag" style="{box(bx, by - CHIP_H - 8)}">{ICON.svg(mark, 24, color)}'
                         f'<div class="pair-tag-t">{esc(tag)}</div></div>')
            cells.append(f'<div class="pair-box {cls}" style="{box(bx, by, bw, bh)}"><div style="font-size:{fs}px">{rich(it[key])}</div></div>')
        arrow = (f'<svg class="abs-svg" style="{box(bx1 + bw + 24, by + bh / 2 - 22, aw, 44)}" width="{aw}" height="44" viewBox="0 0 56 44">'
                 '<polygon points="0,13 32,13 32,2 56,22 32,42 32,31 0,31" style="fill:var(--primary-mark)"/></svg>')
        parts.append(f'<div class="card pair" style="{box(x0, y0, pw, ph)}" {s.anim(i + 1, "rise", 0, f"組{i + 1}", item=True)}>'
                     f'<div class="pair-left" style="{box(40, 0, lw, phi)}">{left}</div>' + cells[0] + cells[1] + arrow + cells[2] + cells[3] + "</div>")
    s.need, s.avail = avail, avail
    parts.append(source_line(s))
    return parts


# --- DADS のコンポーネントから足した型（ADR 0021）: お知らせの枠・用語と説明・手順の案内 ---------------------

# 種類 → (既定のラベル, アイコン, 枠と色帯の色（意味の色の -1・3:1）, 文字とアイコンの色（-2・4.5:1）)。意味の色は白い面の上だけ
NOTICE_KINDS = {
    "info": ("お知らせ", "info", "var(--primary-mark)", "var(--primary-text)"),
    "success": ("完了", "check", "var(--success-1)", "var(--success-2)"),
    "warning": ("注意", "alert", "var(--warning-orange-1)", "var(--warning-orange-2)"),
    "error": ("重要", "error", "var(--error-1)", "var(--error-2)"),
}


def lead_icon_box(size: int) -> int:
    """ラベルの文字の大きさに合わせたリードアイコンの箱（slide-local.md の 6: 20〜28 → 32・32 → 40・36 → 48・45 → 56）。"""
    return 32 if size <= 28 else 40 if size <= 32 else 48 if size <= 36 else 56


def _notice_boxes(s: Slide, its, drop: int, avail: float, gap: int):
    """notice の枠の HTML の列と、全部の高さの見積もり。文字は段を drop だけ下りる。"""
    n = len(its)
    iw = CW - 2 * BORDER - 16 - 80                     # 枠・色帯 16・左右の余白 40
    boxes, total = [], 0.0
    for i, it in enumerate(its):
        where = f"items の {i + 1} 個目"
        kind = it.get("kind", "info")
        if kind not in NOTICE_KINDS:
            s.err(f"{where}の kind は {' / '.join(NOTICE_KINDS)} のどれか（{kind!r}）")
        dlabel, icon, line, strong = NOTICE_KINDS[kind]
        label, body = s.text("label", spec=it) or dlabel, s.text("body", spec=it)
        pts = it.get("points") or []
        if not isinstance(pts, list) or len(pts) > 4 or not all(isinstance(x, str) and x.strip() for x in pts):
            s.err(f"{where}の points は 4 つまでの文字の配列")
        ls, _ = s.fit("nt-label", label, step_down(28, drop, 24), iw - 56, what=f"{where}の label", lines=1, lh=1.5, min_size=24, bold=True)
        ib = lead_icon_box(ls)
        head_h = max(ib, ls * 1.5)
        ts, tn = s.fit("nt-title", it["title"], step_down(s.g(45 if n == 1 else 36 if n == 2 else 32, 48), drop, 28), iw, what=f"{where}の title",
                       lines=2, lh=1.4, min_size=28, bold=True, tidy=True)
        inner = [f'<div class="nt-head"><div style="display:flex; width:{ib}px; height:{ib}px; margin-right:16px; flex:none">{ICON.svg(icon, ib, strong)}</div>'
                 f'<div class="nt-label" style="font-size:{ls}px; color:{strong}">{esc(label)}</div></div>',
                 f'<div class="nt-title" style="font-size:{ts}px">{rich(it["title"])}</div>']
        h = head_h + 8 + tn * ts * 1.4
        if body:
            bs, bn = s.fit("nt-body", body, step_down(s.g(32 if n == 1 else 28, 36), drop, 24), iw, under="nt-title", what=f"{where}の body",
                           lines=3, lh=1.6, min_size=24)
            inner.append(f'<div class="nt-body" style="font-size:{bs}px">{rich(body)}</div>')
            h += 8 + bn * bs * 1.6
        for j, p_ in enumerate(pts):
            ps, pn = s.fit("nt-point", p_, step_down(s.g(28, 32), drop, 24), iw - 32, under="nt-title", what=f"{where}の points の {j + 1} 個目",
                           lines=2, lh=1.5, min_size=24)
            inner.append(f'<div class="nt-point"><div class="nt-mark" style="background-color:{line}; margin-top:{(ps * 1.5 - 16) / 2:.0f}px"></div>'
                         f'<div class="nt-text" style="font-size:{ps}px">{rich(p_)}</div></div>')
            h += 8 + pn * ps * 1.5
        h += 2 * 32 + 2 * BORDER
        total += h
        boxes.append(f'<div class="notice" style="width:{px(CW)}; border-color:{line}" {s.anim(i + 1, "rise", 0, f"お知らせ{i + 1}", item=True)}>'
                     f'<div class="nt-band" style="background-color:{line}"></div><div class="nt-main">' + "".join(inner) + "</div></div>")
    return boxes, total + gap * (n - 1)


def t_notice(s: Slide):
    """お知らせの枠（DADS の注釈ブロック）: 補足・注意・決まりごとを、種類（info・success・warning・error）つきの枠で1〜3個。

    枠（意味の色の -1・2px）と左の色帯の白い面に、アイコン＋種類の語（label）・題・本文（短い箇条 points も）。色だけで種類を示さない。
    1個ずつクリックで出る。全部が入るまで、文字を同じだけ段で下りる。
    """
    head, top = header(s)
    its = s.items("items", 1, 3, ("kind", "label", "title", "body", "points"), required=("title",))
    gap, avail = 24, s.bottom - top
    for drop in range(4):
        s.seen = {}                                    # 組んだ大きさの記録は、段を下りるたびに取り直す
        boxes, total = _notice_boxes(s, its, drop, avail, gap)
        if total <= avail + 0.5:
            break
    else:
        s.err(f"お知らせが多すぎて入らない（{total:.0f}px・入るのは {avail:.0f}px。題・本文・箇条を短くするか、個数を減らす）")
    s.need, s.avail = total, avail
    return [head, f'<div class="center" style="{box(X0, top, CW, avail)}"><div class="col" style="gap:{gap}px">' + "".join(boxes) + "</div></div>",
            source_line(s)]


def t_deflist(s: Slide):
    """用語と説明（DADS の説明リスト）: 左に用語（太字）・右に説明の2列で 3〜8 行。行の区切りは rule。1行ずつクリックで出る。

    見出しの下から始める。行が少なく余るときは文字の段を上げる（GROWABLE）。用語の列は格子の 3 列（長ければ 4 列）で固定し、説明はその残りに折り返す。用語と説明は同じ大きさで、先頭の行がそろう。
    項目名と内容の対でないもの（並べるだけの要点・比べる表）には使わない。
    """
    head, top = header(s)
    its = s.items("items", 3, 8, ("term", "body", "icon"), required=("term", "body"))
    icons = [icon_of(s, it, f"items の {i + 1} 個目") for i, it in enumerate(its)]
    if any(icons) and not all(icons):
        s.err("items の icon は、書くなら全部の行に書く（用語の頭がそろわない）")
    n = len(its)
    avail = s.bottom - top
    size = s.g(32 if n <= 5 else 28, 45)
    ib = lead_icon_box(size) if icons[0] else 0
    lead = ib + 16 if ib else 0
    longest = max(text_width(it["term"], 28, True) for it in its)
    tcol = span(3) if longest + lead <= span(3) * SAFE else span(4)
    bw = CW - tcol - GUTTER
    row_max = avail / n
    pad = s.gap(16)                                    # 行の上下の余白（大きく組むときは広げる）
    rows, total = [], 0.0
    for i, it in enumerate(its):
        where = f"items の {i + 1} 個目"
        ts, tn = s.fit("dl", it["term"], size, tcol - lead, what=f"{where}の term", height=row_max - 2 * pad - BORDER, lh=1.5, min_size=24, bold=True)
        bs, bn = s.fit("dl", it["body"], size, bw, what=f"{where}の body", height=row_max - 2 * pad - BORDER, lh=1.5, min_size=24)
        total += max(tn * ts, bn * bs) * 1.5 + 2 * pad + BORDER
        rows.append((it, ts, bs))
    if total > avail + 0.5:
        s.err(f"行が多すぎて入らない（{total:.0f}px・入るのは {avail:.0f}px。用語・説明を短くするか行を減らす）")
    out = []
    for i, (it, ts, bs) in enumerate(rows):
        pic = (f'<div style="display:flex; width:{ib}px; height:{ib}px; margin: {(ts * 1.5 - ib) / 2:.0f}px 16px 0 0; flex:none">'
               f'{ICON.svg(icons[i], ib, "var(--primary-text)")}</div>') if ib else ""
        out.append(f'<div class="dl-row{" first" if i == 0 else ""}" style="width:{px(CW)}; padding:{pad}px 0" {s.anim(i + 1, "rise", 0, f"行{i + 1}", item=True)}>'
                   f'<div class="dl-head" style="width:{px(tcol)}">{pic}<div class="dl-term" style="font-size:{ts}px">{rich(it["term"])}</div></div>'
                   f'<div class="dl-body" style="width:{px(bw)}; margin-left:{GUTTER}px; font-size:{bs}px">{rich(it["body"])}</div></div>')
    s.need, s.avail = total, avail
    return [head, f'<div class="col" style="{box(X0, top, CW, avail)} position:absolute;">' + "".join(out) + "</div>", source_line(s)]   # 見出しの下から始める（行が少なければ grow で文字の段を上げる）


STEP_STATES = {"done": ("済", "done"), "current": ("いま", "on"), "todo": ("これから", "todo")}   # 状態 → (札の語, 札の種類)
ST_CIRCLE, ST_CHIP_W = 80, 144        # 番号の丸の枠・札の幅（3つの札を同じ幅にして、題の頭をそろえる）


def _step_no(n: int, state: str) -> str:
    """番号の丸（枠は 80 四方）。済＝主役の面に白抜き・いま＝差し色の二重の輪・これから＝白に太い枠。番号は文字で書く（形でも状態が分かる）。"""
    if state == "current":
        return (f'<div class="st-no cur"><div class="st-dot" style="width:56px; height:56px; background-color:var(--accent-strong); color:var(--on-accent)">{n}</div></div>')
    if state == "done":
        return (f'<div class="st-no"><div class="st-dot" style="width:64px; height:64px; background-color:var(--primary-strong); color:var(--on-primary)">{n}</div></div>')
    return (f'<div class="st-no"><div class="st-dot" style="width:64px; height:64px; background-color:var(--bg); color:var(--ink); border:4px solid var(--rule)">{n}</div></div>')


def _step_chip(state: str) -> str:
    word, kind = STEP_STATES[state]
    icon = "check" if state == "done" else None
    c = chip(word, kind, icon, "var(--primary-text)")
    return c.replace('<div class="chip ', f'<div style="width:{ST_CHIP_W}px; justify-content:center; margin-bottom:0" class="chip ', 1)


def _step_link(s: Slide, x, y, length, vertical: bool, done: bool, step: int, name: str) -> str:
    """段と段をつなぐ線（4px）。進んだ所は実線の primary-mark、これからは破線の rule（線種でも示す）。"""
    color = "var(--primary-mark)" if done else "var(--rule)"
    dash = "" if done else ' stroke-dasharray="16 12"'
    if vertical:
        w_, h_, ln = 8, length, f'<line x1="4" y1="0" x2="4" y2="{length:.0f}" stroke-width="4"{dash} style="stroke:{color}"/>'
    else:
        w_, h_, ln = length, 8, f'<line x1="0" y1="4" x2="{length:.0f}" y2="4" stroke-width="4"{dash} style="stroke:{color}"/>'
    return (f'<svg class="abs-svg" style="{box(x, y, w_, h_)}" width="{w_:.0f}" height="{h_:.0f}" viewBox="0 0 {w_:.0f} {h_:.0f}" '
            f'{s.anim(step, "wipe-down" if vertical else "wipe-right", 0, name)}>{ln}</svg>')


def t_steps(s: Slide):
    """手順の案内（DADS のステップナビゲーション）: 番号の丸・状態の札・題・補足の段を並べ、どこまで済んでいて今どこかを見せる。

    state は done（済）・current（いま）・todo（これから）。`current`（1から）を書けば、それより前が done・後ろが todo になる（段ごとの state が優先）。
    色だけにせず、丸の形（塗り・二重の輪・白に枠）・札の語・線の種類（進んだ所は実線・これからは破線）で示す。
    layout: row（横・3〜5段・既定）か column（縦・3〜6段）。1段ずつクリックで出る。順番だけを説明するなら flow。
    """
    head, top = header(s)
    layout = s.spec.get("layout", "row")
    if layout not in ("row", "column"):
        s.err("layout は row（横・3〜5段）か column（縦・3〜6段）")
    its = s.items("items", 3, 5 if layout == "row" else 6, ("title", "body", "state"), required=("title",))
    n = len(its)
    cur = s.spec.get("current")
    if cur is not None and (isinstance(cur, bool) or not isinstance(cur, int) or not 1 <= cur <= n):
        s.err(f"current は 1〜{n} の整数")
    states = []
    for i, it in enumerate(its):
        st = it.get("state") or (None if cur is None else "done" if i + 1 < cur else "current" if i + 1 == cur else "todo")
        if st is None and not any(x.get("state") for x in its):
            s.err("steps は進み具合を見せる型。current（いま何段目か）か、段ごとの state（done・current・todo）を書く（順番だけを説明するなら flow）")
        st = st or "todo"
        if st not in STEP_STATES:
            s.err(f"items の {i + 1} 個目の state は {' / '.join(STEP_STATES)} のどれか（{st!r}）")
        states.append(st)
    if states.count("current") > 1:
        s.err("state: current（いま）は 1 つの段だけ")
    avail = s.bottom - top
    parts = [head]
    if layout == "row":
        cw = (CW - GUTTER * (n - 1)) / n
        body_room = avail - ST_CIRCLE - 16 - CHIP_H - 16 - 8
        cols, tallest = [], 0.0
        for i, it in enumerate(its):
            where = f"items の {i + 1} 個目"
            ts, tn = s.fit("st-title", it["title"], 36 if n <= 4 else 32, cw, what=f"{where}の title", lines=2, lh=1.4, min_size=28, bold=True, tidy=True)
            inner = (f'<div class="st-title" style="font-size:{ts}px">{rich(it["title"])}</div>')
            h = tn * ts * 1.4
            body = s.text("body", spec=it)
            if body:
                bs, bn = s.fit("st-body", body, 28, cw, under="st-title", what=f"{where}の body", height=body_room - tn * ts * 1.4, lh=1.6, min_size=24)
                inner += f'<div class="st-body" style="font-size:{bs}px">{rich(body)}</div>'
                h += 8 + bn * bs * 1.6
            tallest = max(tallest, h)
            cols.append(inner)
        group = ST_CIRCLE + 16 + CHIP_H + 16 + tallest
        gy = top + max(0.0, (avail - group) / 2)
        for i, (inner, st) in enumerate(zip(cols, states)):
            x = X0 + i * (cw + GUTTER)
            parts.append(f'<div class="st-col" style="{box(x, gy, cw)}" {s.anim(i + 1, "rise", 0, f"手順{i + 1}", item=True)}>'
                         f'{_step_no(i + 1, st)}<div style="margin-top:16px">{_step_chip(st)}</div>'
                         f'<div style="margin-top:16px">{inner}</div></div>')
            if i < n - 1:
                parts.append(_step_link(s, x + ST_CIRCLE + 8, gy + ST_CIRCLE / 2 - 4, cw + GUTTER - ST_CIRCLE - 16, False,
                                        st == "done", i + 2, f"線{i + 1}"))
    else:
        tw = CW - ST_CIRCLE - 32
        blocks, hs = [], []
        size = 36 if n <= 4 else 32
        for i, it in enumerate(its):
            where = f"items の {i + 1} 個目"
            ts, tn = s.fit("st-title", it["title"], size, tw - ST_CHIP_W - 16, what=f"{where}の title", lines=2, lh=1.4, min_size=28, bold=True)
            body = s.text("body", spec=it)
            h = max(CHIP_H, tn * ts * 1.4)
            inner = (f'<div class="st-line">{_step_chip(states[i])}<div class="st-title" style="font-size:{ts}px; margin-left:16px">{rich(it["title"])}</div></div>')
            if body:
                bs, bn = s.fit("st-body", body, 28, tw, under="st-title", what=f"{where}の body", lines=2, lh=1.6, min_size=24)
                inner += f'<div class="st-body" style="font-size:{bs}px; margin-top:8px">{rich(body)}</div>'
                h += 8 + bn * bs * 1.6
            blocks.append(inner)
            hs.append(max(float(ST_CIRCLE), h))
        gap = next((g for g in (40, 24) if sum(hs) + g * (n - 1) <= avail + 0.5), None)
        if gap is None:
            s.err(f"段が高すぎて入らない（{sum(hs) + 24 * (n - 1):.0f}px・入るのは {avail:.0f}px。題・補足を短くするか段を減らす）")
        gy = top + (avail - sum(hs) - gap * (n - 1)) / 2
        y = gy
        for i, (inner, st) in enumerate(zip(blocks, states)):
            parts.append(f'<div class="st-row" style="{box(X0, y, CW, hs[i])}" {s.anim(i + 1, "rise", 0, f"手順{i + 1}", item=True)}>'
                         f'{_step_no(i + 1, st)}<div class="st-text" style="width:{px(tw)}; margin-left:32px">{inner}</div></div>')
            if i < n - 1:
                parts.append(_step_link(s, X0 + ST_CIRCLE / 2 - 4, y + ST_CIRCLE + 8, hs[i] - ST_CIRCLE + gap - 16, True,
                                        st == "done", i + 2, f"線{i + 1}"))
            y += hs[i] + gap
    parts.append(source_line(s))
    return parts


def takeaway_band(s: Slide) -> str:
    """締めの帯（takeaway）: 中身の下に、結論を1行。最後の段と一緒に出る（クリックは増やさない）。

    DADS の注釈ブロック（notice-block）に倣い、頭にアイコン（台本の takeaway_icon。無ければ info）・外周（primary-mark の 2px）・角は full。
    文字は 32B-140。アイコンは語と組む（アイコンだけにしない）。
    """
    text = s.text("takeaway")
    name = icon_of(s, {"icon": s.spec.get("takeaway_icon") or "info"}, "takeaway_icon ")
    ts, _ = fit(text, 32, CW - 128 - 56, what="takeaway", lines=1, lh=1.4, min_size=28, bold=True)
    w = min(CW, -(-max(904.0, text_width(plain(text), ts, True) / SAFE + 128 + 56) // 16) * 16)    # 16 の倍数（左右の位置が 8 の倍数になる）
    step = max(s.steps) if s.steps else 0
    return (f'<div class="take" style="{box(X0 + (CW - w) / 2, BODY_BOTTOM - TAKE_H, w, TAKE_H)}" '
            f'{s.anim(step, "fade", 500, "締めの帯")}>{ICON.svg(name, 40, "var(--primary-text)")}'
            f'<div class="take-t" style="font-size:{ts}px">{rich(text)}</div></div>')


# 中身が少ないとき（型の見積もった高さが置ける高さの GROW_FILL 未満）は、文字を段で上り、間隔を広げて組み直す。
# 上る段の数は GROW_STEPS の順に試す（役ごとの上限は Slide.g の hi）。
# 画像のあるページは大きくしない（画像の枠の大きさが変わると、描いた絵の区画が変わる）
GROWABLE = {"cards", "flow", "compare", "bullets", "closing", "timeline", "summary", "notice", "deflist"}
GROW_STEPS = (2, 1)
GROW_FILL = 0.7

# 型の棚: 名前 → (関数, 台本で使えるキー, 見出しの帯・下の文字・ページ番号を付けるか)
HEAD_KEYS = {"kicker", "headline", "sub", "source"}
TYPES = {
    "cover": (t_cover, {"label", "title", "subtitle", "meta", "image", "layout", "text_side", "tone", "motif"}, False),
    "agenda": (t_agenda, HEAD_KEYS | {"items", "highlight"}, True),
    "section": (t_section, {"number", "label", "title", "subtitle", "image", "layout", "text_side", "tone", "motif"}, False),
    "statement": (t_statement, {"kicker", "headline", "body", "source"}, True),
    "big-number": (t_big_number, HEAD_KEYS | {"value", "unit", "label", "note", "side"}, True),
    "cards": (t_cards, HEAD_KEYS | {"items"}, True),
    "flow": (t_flow, HEAD_KEYS | {"items"}, True),
    "compare": (t_compare, HEAD_KEYS | {"left", "right", "accent", "arrow"}, True),
    "bar-chart": (t_bar_chart, HEAD_KEYS | {"bars", "unit", "note", "max", "callout"}, True),
    "table": (t_table, HEAD_KEYS | {"columns", "rows", "highlight", "widths"}, True),
    "photo": (t_photo, {"image", "layout", "split", "kicker", "headline", "body", "caption"}, False),
    "quote": (t_quote, {"quote", "by", "position", "kicker", "source", "layout", "image"}, True),
    "bullets": (t_bullets, HEAD_KEYS | {"items", "image", "side", "split"}, True),
    "timeline": (t_timeline, HEAD_KEYS | {"items", "current"}, True),
    "closing": (t_closing, HEAD_KEYS | {"items", "contact", "image"}, True),
    "summary": (t_summary, HEAD_KEYS | {"points", "label"}, True),
    "kpi": (t_kpi, HEAD_KEYS | {"items", "image"}, True),
    "line-chart": (t_line_chart, HEAD_KEYS | {"labels", "series", "unit", "min", "max", "note", "callout", "value_labels"}, True),
    "team": (t_team, HEAD_KEYS | {"members"}, True),
    "matrix": (t_matrix, HEAD_KEYS | {"x", "y", "quadrants", "focus", "items", "callout"}, True),
    "gallery": (t_gallery, HEAD_KEYS | {"items", "layout"}, True),
    "figure": (t_figure, HEAD_KEYS | {"image", "caption"}, True),
    "annotate": (t_annotate, HEAD_KEYS | {"image", "items", "fit"}, True),
    "equation": (t_equation, HEAD_KEYS | {"items", "result"}, True),
    "hub": (t_hub, HEAD_KEYS | {"center", "items"}, True),
    "pairs": (t_pairs, HEAD_KEYS | {"items", "bad_label", "good_label"}, True),
    "notice": (t_notice, HEAD_KEYS | {"items"}, True),
    "deflist": (t_deflist, HEAD_KEYS | {"items"}, True),
    "steps": (t_steps, HEAD_KEYS | {"items", "current", "layout"}, True),
}
# 退役した型（型の名前 → 代わりの型の案内）。台本に書いてあれば今までどおり描くが、使うと警告し、台本の書き方（deck-format.md）・
# 台本係の一覧・カタログには載せない。描く部分は残す（過去のデッキを組み直せるように）。今は無し（ADR 0021）
RETIRED_TYPES: dict[str, str] = {}
# 締めの帯（takeaway）を置ける型（見出しと中身の型。表紙・章扉・写真・引用・締めなど、それ自体が言い切りの型には置かない）。
# 中身の高さを確かめていない型（big-number の補足・team の2段）にも置かない（帯に重なる）
# notice は枠そのものが言い切りの型なので置かない。deflist・steps は置ける
TAKEAWAY_TYPES = ("cards", "flow", "compare", "bullets", "timeline", "summary", "kpi", "bar-chart", "line-chart",
                  "table", "matrix", "equation", "hub", "pairs", "deflist", "steps")
for _t in TAKEAWAY_TYPES:
    TYPES[_t] = (TYPES[_t][0], TYPES[_t][1] | {"takeaway", "takeaway_icon"}, TYPES[_t][2])


def build_slide(s: Slide) -> tuple[list[str], bool]:
    """chrome（上の帯・下の文字・ページ番号）は型の既定。置き方で型が s.chrome を変える。上の帯だけ外すときは s.no_band"""
    """1枚の部品と、帯・下の文字を付けるかを返す。未知の型・未知のキーは DeckError。"""
    if s.type not in TYPES:
        s.err(f"知らない型（使えるのは {', '.join(TYPES)}）")
    fn, keys, chrome = TYPES[s.type]
    extra = sorted(k for k in set(s.spec) - keys - COMMON_KEYS if not k.startswith("_"))
    if extra:
        s.err(f"知らないキー: {', '.join(extra)}（この型で使えるのは {', '.join(sorted(keys))}）")
    if s.spec.get("takeaway_icon") and not s.spec.get("takeaway"):
        s.err("takeaway_icon は takeaway（結論の1行）と一緒に書く")
    if s.type in RETIRED_TYPES:
        s.warnings.append(f"{s.no}枚目: 型 {s.type} は退役した（{RETIRED_TYPES[s.type]}）。今までどおり描くが、新しい台本では使わない")
    parts = _compose(s, fn)
    if s.type in GROWABLE and not _has_image(s.spec) and s.need is not None and s.need < GROW_FILL * s.avail:
        for f in GROW_STEPS:                    # 中身が少ないときは、文字を大きくして場所を使う。入る一番大きい倍率
            s.grow = f
            try:
                grown = _compose(s, fn)
            except DeckError:
                continue
            if s.need is not None and s.need <= GROW_FILL * s.avail:
                parts = grown
                break
        else:
            s.grow = 0
            parts = _compose(s, fn)
    if s.spec.get("takeaway") not in (None, ""):
        parts.append(takeaway_band(s))
    return parts, getattr(s, "chrome", chrome)


def _compose(s: Slide, fn) -> list[str]:
    """型を組む。並ぶ項目の同じ役の文字の大きさがそろわなければ、一番小さい大きさを上限にして組み直す（画像のあるページは除く）。"""
    s.caps, s.seen, s.need = {}, {}, None
    parts = [p for p in fn(s) if p]
    # 画像のあるページは組み直さない（画像の枠・キャラの参照・警告は組むたびに足されるため。大きさは項目ごとのまま）
    if not _has_image(s.spec) and any(len(set(v)) > 1 for v in s.seen.values()):
        s.caps, s.seen, s.need = {k: min(v) for k, v in s.seen.items()}, {}, None
        parts = [p for p in fn(s) if p]
    return parts


def _has_image(v) -> bool:
    if isinstance(v, dict):
        return "image" in v or any(_has_image(x) for x in v.values())
    return isinstance(v, list) and any(_has_image(x) for x in v)
