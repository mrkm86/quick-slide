"""直しの種類（どう直すか）と、選ぶだけで決まる方針（docs/adr/0008-edit-kinds.md・docs/adr/0037-ask-ai-cards.md）。

修正の欄（AI に頼む）は「どう直しますか？」で種類を選び、種類ごとの選択肢を選ぶ。言葉を整える以外は書かなくても送れる。
**出す種類・選択肢はモードと機械（Codex の有無）でここが決め**、画面は渡されたものだけを並べる。画像を作れない機械でも
「作ってもらう」は消さずに、押せない選択肢（`disabled` と `reason`）として出す（作れることがあると分かるように）。
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

# 版の控え・記録に残す種類の名前（「図解（流れ）」。画面の札の言葉を替えても、控えの名前は変えない）
NAMES = {"text": "文字", "diagram": "図解", "illustration": "挿絵", "image": "画像"}
# 出どころの、控えに残す名前
_SOURCE_NAMES = (("generate", "生成する"), ("attached", "渡した画像を入れる"))
# 画面の札は 3 つ（docs/adr/0037-ask-ai-cards.md）。分け方は「押したあとにページがどれだけ変わるか」で、作り方（SVG で描く・
# 生成する）では分けない:
#   文字を整える（レイアウトはそのまま）／絵や写真を足す（挿絵と画像をまとめる。仕上がりは次の段で選ぶ）／見せ方を変える（図の型を選ぶ）
# 種類（kind）は今までどおり 4 つで、挿絵と画像が同じ札（card: visual）に入る
LABELS = {"text": "文字を整える", "diagram": "見せ方を変える", "illustration": "線のイラスト", "image": "写真・画像"}
CARDS = {"text": "text", "diagram": "layout", "illustration": "visual", "image": "visual"}
CARD_LABELS = {"text": "文字を整える", "layout": "見せ方を変える", "visual": "絵や写真を足す"}
CARD_BRIEFS = {"text": "レイアウトはそのまま", "layout": "流れ・比較など、型を選ぶ", "visual": "絵や写真を入れる"}
# 取り込んだページ（元の見た目を保つ）での一言
IMPORTED_CARD_BRIEFS = {"text": "レイアウトはそのまま", "layout": "流れ・比較など、型を選ぶ", "visual": "文字はそのまま、空いた所に"}
# 札の中で、次に選ぶものの見出し（絵や写真を足す → どんな絵にするか）
CARD_STEPS = {"visual": "どんな絵にしますか"}


def _card(kind: str, *, imported: bool = False) -> dict[str, Any]:
    card = CARDS[kind]
    return {"card": card, "card_label": CARD_LABELS[card], "card_brief": (IMPORTED_CARD_BRIEFS if imported else CARD_BRIEFS)[card],
            "card_step": CARD_STEPS.get(card, "")}


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
DIAGRAM_SHAPES = (("auto", "おまかせ"), ("flow", "流れ"), ("relation", "関係"), ("compare", "比較"), ("before_after", "前と後"),
                  ("numbers", "数字"))
# 画像の入口は「＋」（貼り付け・ドラッグ・URL の貼り付けも同じ）の 1 つ。「渡した画像を入れる」は、渡した画像をスライドに置く
# プレゼンで、絵を生成できる機械にだけ出す選択肢: 図を絵として描く（html-slide の「絵で描く図解」。図の中の文字も描く）
DIAGRAM_PICTURE = ("picture", "絵で描く")
# 図の形は数が多いので、札は「何を伝えたいか」の少数にとどめる。札に無い形は「その他」を選んで、欄に書いて伝える（文が要る）
DIAGRAM_OTHER = ("other", "その他（書いて伝える）")
DIAGRAM_OTHER_REQUIRED = "どんな見せ方にしたいかを、下の欄に書いてください（例: 前と後を左右に並べる、3 つの輪が重なる図）。"
DIAGRAM_OTHER_PLACEHOLDER = "どんな見せ方にしたいかを書く（例: 前と後を左右に並べる）"
IMAGE_SOURCES = (("generate", "写真・絵を生成"), ("attached", "手持ちの画像を使う"))
# 画像を作れない機械（Codex が無い・ログインしていない）で、「作ってもらう」に添える理由
GENERATE_UNAVAILABLE = "この機械では画像を生成できません（Codex が要ります）。「手持ちの画像を使う」を選んでください。"
# 押せない選択肢の札に、名前の下へ小さく出す一言
UNAVAILABLE_NOTE = "現在利用できません"
# 選択肢の行の見出し（種類を選ぶと、次に選ぶものとして出る）
OPTION_LABELS = {"touch": "整え方（いくつでも）", "shape": "どの型にしますか", "source": "画像は", "way": "入れ方"}
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
    "presentation": "このページを図解にする。自由なページ（free）で中身に合う形に組むか、形の決まっている図解の型に替え、言葉の中身は保つ",
    "pitch": "このページを図解として描き直す（図形・矢印・短いラベルが主役。文は減らす）",
    "motion": "この場面を図解にする（HTML・SVG の図で描き、拍ごとに図が組み上がるように見せる。言葉の中身は保つ）",
}
_DIAGRAM_SHAPE_WORDS = {
    "presentation": {
        "auto": "形は中身から選ぶ（「書く前に」の 9。まず free で組み、形の決まっているものは型）",
        "flow": "流れの図にする（free で箱を矢印でつなぐか、flow。進み具合が主役なら steps、時期が主役なら timeline）",
        "relation": "関係の図にする（hub・matrix・layers・equation のどれか。合う型が無ければ free で組む）",
        "compare": "比較の図にする（free で面の箱を左右に並べるか、観点が多ければ table。compare・pairs でもよい）",
        "numbers": "数字の図にする（free で数字を主役にしてマス・棒を添えるか、軸の要るグラフは bar-chart・line-chart）",
        "before_after": "前と後を見せる図にする（free で左に前・右に後を並べて矢印でつなぐか、compare。何が変わったかが分かるようにする）",
        "other": "図の形は、指示の文に書かれたとおりにする（合う型が無ければ free で組む）",
        "picture": "絵で描く図解にする（型 figure の image に prompt と labels。書き方は「絵で描く図解」の節）。"
                   "図の中に描く文字は labels に全部並べ、ページの言いたいことは見出しに置く",
    },
    "_": {
        "auto": "図の形は中身から選ぶ",
        "flow": "流れの図にする（順に並んだ段を矢印でつなぐ）",
        "relation": "関係の図にする（中心と周り・つながり・位置づけ）",
        "compare": "比較の図にする（2 つ以上を同じ観点で並べる）",
        "numbers": "数字の図にする（グラフ・大きな数字）",
        "before_after": "前と後を見せる図にする（左に前・右に後を並べ、矢印でつなぐ。何が変わったかが分かるようにする）",
        "other": "図の形は、指示の文に書かれたとおりにする",
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
                                 **_card(kind), "options": [], "many": False,
                                 "empty_reason": None}
        if kind == "text":
            entry.update(options=_options(TEXT_TOUCHES), many=True, empty_reason=TEXT_REQUIRED, options_label=OPTION_LABELS["touch"])
        elif kind == "diagram":
            entry.update(options=diagram_shapes(picture=mode == "presentation" and can_generate),
                         options_label=OPTION_LABELS["shape"])
        elif kind == "image":
            entry["options_label"] = OPTION_LABELS["source"]
            # `attachment_reason`: 添えた画像が要る出どころの、画像が無いときの文
            if not can_generate:
                entry["lead"] = IMAGE_LEAD_NO_GENERATE
            entry["options"] = image_sources(can_generate)
        out.append(entry)
    return out


def _options(pairs: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    return [{"value": v, "label": label} for v, label in pairs]


def diagram_shapes(*, picture: bool) -> list[dict[str, Any]]:
    """見せ方の型の選択肢。最後は「その他」（文が要る。`text_reason` は文が無いときの文、`placeholder` は欄の案内）。"""
    opts = _options(DIAGRAM_SHAPES + ((DIAGRAM_PICTURE,) if picture else ()) + (DIAGRAM_OTHER,))
    opts[-1].update(text_reason=DIAGRAM_OTHER_REQUIRED, placeholder=DIAGRAM_OTHER_PLACEHOLDER)
    return opts


def shape_words(mode: str, shape: str | None) -> str:
    """台本係に渡す、選んだ型の 1 行。"""
    shapes = _DIAGRAM_SHAPE_WORDS.get(mode, _DIAGRAM_SHAPE_WORDS["_"])
    return shapes.get(shape or "auto", shapes["auto"])


def image_sources(can_generate: bool) -> list[dict[str, Any]]:
    """画像の出どころ（作ってもらう・手持ちの画像を使う）。作れない機械では「作ってもらう」を押せない選択肢にする。"""
    out = []
    for o in _options(IMAGE_SOURCES):
        o["attachment_reason"] = ATTACHMENT_REQUIRED.get(o["value"])
        if o["value"] == "generate" and not can_generate:
            o.update(disabled=True, reason=GENERATE_UNAVAILABLE, note=UNAVAILABLE_NOTE)
        out.append(o)
    return out


def _first_enabled(options: list[dict[str, Any]]) -> str | None:
    return next((o["value"] for o in options if not o.get("disabled")), None)


IMPORTED_LEAD = "このページの空いている所に、挿絵を重ねます。元の文字・図形・画像はそのままです。"
IMPORTED_TEXT_LEAD = "文字だけを直します。位置・色・書式・図形はそのままです。"
IMPORTED_IMAGE_LEAD = "画像を生成するか、手持ちの画像を使うかと、入れ方を選びます。"
IMPORTED_IMAGE_LEAD_NO_GENERATE = "手持ちの画像を「＋」から渡して、入れ方を選びます。"
IMPORTED_DIAGRAM_LEAD = "選んだ型のページに組み直します。"
IMPORTED_DIAGRAM_ADD = "図を足す"
# 取り込んだページの画像・図解の選び方（docs/adr/0015）。差し替えるは、ページに画像があるときだけ画面が出す
IMPORTED_WAYS = (("add", "空いた所に足す"), ("replace", "差し替える"), ("main", "主役にして組み直す"))
# 元の見た目が型の見た目に変わる選び方（画像を「主役にして組み直す」・「見せ方を変える」の型）に、札の下へ小さく出す一言
# （送る前に確かめる窓は出さない。前の版に戻せる）
REBUILD_NOTE = "元の見た目から型の見た目に組み直します（元の文字は見出しや中身として載せ直します）。前の版にはいつでも戻せます。"
IMPORTED_ATTACHMENT_REQUIRED = "この機械では画像を生成できません。「＋」から画像を渡してください。"
IMPORTED_GENERATE_WITH_ATTACHMENT = "渡した画像があります。生成するときは、渡した画像を外してください。"


def offered_for_imported(mode: str, *, can_generate: bool = True) -> list[dict[str, Any]]:
    """取り込んだページ（PPTX から）の修正の欄に出す種類。文字（見た目を保って文字だけ。docs/adr/0014）・
    挿絵を重ねる（docs/adr/0013）・画像と図解（空いた所に足す・差し替える・主役にして組み直す。docs/adr/0015）。"""
    if mode != "presentation":
        return []

    def ways(*values: str, attach: bool = False) -> list[dict[str, Any]]:
        out = []
        for v, label in IMPORTED_WAYS:
            if v in values:
                o: dict[str, Any] = {"value": v, "label": label}
                if v == "main":
                    o["note"] = REBUILD_NOTE
                if v == "replace":
                    o["needs_picture"] = True
                if attach:
                    o["uses_attachment"] = True   # 渡した画像は、そのまま入れる画像になる（画面の札に「入れる」）
                    if v in ("add", "replace") and not can_generate:
                        o["attachment_reason"] = IMPORTED_ATTACHMENT_REQUIRED
                out.append(o)
        return out

    def entry(kind: str, lead: str, options: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        return {"kind": kind, "label": LABELS[kind], "icon": ICONS[kind], "lead": lead, **_card(kind, imported=True),
                "options": options, "many": False, "empty_reason": None, **extra}

    return [entry("text", IMPORTED_TEXT_LEAD, _options(TEXT_TOUCHES), many=True, empty_reason=TEXT_REQUIRED,
                  options_label=OPTION_LABELS["touch"]),
            # 見せ方を変える: 取り込んだページでも型を選び、その型のページに組み直す（元の見た目は型の見た目に変わる。
            # 絵で描く型は、取り込んだページでは出さない）
            # 空いた所に図を足す（差し込み。元のページはそのまま）は、「絵や写真を足す」の札の中で選ぶ（選択肢の `card`）
            entry("diagram", IMPORTED_DIAGRAM_LEAD,
                  [{"value": "add", "label": IMPORTED_DIAGRAM_ADD, "card": "visual"}, *diagram_shapes(picture=False)],
                  options_label=OPTION_LABELS["shape"], note=REBUILD_NOTE),
            entry("illustration", IMPORTED_LEAD, []),
            entry("image", IMPORTED_IMAGE_LEAD if can_generate else IMPORTED_IMAGE_LEAD_NO_GENERATE,
                  ways("add", "replace", "main", attach=True),
                  # 画像の出どころは、入れ方とは別に選ぶ（options の 2 つ目で送る。docs/adr/0037-ask-ai-cards.md）
                  sources=image_sources(can_generate), sources_label=OPTION_LABELS["source"],
                  options_label=OPTION_LABELS["way"])]


def parse(mode: str, raw_kind: Any, raw_options: Any, *, can_generate: bool, imported: bool = False
          ) -> tuple[str | None, list[str]]:
    """送られた種類と選択肢を確かめる。種類を選ばない（None）ならそのまま。誤りは ValueError（画面向けの文）。"""
    if raw_kind is None:
        if raw_options not in (None, []):
            raise ValueError("直すものを選んでから、選択肢を送ってください。")
        return None, []
    kinds = offered_for_imported(mode, can_generate=can_generate) if imported else offered(mode, can_generate=can_generate)
    entry = next((e for e in kinds if e["kind"] == raw_kind), None)
    if entry is None:
        raise ValueError("このスライドでは、その直し方は選べません。画面を読み込み直してください。")
    options = raw_options if raw_options is not None else []
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        raise ValueError("options は文字列の配列で送ってください。")
    refused = "その選択肢は選べません。画面を読み込み直してください。"
    if len(set(options)) != len(options):
        raise ValueError(refused)
    sources = entry.get("sources") or []
    source_values = [o["value"] for o in sources]
    picked_sources = [o for o in options if o in source_values]   # 出どころ（画像の 2 つ目の選択肢）は、選び方と分けて確かめる
    options = [o for o in options if o not in source_values]
    allowed = [o["value"] for o in entry["options"]]
    disabled = {o["value"]: o.get("reason") or refused for o in [*entry["options"], *sources] if o.get("disabled")}
    if any(o not in allowed for o in options) or len(picked_sources) > 1:
        raise ValueError(refused)
    for o in [*options, *picked_sources]:
        if o in disabled:
            raise ValueError(disabled[o])
    if not entry["many"]:
        if len(options) > 1:
            raise ValueError("選択肢は 1 つだけ選んでください。")
        if not options and allowed:
            first = _first_enabled(entry["options"])
            options = [first] if first is not None else []
    return raw_kind, options + picked_sources


def missing(kind: str | None, options: list[str], *, text: str, attachments: int, imported: bool = False,
            can_generate: bool = True) -> str | None:
    """送るのに足りないもの（画面向けの文）。足りていれば None。種類を選ばないときは、これまでどおり呼ぶ側が見る。"""
    if kind == "text" and not text and not options and not attachments:
        return TEXT_REQUIRED
    if kind == "diagram" and options and options[0] == "other" and not text.strip():   # 取り込んだページの「図を足す」は書かなくてよい
        return DIAGRAM_OTHER_REQUIRED
    if imported:   # 取り込んだページの画像: 生成できない機械では、足す・差し替えるに画像が要る
        if kind == "image" and options and options[0] in ("add", "replace") and not can_generate and not attachments:
            return IMPORTED_ATTACHMENT_REQUIRED
        # 出どころを選んで送ったとき（options の 2 つ目）: 手持ちの画像を使うなら画像が要る。作ってもらうなら、渡した画像は
        # そのまま入れる画像になってしまうので、外してもらう（取り込んだページでは、渡した画像を参考にして作ることはできない）
        source = options[1] if kind == "image" and len(options) > 1 else None
        if source == "attached" and not attachments:
            return ATTACHMENT_REQUIRED["attached"]
        if source == "generate" and attachments:
            return IMPORTED_GENERATE_WITH_ATTACHMENT
        return None
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
    if kind not in NAMES:
        return ""
    names = dict({"text": TEXT_TOUCHES, "diagram": DIAGRAM_SHAPES + (DIAGRAM_PICTURE, ("other", "その他")), "image": _SOURCE_NAMES}.get(kind, ()))
    picked = [names[o] for o in options if o in names and not (kind == "diagram" and o == "auto")]
    return NAMES[kind] + (f"（{'・'.join(picked)}）" if picked else "")
