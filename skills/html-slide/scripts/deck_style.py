"""スタイル（ADR 0005）。資料全体の見せ方の既定を決める。名前は image-slide とそろえる（standard・visual）。

html-slide のスタイルは、**決まった項目の値だけ**を持つ（文章を読んで解釈する仕組みは無い）:

    name: visual                     # 必須
    description: ビジュアル寄り…       # 任意。選ぶ人向けの補足
    defaults:                        # 任意。型ごとの変化形の既定（台本に書いてあれば台本が勝つ）
      cover: {layout: hero}
      photo: {layout: left, split: 1/2}
    image_look: photo                # 任意。Codex で描く画像の枠の画風（photo・illustration・handdrawn。--image-look が勝つ・ADR 0011）
    expects_images: [cover, section] # 任意。画像を置くのが基本の型。image が無ければ警告する（止めない）
    motion:                          # 任意。動きの既定
      fx: fade                       # 並びの出方（台本の fx が勝つ）
      transition: fade               # 切り替え（台本の transition が勝つ。fade か none。morph は退役・mode: handout は動きなし）

- 知らないキー・知らない型・その型に無い変化形・選べない値は rc=2（DeckError）
- 画像が要る変化形は、そのページに画像があるときだけ当てる（無いページは型の既定のまま。止めない）
- 組み込みは `styles/standard.yaml`（既定・今までの見た目）と `styles/visual.yaml`。カスタムは同じ形の YAML を `--style <パス>` で
- 見た目の調子（余白・見出しの大きさの段階）はまだ持たない（文字のはみ出しの検査とぶつかりやすいため・ADR 0005）
"""
from __future__ import annotations

import pathlib

import deck_types as T
from deck_types import DeckError

SKILL = pathlib.Path(__file__).resolve().parent.parent
STYLES = SKILL / "styles"
DEFAULT = "standard"
FIELDS = {"name", "description", "defaults", "motion", "look", "image_look", "expects_images"}
# 見た目の調子（look）。文字の大きさ・余白は変えない（はみ出しの検査は字体で実測しているため）。色は全部テーマから作り、読みやすさの検査も通す
LOOK = {
    "background": ("plain", "tint"),                    # ページの地: テーマの地／主役の色（primary）をうっすら混ぜた地
    "surface": ("soft", "white", "primary", "line"),     # カード・比較の列・KPI・吹き出しの地: 淡いグレー／（white は退役・RETIRED_LOOKS）／主役の色の淡い地／白に枠線
    "radius": ("default", "square", "round"),           # 箱の角: 16px／4px／32px（写真は 8px・square のときだけ 4px。notice の枠は default で 0・式の答えと NG／OK の箱は 16px・12px）
    "band": ("small", "bar", "none"),                   # 中身の型の左上の印: 小さな印／上端の全幅の帯／無し
    "emphasis": ("default", "strong"),                  # 強調する箱（accent: true のカードなど）: 淡い地に枠／差し色のベタ塗りに白抜き（明るい色なら黒）
    "statement": ("plain", "panel"),                    # 主張の1枚（statement）: 地のまま／主役の色の全面に白抜き（明るい色なら黒）
}
# 退役した変化形（look の項目, 値）→ 代わりの値。YAML には今までどおり書けて、代わりの値と同じに描くが、読んだときに注意を出し、
# 台本の書き方（deck-format.md）・台本係の一覧には載せない（ADR 0020・0021）。white は白に影（0 8px 28px）で、影は elevation の番号の丸だけにしたため
RETIRED_LOOKS = {("surface", "white"): "line"}
TRANSITIONS = ("fade", "none")
# 退役した切り替え（motion.transition）→ 代わりの値。YAML には今までどおり書け、代わりの値で描き、読んだとき注意を出す（ADR 0023）
RETIRED_TRANSITIONS = {"morph": "fade"}
HALVES = ("1/2", "2/5", "1/3")

# 型 → 変化形のキー → 選べる値（deck_types の各型が受ける値と同じ）
VARIANTS = {
    "cover": {"layout": ("split", "hero"), "text_side": ("left", "right"), "tone": ("light", "dark")},
    "section": {"layout": ("split", "hero"), "text_side": ("left", "right"), "tone": ("light", "dark")},
    "photo": {"layout": ("full", "left", "right", "band"), "split": HALVES},
    "quote": {"layout": ("plain", "full", "portrait")},
    "bullets": {"side": ("right", "left"), "split": ("1/3", "2/5", "1/2")},
    "gallery": {"layout": ("row", "collage")},
    "annotate": {"fit": ("cover", "contain")},
    "line-chart": {"value_labels": ("all", "last", "none")},
    "compare": {"arrow": (True, False)},
}


def load(spec: str | None) -> dict:
    """--style の値（standard・visual・YAML のパス）から {name, description, defaults, motion, path}。誤りは DeckError。"""
    spec = spec or DEFAULT
    if "/" not in spec and not spec.endswith((".yaml", ".yml")):
        path = STYLES / f"{spec}.yaml"
        if not path.is_file():
            have = ", ".join(sorted(p.stem for p in STYLES.glob("*.yaml")))
            raise DeckError(f"スタイルが無い: {spec}（組み込みは {have}。カスタムは YAML のパス）")
    else:
        path = pathlib.Path(spec).expanduser().resolve()
        if not path.is_file():
            raise DeckError(f"カスタムスタイルのファイルが無い（--style）: {path}")
    import yaml
    try:
        d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise DeckError(f"{path}: スタイルの YAML が読めない（{e}）")
    if not isinstance(d, dict):
        raise DeckError(f"{path}: スタイルは name・description・defaults・motion のキーで書く")
    extra = [k for k in d if k not in FIELDS]
    if extra:
        raise DeckError(f"{path}: スタイルに知らないキー: {', '.join(map(repr, extra))}（使えるのは {', '.join(sorted(FIELDS))}）")
    if not isinstance(d.get("name"), str) or not d["name"].strip():
        raise DeckError(f"{path}: name が無い")
    if d.get("description") is not None and not isinstance(d["description"], str):
        raise DeckError(f"{path}: description は文字で書く")
    defaults = {} if d.get("defaults") is None else d["defaults"]     # 「defaults:」だけ（null）は空。false・0・[] は誤り
    if not isinstance(defaults, dict):
        raise DeckError(f"{path}: defaults は「型: {{変化形: 値}}」で書く")
    for typ, vals in defaults.items():
        if typ not in VARIANTS:
            have = ", ".join(VARIANTS)
            raise DeckError(f"{path}: defaults の {typ!r} は変化形を持たない型か、知らない型（使えるのは {have}）")
        if not isinstance(vals, dict):
            raise DeckError(f"{path}: defaults.{typ} は {{変化形: 値}} で書く")
        for k, v in vals.items():
            if k not in VARIANTS[typ]:
                raise DeckError(f"{path}: {typ} に変化形 {k!r} は無い（あるのは {', '.join(VARIANTS[typ])}）")
            if v not in VARIANTS[typ][k] or type(v) is not type(VARIANTS[typ][k][0]):
                raise DeckError(f"{path}: {typ}.{k} は {' / '.join(map(str, VARIANTS[typ][k]))} のどれか（{v!r}）")
    motion = {} if d.get("motion") is None else d["motion"]
    if not isinstance(motion, dict) or any(k not in ("fx", "transition") for k in motion):
        raise DeckError(f"{path}: motion に書けるのは fx と transition だけ")
    if "fx" in motion and motion["fx"] not in T.FX:
        raise DeckError(f"{path}: motion.fx は {' / '.join(T.FX)} のどれか（{motion['fx']!r}）")
    retired_motion = []
    if motion.get("transition") in RETIRED_TRANSITIONS:
        new = RETIRED_TRANSITIONS[motion["transition"]]
        retired_motion.append(f"スタイル {d['name'].strip()}: motion.transition: {motion['transition']} は退役した（ブラウザで再現できない・{new} で描く）。{new} に書き換える")
        motion = {**motion, "transition": new}
    if "transition" in motion and motion["transition"] not in TRANSITIONS:
        raise DeckError(f"{path}: motion.transition は {' / '.join(TRANSITIONS)} のどれか（{motion['transition']!r}）")
    look = {} if d.get("look") is None else d["look"]
    if not isinstance(look, dict):
        raise DeckError(f"{path}: look は「項目: 値」で書く")
    for k, v in look.items():
        if k not in LOOK:
            raise DeckError(f"{path}: look に {k!r} は無い（あるのは {', '.join(LOOK)}）")
        if v not in LOOK[k]:
            raise DeckError(f"{path}: look.{k} は {' / '.join(LOOK[k])} のどれか（{v!r}）")
    import imagegen as G
    if d.get("image_look") is not None and d["image_look"] not in G.IMAGE_LOOKS:
        raise DeckError(f"{path}: image_look は {' / '.join(G.IMAGE_LOOKS)} のどれか（{d['image_look']!r}）")
    expects = d.get("expects_images") or []
    if not isinstance(expects, list) or any(t not in T.TYPES for t in expects):
        raise DeckError(f"{path}: expects_images は型の名前の配列で書く（{expects!r}）")
    retired = [f"スタイル {d['name'].strip()}: look.{k}: {v} は退役した（{RETIRED_LOOKS[(k, v)]} と同じに描く）。{RETIRED_LOOKS[(k, v)]} に書き換える"
               for k, v in look.items() if (k, v) in RETIRED_LOOKS]
    return {"name": d["name"].strip(), "description": (d.get("description") or "").strip(), "retired": retired + retired_motion,
            "defaults": defaults, "motion": motion, "look": look, "path": path,
            "image_look": d.get("image_look"), "expects_images": tuple(expects)}


def look_theme(theme: dict, look: dict) -> dict:
    """look が色に効くもの（background: tint）をテーマに当てる。色の組み立てと読みやすさの検査は、このあとの palette に任せる。"""
    if look.get("background") == "tint":
        import deck as _d
        theme = {**theme, "background": _d.mix(theme["primary"], 0.05, theme["background"])}
    return theme


BOXES = ".card, .cmp, .kpi, .callout"
HL = ".card.hl, .cmp.hl, .kpi.hl"


def look_css(look: dict) -> str:
    """look を CSS にする（テンプレートの {{LOOK}} に入る）。使うのは HTML の決まりの許可リストの中だけ（地の色・一様な枠・角丸・外側の影）。"""
    css = []
    s = look.get("surface", "soft")
    s = RETIRED_LOOKS.get(("surface", s), s)    # white（白に影）は退役。影は elevation の番号の丸だけなので、line（白に枠線）と同じに描く（ADR 0020）
    if s == "line":
        css.append(f"{BOXES} {{ background-color: var(--bg); border: 2px solid var(--rule); }}")
        css.append(".tr.th { background-color: var(--bg); }")
    elif s == "primary":
        css.append(f"{BOXES} {{ background-color: var(--primary-soft); }}")
        css.append(".tr.th { background-color: var(--primary-soft); }")
    r = {"square": 4, "round": 32}.get(look.get("radius"))
    if r:
        css.append(f"{BOXES}, .contact, .scrim, .side-img, .gal-img, .notice, .eq-res, .pair-box {{ border-radius: {r}px; }}")
        css.append(f".nt-main {{ border-radius: {r - 2}px; }}")   # notice の白い面は枠（2px）の内側。帯と枠は同じ色の地なので、角の内側に白い隙間も帯のはみ出しも出ない
        if r == 4:
            css.append(".card-img { border-radius: 4px; }")
    b = look.get("band", "small")
    if b == "bar":
        css.append(".band { left: 0; width: 1920px; height: 16px; }")
    elif b == "none":
        css.append(".band { display: none; }")
    if look.get("emphasis") == "strong":
        css.append(f"{HL} {{ background-color: var(--accent-strong); border-color: var(--accent-strong); }}")
        css.append(f".card.hl div, .cmp.hl div, .kpi.hl div:where(:not(.kpi-delta)), .card.hl em, .cmp.hl em, .kpi.hl em {{ color: var(--on-accent); }}")   # 増減の札は tone の色のまま（:where で優先度は元のまま。札の色の規則に勝たない）
        css.append(".card.hl .card-bar, .cmp.hl .cmp-dot { background-color: var(--on-accent); }")
        css.append(".kpi.hl .kpi-delta { border-color: transparent; }")   # 濃い面の上では tone の色の枠が面に沈むので、札の地の色を輪郭にする
        css.append(".kpi.hl .kpi-value, .kpi.hl .kpi-unit { color: var(--on-accent); }")   # 型の CSS の方が強いので名指しで上書き
        css.append(".cmp.hl .cmp-chip { background-color: var(--on-accent); color: var(--accent-strong); }")
        css.append(".card.hl .chip, .cmp.hl .chip, .kpi.hl .chip { background-color: var(--on-accent); color: var(--accent-strong); }")   # 強める箱の札は面と反対の色
    if look.get("statement") == "panel":
        st = 'section[data-type="statement"]'
        css.append(f"{st} {{ background-color: var(--primary-strong); }}")
        css.append(f"{st} div, {st} em {{ color: var(--on-primary); }}")
        css.append(f"{st} .stmt-bar, {st} .band {{ background-color: var(--on-primary); }}")
    return "\n      ".join(css)


def _fits(typ: str, key: str, value, spec: dict) -> bool:
    """この既定を、このページに当ててよいか（画像が要る変化形は画像があるときだけ、など）。"""
    has_image = bool(spec.get("image"))
    if typ in ("cover", "section"):
        layout = spec.get("layout", "split")
        if key == "layout":
            return value != "hero" or has_image
        return layout == "hero"                       # text_side・tone は hero のときだけ
    if typ == "photo":
        if key == "split":
            return spec.get("layout", "full") in ("left", "right")
        return True
    if typ == "quote":
        return (value == "plain") != has_image        # plain は画像なし、full・portrait は画像ありのときだけ
    if typ == "bullets":
        return has_image                              # side・split は脇に写真があるときだけ
    if typ == "gallery":
        return value != "collage" or len(spec.get("items") or []) == 3
    return True


def apply(style: dict, spec: dict, no: int, mode: str) -> tuple[dict, dict]:
    """台本の1枚にスタイルの既定を足す。(足したあとの spec, 足したもの)。台本に書いてあるキーは触らない。"""
    typ = spec.get("type")
    out, applied = dict(spec), {}
    vals = (style.get("defaults") or {}).get(typ) or {}
    # layout を先に決める（split・text_side・tone の当否は layout で決まる）
    for key in sorted(vals, key=lambda k: k != "layout"):
        if key in out:
            continue
        if _fits(typ, key, vals[key], out):
            out[key] = vals[key]
            applied[key] = vals[key]
    motion = style.get("motion") or {}
    if "fx" in motion and "fx" not in out:
        out["fx"] = applied["fx"] = motion["fx"]
    if "transition" in motion and "transition" not in out and no != 1 and mode != "handout":
        out["transition"] = applied["transition"] = motion["transition"]
    return out, applied
