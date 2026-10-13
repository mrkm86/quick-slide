"""直しの種類（何を直すか）と、選ぶだけで決まる方針（docs/adr/0008-edit-kinds.md）。

修正の欄は「何を直しますか？」で種類を選び、種類ごとの選択肢を選ぶ。文字以外は書かなくても送れる。
**出す種類・選択肢はモードと機械（Codex の有無）でここが決め**、画面は渡されたものだけを並べる（できないものは出さない）。
台本係には、種類と選択肢から作った文を「直すもの」の節で渡す（利用者が書いた文は「指示」の節のまま）。
"""

from __future__ import annotations

from typing import Any

KINDS = ("text", "diagram", "illustration", "image")

# モードで出す種類。プレゼンの挿絵は SVG で書き、サーバが PNG にして画像の枠に置く（docs/adr/0011）。モーションは画像を持たない
MODE_KINDS: dict[str, tuple[str, ...]] = {
    "presentation": ("text", "diagram", "illustration", "image"),
    "pitch": ("text", "diagram", "illustration", "image"),
    "motion": ("text", "diagram", "illustration"),
}

LABELS = {"text": "文字", "diagram": "図解", "illustration": "挿絵", "image": "画像"}
# 札のアイコン（画面の SS.icon の名前）
ICONS = {"text": "article", "diagram": "hub", "illustration": "brush", "image": "image"}
LEADS = {
    "text": "言い回し・見出し・本文を直します。",
    "diagram": "中身を図にします。何を図にするかは、ページの中身から決めます。",
    "illustration": "中身を表す、動かない絵を入れます。",
    "image": "生成した画像か、渡した画像を入れます。",
}
IMAGE_LEAD_NO_GENERATE = "渡した画像を入れます。"

# 種類ごとの選択肢。`many` はいくつでも、そうでなければ 1 つ（先頭が既定）
TEXT_TOUCHES = (("short", "短くする"), ("firm", "言い切る"), ("plain", "やさしく"), ("numbers", "数字を先に"))
DIAGRAM_SHAPES = (("auto", "おまかせ"), ("flow", "流れ"), ("relation", "関係"), ("compare", "比較"), ("numbers", "数字"))
# 画像の入口は「＋」（貼り付け・ドラッグ・URL の貼り付けも同じ）の 1 つ。「渡した画像を入れる」は、渡した画像をスライドに置く
IMAGE_SOURCES = (("generate", "生成する"), ("attached", "渡した画像を入れる"))
# 添えた画像が要る出どころ
NEEDS_ATTACHMENT = ("attached",)

TEXT_REQUIRED = "直したい言い回しを選ぶか、書いてください。"
ATTACHMENT_REQUIRED = {"attached": "入れる画像を渡してください（「＋」・貼り付け・ドラッグ。画像の URL を貼り付けても取り込みます）。"}

# ---- 台本係への文 -------------------------------------------------------------------------------------------------------
_TOUCH_WORDS = {
    "short": "文字を短くする（見出しは短く、本文は要点だけ）",
    "firm": "言い切る（「〜と考えられる」のような弱い言い方をやめる）",
    "plain": "やさしい言葉にする（専門用語は言い換えるか、短い説明を添える）",
    "numbers": "数字を先に出す（結論の数字を見出しか先頭に置く）",
}
_DIAGRAM_BASE = {
    "presentation": "このページを図解にする。中身に合う図解の型に替え、言葉の中身は保つ",
    "pitch": "このページを図解として描き直す（図形・矢印・短いラベルが主役。文は減らす）",
    "motion": "この場面を図解にする（HTML・SVG の図で描き、拍ごとに図が組み上がるように見せる。言葉の中身は保つ）",
}
_DIAGRAM_SHAPE_WORDS = {
    "presentation": {
        "auto": "型は中身から選ぶ（flow・steps・timeline・hub・matrix・equation・compare・pairs・table・bar-chart・line-chart・kpi など）",
        "flow": "流れの図にする（flow・steps・timeline のどれか）",
        "relation": "関係の図にする（hub・matrix・equation のどれか）",
        "compare": "比較の図にする（compare・pairs・table のどれか）",
        "numbers": "数字の図にする（bar-chart・line-chart・kpi・big-number のどれか）",
    },
    "_": {
        "auto": "図の形は中身から選ぶ",
        "flow": "流れの図にする（順に並んだ段を矢印でつなぐ）",
        "relation": "関係の図にする（中心と周り・つながり・位置づけ）",
        "compare": "比較の図にする（2 つ以上を同じ観点で並べる）",
        "numbers": "数字の図にする（グラフ・大きな数字）",
    },
}
_ILLUSTRATION = {
    "presentation": "このページに挿絵（中身を表す、動かない絵）を入れる。画像を置ける型・変化形にし、画像の枠に image: {\"svg\": …} で"
                    "挿絵を書く（書き方は illust/references/authoring.md。言葉の中身は保つ）",
    "pitch": "このページに挿絵（中身を表す、動かない絵）を入れて描き直す。写真ではなく絵にする",
    "motion": "この場面に挿絵を入れる（HTML・SVG の図形で描いた、動かない絵。画像ファイルは使わない。"
              "中身を表す人・物・場面を、単純な形とテーマの色で描く）",
}
_IMAGE = {
    "presentation": {
        "generate": "このページに画像を入れる。画像を置ける型・変化形にし、生成の枠に何を描くか（prompt）を書く",
        "attached": "添えた画像を、このページの画像として置く（台本の path に書く。画像を置ける型・変化形にする）",
    },
    "pitch": {
        "generate": "このページに印象的な画像を大きく入れて描き直す",
        "attached": "添えた画像を、このページに入れて描き直す",
    },
}


def offered(mode: str, *, can_generate: bool) -> list[dict[str, Any]]:
    """画面に出す種類と選択肢（`GET /api/decks/<id>` の `edit_kinds`）。並びはそのまま画面の並び。
    デッキを作ったときの「画像を使わない」は見ない（作るときだけの指定。docs/adr/0010-images-follow-the-machine.md）。"""
    out = []
    for kind in MODE_KINDS.get(mode, ()):
        # `empty_reason`: 選択肢も文も無いと送れない種類の、そのときの文（文字だけ。ほかは選ぶだけで送れる）
        entry: dict[str, Any] = {"kind": kind, "label": LABELS[kind], "icon": ICONS[kind], "lead": LEADS[kind],
                                 "options": [], "many": False,
                                 "empty_reason": None}
        if kind == "text":
            entry.update(options=_options(TEXT_TOUCHES), many=True, empty_reason=TEXT_REQUIRED)
        elif kind == "diagram":
            entry["options"] = _options(DIAGRAM_SHAPES)
        elif kind == "image":
            # `attachment_reason`: 添えた画像が要る出どころの、画像が無いときの文
            sources = [s for s in IMAGE_SOURCES if s[0] != "generate" or can_generate]
            if not can_generate:
                entry["lead"] = IMAGE_LEAD_NO_GENERATE
            entry["options"] = [{**o, "attachment_reason": ATTACHMENT_REQUIRED.get(o["value"])} for o in _options(tuple(sources))]
        out.append(entry)
    return out


def _options(pairs: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    return [{"value": v, "label": label} for v, label in pairs]


def parse(mode: str, raw_kind: Any, raw_options: Any, *, can_generate: bool) -> tuple[str | None, list[str]]:
    """送られた種類と選択肢を確かめる。種類を選ばない（None）ならそのまま。誤りは ValueError（画面向けの文）。"""
    if raw_kind is None:
        if raw_options not in (None, []):
            raise ValueError("直すものを選んでから、選択肢を送ってください。")
        return None, []
    entry = next((e for e in offered(mode, can_generate=can_generate) if e["kind"] == raw_kind), None)
    if entry is None:
        raise ValueError("このスライドでは、その直し方は選べません。画面を読み込み直してください。")
    options = raw_options if raw_options is not None else []
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        raise ValueError("options は文字列の配列で送ってください。")
    allowed = [o["value"] for o in entry["options"]]
    if any(o not in allowed for o in options) or len(set(options)) != len(options):
        raise ValueError("その選択肢は選べません。画面を読み込み直してください。")
    if not entry["many"]:
        if len(options) > 1:
            raise ValueError("選択肢は 1 つだけ選んでください。")
        if not options and allowed:
            options = [allowed[0]]
    return raw_kind, options


def missing(kind: str | None, options: list[str], *, text: str, attachments: int) -> str | None:
    """送るのに足りないもの（画面向けの文）。足りていれば None。種類を選ばないときは、これまでどおり呼ぶ側が見る。"""
    if kind == "text" and not text and not options and not attachments:
        return TEXT_REQUIRED
    if kind == "image" and options and options[0] in NEEDS_ATTACHMENT and not attachments:
        return ATTACHMENT_REQUIRED[options[0]]
    return None


def direction(mode: str, kind: str | None, options: list[str], *, scope: str = "page") -> list[str]:
    """台本係に渡す「直すもの」の行。種類を選ばなければ空。"""
    if kind is None:
        return []
    lines: list[str] = []
    if kind == "text":
        lines.append("文字（言い回し・見出し・本文）を直す。図や絵の組み立ては変えない")
        lines += [_TOUCH_WORDS[o] for o in options]
    elif kind == "diagram":
        lines.append(_DIAGRAM_BASE[mode])
        shapes = _DIAGRAM_SHAPE_WORDS.get(mode, _DIAGRAM_SHAPE_WORDS["_"])
        lines.append(shapes[options[0] if options else "auto"])
    elif kind == "illustration":
        lines.append(_ILLUSTRATION[mode])
    elif kind == "image":
        source = options[0] if options else "generate"
        lines.append(_IMAGE[mode]["generate" if source == "generate" else "attached"])
    if scope == "deck" and kind in ("diagram", "illustration", "image"):
        lines.insert(0, "スライド全体への直し: 当てはまると伝わりやすくなるページだけを選んで直す（全部のページを同じにしない）")
    return lines


def summary(kind: str | None, options: list[str]) -> str:
    """選んだ方針の短い名前（「図解（流れ）」）。台本の版の控えと記録に使う。種類を選ばなければ空。"""
    if kind not in LABELS:
        return ""
    names = dict({"text": TEXT_TOUCHES, "diagram": DIAGRAM_SHAPES, "image": IMAGE_SOURCES}.get(kind, ()))
    picked = [names[o] for o in options if o in names and not (kind == "diagram" and o == "auto")]
    return LABELS[kind] + (f"（{'・'.join(picked)}）" if picked else "")
