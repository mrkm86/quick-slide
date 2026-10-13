"""画面に出す語彙。**状態名とデータ上の状態の対応はこのファイル 1 か所に集める**。

画面（`web/app/`）は状態名を自分では作らず、API が付ける `display` の `key` / `label` / `motion` と、
`GET /api/vocabulary` の語をそのまま使う。v1 の画面が読むキーは全部そろえる（値の一部は P3 以降で使い始める）。

ページの状態（`page_states`）の名前は v1 と同じ。描いた回のページの結果は `run_page_display` が寄せる。
台本係・描き係がいま何をしているかの 1 文（企画の帯）は `planning_message` が作る。
スライドの形式（モード）の名前・説明・アイコン・色・書き出せる形式は `DECK_KINDS` 1 か所に持つ（`deck_kinds()` が配る）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..enums import DECK_MODES, DECK_STATUSES, EXPORT_FORMATS
from ..store.intakes import MAX_REQUEST_CHARS, REQUEST_REQUIRED_REASON, REQUEST_TOO_LONG_REASON
from .later import NOT_READY_REASON

# key → (表示名, 動きのある表現か)
PAGE_STATE_LABELS: dict[str, tuple[str, bool]] = {
    "queued_slot": ("待機中", False),
    "queued_rate_limit": ("待機中（制限のため再試行待ち）", False),
    "retry_backoff": ("再試行待ち", False),
    "paused": ("一時停止中", False),
    "generating": ("生成中", True),
    "adjusting": ("画像を調整中", True),
    # 直しを頼んだページ（描いてあるページを直している・順番を待っている）。絵の上にも札で出す
    "editing": ("編集中", True),
    "edit_queued": ("編集待ち", False),
    "ready": ("完成", False),
    "failed": ("失敗", False),
    "cancelled": ("中止", False),
    "unstarted": ("未生成", False),
    "unknown": ("状態を取得できません", False),
}

# 件数と上部の帯で束ねる 4 区分。key → 区分
PAGE_STATE_GROUPS: dict[str, str] = {
    "ready": "ready",
    "generating": "generating",
    "adjusting": "generating",
    "editing": "generating",
    "edit_queued": "waiting",
    "queued_slot": "waiting",
    "queued_rate_limit": "waiting",
    "retry_backoff": "waiting",
    "paused": "waiting",
    "unstarted": "waiting",
    "failed": "failed",
    "cancelled": "failed",
    "unknown": "failed",
}
GROUP_LABELS: dict[str, str] = {"ready": "完成", "generating": "生成中", "waiting": "待機", "failed": "要対応"}

DECK_STATUS_LABELS: dict[str, str] = {"draft": "下書き", "review": "レビュー中", "final": "確定"}
# スライドの形式＝モード（作るときに決め、あとから替えない）。プレゼン＝html-slide・ピッチ＝image-slide・モーション＝motion-slide。
# **並び順もここが正本**（画面の切り替え・一覧の絞り込みの順）。`icon` は Material Symbols の名前、`tone` は画面の色の組の名前
# （CSS は tone ごとの組を 1 か所に持つ）。書き出せる形式（`outputs`）は手で書かず、`deck_kinds()` が書き出しの可否から作る
DECK_KINDS: tuple[dict[str, str], ...] = (
    {"mode": "presentation", "label": "プレゼン", "description": "PowerPoint 互換のプレゼンスライドを作ります",
     "icon": "slideshow", "tone": "blue"},
    {"mode": "pitch", "label": "ピッチ", "description": "インパクトのある画像のスライドを作ります",
     "icon": "photo_library", "tone": "orange"},
    {"mode": "motion", "label": "モーション", "description": "従来のプレゼンにとらわれない、自由なアニメーションのスライドを作ります",
     "icon": "animation", "tone": "green"},
)
DECK_MODE_LABELS: dict[str, str] = {k["mode"]: k["label"] for k in DECK_KINDS}
# 形式のカードに小さく出す、書き出せる形式の短い名前（並びもこの順）
OUTPUT_LABELS: dict[str, str] = {"pptx": "PPTX", "pdf": "PDF", "html": "HTML", "images": "画像"}

# 画面の入力欄から 1 冊作る（`POST /api/intakes` に `request`。docs/adr/0002-create-from-screen.md）
CREATE_WORDS: dict[str, str] = {
    "heading": "どんなスライドを作りますか？",
    "placeholder": "例: 最新の AI 開発の状況を 5 ページのレポートにまとめて",
    "submit": "作成",
    "working": "作り始めています…",
    "empty_reason": REQUEST_REQUIRED_REASON,
    "too_long_reason": REQUEST_TOO_LONG_REASON,
}

EDIT_MODE_LABELS: dict[str, str] = {"minor": "微修正", "remake": "作り直し"}

# 版（描いた回のページ）の出どころ
SOURCE_KIND_LABELS: dict[str, str] = {"generated": "生成"}

# 並べ替えの操作中に画面が出す語。`moved` の `{n}` だけは画面が数を入れる
REORDER_MESSAGES: dict[str, str] = {
    "grabbed": "掴んでいます。",
    "drop_here": "ここに入ります。",
    "saving": "並びを保存しています。",
    "saved": "並びを保存しました。",
    "moved": "ページを {n} 番目に移しました。",
    "reverted": "並びを元に戻しました。",
    "cancelled": "並べ替えをやめました。",
}

# カード 1 枚の中の見出し語（画面の固定ラベル）
CARD_WORDS: dict[str, str] = {
    "record_label": "このページの記録",
}

# カード 1 枚に出す枚数の言い方（「11枚」）。**数を入れるのはサーバ**（`deck_routes.card_public`）
CARD_PAGE_COUNT = "{n}枚"

# 版の見せ方の語。画面に出す語は「使用中」
VERSION_WORDS: dict[str, str] = {
    "stack_label": "ページの版",
    "active": "使用中",
    "newest": "新しい",
    "image_missing": "絵を取り出せません",
    "show_older": "古い版をすべて見る",
    "hide_older": "古い版を隠す",
    "compare_label": "比較ビュー",
    "compare_hide": "比較を非表示",
    "compare_pick": "見比べる版",
}

# 修正指示の欄の上限（画面が送る前に自分で数える。数は画面に書かない）
MAX_INSTRUCTION_TEXT = 4000
MAX_ATTACHMENTS = 8
# 修正指示の欄の断りの文（送信の口の断りと同じ 1 文にする。口は P3 で作る）
TEXT_REQUIRED_REASON = "指示の文を書くか、参考の画像を添えてから送ってください。"
TEXT_TOO_LONG_REASON = f"指示の文が長すぎます。{MAX_INSTRUCTION_TEXT}文字以内にしてから送ってください。"
TOO_MANY_ATTACHMENTS_REASON = f"参考の画像は{MAX_ATTACHMENTS}枚までです。減らしてから送ってください。"
MINOR_NEEDS_A_PICTURE_REASON = (f"絵ができていないページは「{EDIT_MODE_LABELS['minor']}」では直せません。"
                                f"「{EDIT_MODE_LABELS['remake']}」を選んでから送ってください。")

# 修正指示の欄の固定ラベル。`{n}` だけは画面が数を入れる
INSTRUCTION_WORDS: dict[str, str] = {
    "remake_confirm": "{n}ページを作り直します。よろしいですか？",
    "minor_confirm": "{n}ページを直します。よろしいですか？",
    "generate_label": "画像づくりを始める",
    "attach_hint": "画像は「＋」、ドラッグ&ドロップ、貼り付け（⌘V）で渡せます。画像の URL を貼り付けても取り込みます",
    "section_label": "修正指示",
    "page_placeholder": "このページを直したいことを書いてください（例: 図を大きく、文字を減らして）",
    "deck_placeholder": "スライド全体に効かせたいことを書いてください（例: 口調を固くして）",
    "attach_label": "画像を選ぶ",
    "attachment_label": "参考の画像",
    "attachment_remove": "外す",
    "attachment_uploading": "画像を確認しています…",
    "remake_description": "今の絵を参考にしません。いくら指示しても変わらないときに使います。",
    "remake_without_source": "元の画像を使わずに生成し直す",
    # 選んだ数ページに 1 つの指示（P3）。`{pages}` は画面が番号を入れる（「7・8」）
    "select_label": "このページを選ぶ",
    "pages_target": "選んだページ（{pages}）",
    "pages_placeholder": "選んだページに効かせたいことを書いてください（例: 7・8枚目の見出しを短く、数字を大きく）",
    "selection_clear": "選ぶのをやめる",
}

# 書き出しで選べる形式の表示名
EXPORT_FORMAT_LABELS: dict[str, str] = {"pdf": "PDF", "pptx": "PPTX", "html": "HTML（1枚）"}

# 書き出しの待機・完了で出す固定文
EXPORT_WORDS: dict[str, str] = {
    "building": "書き出しています…",
    "success": "ダウンロードしました",
}

# 切り替えた版が、書き出しの部品を残す前の回で描いたもの（ページの `export_note`）。画面と発表はその版の絵で出し、
# 書き出し（PPTX・PDF）だけ、そのページをいまの作りで描き直す（docs/adr/0001-version-pins.md）
PIN_EXPORT_REDRAW_NOTE = "書き出しでは、今の作りで描き直します"

# 隙間にページを足すときの語。ボタンの語だけは `capabilities.insert_page.label` から来る
INSERT_TEXT_REQUIRED_REASON = "どんなページが欲しいかを書いてください。画像だけでは足せません。"
INSERT_WORDS: dict[str, str] = {
    "placeholder": "ここにどんなページが欲しいか、一言で。例: この後に費用の内訳が欲しい",
    "submit": "送信",
    "cancel": "キャンセル",
    "waiting": "どんなページにするかを考えています…",
    "failed": "ページを足せませんでした。書き直してやり直してください。",
    "landed": "頼んだページができました。書きかけを送ってから再読み込みすると、画面に出ます。",
    "section_label": "足すページの指示",
    "attach_hint": "画像はドラッグ&ドロップ、貼り付け（⌘V）でも添えられます。添えた画像はこのページの絵を描くときの参考になります。",
    "empty_reason": INSERT_TEXT_REQUIRED_REASON,
}

# ごみ箱からの完全削除。確認の窓は出さず、ボタンがその場で 2 段になる
PURGE_WORDS: dict[str, str] = {
    "confirm": "本当に削除する",
    "cancel": "やめる",
    "working": "削除しています…",
    "done": "完全に削除しました。元に戻せません。",
    "notice": "元に戻せます。完全に削除すると戻せません。",
    "empty": "ごみ箱は空です。",
    "confirm_all": "{n} 件すべて完全に削除する",
    "done_all": "ごみ箱を空にしました（{n} 件・{size}）。元に戻せません。",
    "kept_back": "{n} 件は今は削除できないので残しました。",
    "reclaimable": "空けられる容量 {size}",
    "reclaimable_none": "空けられる容量はありません",
}

PURGE_ALL_LABEL = "ごみ箱を空にする"

# 容量の単位。**1000 で割る**（1024 ではない）。利用者が見比べる先は Finder で、
# Finder は 1 MB = 1,000,000 バイトで数える
SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")


def size_label(n: int) -> str:
    """バイト数を読める 1 語にする。`B` と `KB` は整数、`MB` 以上は小数 1 桁。"""
    if n < 0:
        raise ValueError("容量は負にならない")
    value = float(n)
    unit = 0
    while value >= 999.5 and unit < len(SIZE_UNITS) - 1:
        value /= 1000
        unit += 1
    if unit <= 1:
        return f"{round(value)} {SIZE_UNITS[unit]}"
    return f"{value:.1f} {SIZE_UNITS[unit]}"


def reclaimable_label(n: int) -> str:
    """「空けられる容量 412.3 MB」。0 のときは容量を出さずに**無いと言う**。"""
    if n <= 0:
        return PURGE_WORDS["reclaimable_none"]
    return PURGE_WORDS["reclaimable"].format(size=size_label(n))


def purge_all_confirm_label(count: int) -> str:
    return PURGE_WORDS["confirm_all"].format(n=count)


def purge_all_done_message(count: int, freed: int, kept_back: int) -> str:
    """空にし終わった 1 行。残したものがあるときだけ、そのことを足す。"""
    text = PURGE_WORDS["done_all"].format(n=count, size=size_label(freed))
    if kept_back:
        text += PURGE_WORDS["kept_back"].format(n=kept_back)
    return text


def page_display(key: str, *, reason: str | None = None) -> dict[str, Any]:
    label, motion = PAGE_STATE_LABELS[key]
    out: dict[str, Any] = {"key": key, "label": label, "motion": motion, "group": PAGE_STATE_GROUPS[key]}
    if reason:
        out["reason"] = reason
    return out


def run_page_display(page_state: str | None, run_state: str, *, has_image: bool) -> dict[str, Any]:
    """描いた回のページの状態 → 画面の語（v1 と同じ名前）。

    - 絵がある → 完成
    - 回がまだ動いている: 絵が返った（drawn）→ 画像を調整中 / まだ → 生成中
    - 回が終わったのに絵が無い → 失敗（理由は回の文が帯に出る）
    """
    if page_state == "ready" and has_image:
        return page_display("ready")
    if page_state == "ready":
        return {"key": "unknown", "label": "画像を取得できません", "motion": False, "group": "failed"}
    if run_state in ("queued", "writing", "checking", "rendering"):
        if page_state == "failed":
            return page_display("failed")
        return page_display("adjusting" if page_state == "drawn" else "generating")
    return page_display("failed")


# 台本係・描き係がいま何をしているか（スライドの画面の「企画の帯」）。**あと何秒かは言わない**
PLANNING_MESSAGES: dict[str, str] = {
    "queued": "順番を待っています。前の仕事が終わったら始めます。",
    "researching": "下調べをしています。",
    "writing": "台本を書いています。",
    "rewriting": "台本を直しています（{round}回目）。",
    "checking": "台本を確かめています。",
    "starting": "描く準備をしています。",
    "pages": "ページを描いています（{done}/{total}）。",
    "finishing": "仕上げています（PPTX・PDF・ページの画像）。",
    "images": "画像を描いています（{done}/{total}）。",
    "compose": "スライドを組んでいます。",
    "motion": "全部の拍を開いて確かめながら組んでいます（拍の数に応じて数分かかります）。",
}


def planning_message(run_state: str, progress: dict[str, Any]) -> str | None:
    if run_state == "queued":
        return PLANNING_MESSAGES["queued"]
    if run_state == "writing" and progress.get("phase") == "researching":
        return PLANNING_MESSAGES["researching"]
    if run_state == "writing":
        rnd = int(progress.get("round") or 1)
        return PLANNING_MESSAGES["writing"] if rnd <= 1 else PLANNING_MESSAGES["rewriting"].format(round=rnd)
    if run_state == "checking":
        return PLANNING_MESSAGES["checking"]
    if run_state == "rendering":
        step = progress.get("step") or "starting"
        text = PLANNING_MESSAGES.get(step, PLANNING_MESSAGES["starting"])
        return text.format(done=int(progress.get("done") or 0), total=int(progress.get("total") or 0))
    return None


def progress_headline(total: int, ready: int, generating: int) -> str:
    """進みの帯の 1 行目。**文は画面ではなくここが作る**。あと何秒かは言わない。"""
    if total == 0:
        return "ページなし"
    done = f"{total} ページ中 {ready} ページが{GROUP_LABELS['ready']}"
    return f"画像を生成しています。{done}" if generating else done


def page_count_label(page_count: int) -> str:
    """カードに出す枚数（`CARD_PAGE_COUNT` に数を入れるのはここだけ）。"""
    return CARD_PAGE_COUNT.replace("{n}", str(page_count))


def deck_status_label(status: str) -> str:
    return DECK_STATUS_LABELS.get(status, "状態を取得できません")


def deck_mode_label(mode: str) -> str:
    return DECK_MODE_LABELS.get(mode, "状態を取得できません")


def deck_kinds(refusals: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """スライドの形式の一覧（`DECK_KINDS` の順）。`outputs` はそのモードで書き出せる形式（`export_routes.format_refusal`）。
    `available`・`reason` は「いまこの機械で作り始められるか」とその理由（`refusals` は `QuickSlideServer.kind_refusals`。
    ピッチは Codex が要る）。画面は使えない札を押せなくし、理由をそのまま出す"""
    from .export_routes import format_refusal  # 遅延 import（循環回避）

    assert set(OUTPUT_LABELS) == set(EXPORT_FORMATS), "書き出しの形式が増えたら OUTPUT_LABELS にも足す"
    assert sorted(k["mode"] for k in DECK_KINDS) == sorted(DECK_MODES), "モードが増えたら DECK_KINDS にも足す"
    refusals = refusals or {}
    return [{**kind, "outputs": [{"format": f, "label": label} for f, label in OUTPUT_LABELS.items()
                                 if format_refusal(kind["mode"], f) is None],
             "available": kind["mode"] not in refusals, "reason": refusals.get(kind["mode"], "")}
            for kind in DECK_KINDS]


def create_limits() -> dict[str, int]:
    """入力欄が送る前に自分で数える上限（すべて整数）。"""
    return {"request": MAX_REQUEST_CHARS}


def instruction_words() -> dict[str, str]:
    """修正指示の欄に要る**語**（すべて文字列）。上限（数）は `instruction_limits()` が別の入れ物で持つ。"""
    return {**INSTRUCTION_WORDS,
            "empty_reason": TEXT_REQUIRED_REASON,
            "remake_forced_reason": MINOR_NEEDS_A_PICTURE_REASON,
            "text_too_long_reason": TEXT_TOO_LONG_REASON,
            "too_many_attachments_reason": TOO_MANY_ATTACHMENTS_REASON}


def instruction_limits() -> dict[str, int]:
    """修正指示の欄が送る前に自分で数える上限（すべて整数）。"""
    return {"text": MAX_INSTRUCTION_TEXT, "attachments": MAX_ATTACHMENTS}


def card_limits() -> dict[str, int]:
    """カードの操作が送る前に自分で抑える数（いまはタイトルの上限だけ）。"""
    from .deck_routes import TITLE_MAX_LENGTH  # 遅延 import（循環回避）

    return {"title": TITLE_MAX_LENGTH}


def vocabulary(kind_refusals: Mapping[str, str] | None = None) -> dict[str, Any]:
    """`GET /api/vocabulary`。画面が絞り込みの選択肢などに使う。`kind_refusals` は形式ごとの使えない理由（`deck_kinds`）。"""
    return {
        "page_states": {k: {"label": v[0], "motion": v[1], "group": PAGE_STATE_GROUPS[k]} for k, v in PAGE_STATE_LABELS.items()},
        "groups": GROUP_LABELS,
        "deck_statuses": {s: DECK_STATUS_LABELS[s] for s in DECK_STATUSES},
        "deck_modes": {m: DECK_MODE_LABELS[m] for m in DECK_MODES},
        "deck_kinds": deck_kinds(kind_refusals),
        "create": dict(CREATE_WORDS),
        "create_limits": create_limits(),
        "edit_modes": EDIT_MODE_LABELS,
        "source_kinds": SOURCE_KIND_LABELS,
        "reorder": REORDER_MESSAGES,
        "card": CARD_WORDS,
        "card_limits": card_limits(),
        "versions": VERSION_WORDS,
        "instruction": instruction_words(),
        "instruction_limits": instruction_limits(),
        "export_formats": EXPORT_FORMAT_LABELS,
        "export": EXPORT_WORDS,
        "purge": PURGE_WORDS,
        "insert": dict(INSERT_WORDS),
        "not_ready": NOT_READY_REASON,
        "source": "src/quick_slide/server/vocabulary.py",
    }
