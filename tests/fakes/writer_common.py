"""偽物の台本係（claude 風・codex 風）が共有する部品。本物の LLM は呼ばない。

振る舞いは環境変数で変える:

    FAKE_WRITER_LOG     呼ばれるたびに 1 つ JSON を書くフォルダ（必須）
    FAKE_WRITER_MODE    ok（既定）/ invalid_first（1 回目はエンジンの検査で止まる台本・2 回目から正しい）/
                        no_ids_first（1 回目はページの id が無い台本）/
                        always_invalid / garbage_first（1 回目は JSON ではない文）/ auth（ログインしていない）/
                        rate（利用枠に達した）/ slow（FAKE_WRITER_SLEEP 秒待ってから答える）
    FAKE_WRITER_IMAGES  0 にすると画像の枠を置かない
    FAKE_WRITER_SLEEP   slow の秒数（既定 2）
    FAKE_WRITER_PAGES   10 にすると 10 ページの台本（直す試験用。どのページも talk を 1 つ持つ）

直す（P3）の指示文も読む: 「直すページ」の節があれば直しの答え、「ページを足します」なら差し込みの答え、
「合意した「構成」」なら構成の見出しの数だけページを書く。直しの振り分けは指示の語で決める
（「絵だけ」→ image・「両方」→ both・「そのまま」→ keep・ほかは script。「分けて」で 2 ページに分ける。
「絵も変えて」で画像の枠の prompt も変える）。FAKE_WRITER_MODE=bad_revision_first なら 1 回目は対象でないページを返す。

**API キーの環境変数が 1 つでも届いていたら、答えずに失敗する**（サブスク枠だけで動かす約束の試験）。
"""
import json
import os
import pathlib
import re
import time
import uuid

API_KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY")
LOG = pathlib.Path(os.environ["FAKE_WRITER_LOG"])
MODE = os.environ.get("FAKE_WRITER_MODE", "ok")


def record(**kw) -> None:
    LOG.mkdir(parents=True, exist_ok=True)
    (LOG / f"{time.time():.6f}-{os.getpid()}-{uuid.uuid4().hex[:6]}.json").write_text(
        json.dumps(kw, ensure_ascii=False), encoding="utf-8")


def calls(kind: str) -> int:
    n = 0
    for p in LOG.glob("*.json") if LOG.is_dir() else []:
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("kind") == kind:
                n += 1
        except ValueError:
            pass
    return n


def leaked_keys() -> list[str]:
    return [k for k in API_KEY_VARS if k in os.environ]


def valid_script() -> dict:
    images = os.environ.get("FAKE_WRITER_IMAGES", "1") != "0"
    cover = {"id": "cover", "type": "cover", "title": "会議を**半分**にする", "subtitle": "3か月の試行の提案（架空）"}
    if images:
        cover["image"] = {"prompt": "朝の光が入る明るい会議室。木の机と椅子。左の半分は無地の壁で何も置かない"}
    return {
        "title": "試験の資料（架空）",
        "slides": [
            cover,
            {"id": "p-why", "type": "statement", "headline": "会議の4割は何も決めずに終わっている",
             "body": "回答240人が先月出た会議のうち、4割で結論が出なかった（架空）"},
            {"id": "p-rules", "type": "cards", "headline": "3つのルールで短くする",
             "items": [{"title": "25分を基本にする"}, {"title": "議題は前日までに出す"}, {"title": "決めたらその場で閉じる"}]},
            {"id": "closing", "type": "closing", "headline": "まず2チームで試します", "items": [{"title": "来月から2チームで試す"}], "contact": "経営企画（架空）"},
        ],
    }


def ten_page_script() -> dict:
    """10 ページ（画像の枠は 1・5・7・8 枚目）。どのページもクリックの無い型なので talk は 1 つ。"""
    def talk(n):
        return [f"{n}枚目の話（架空）"]
    pages = [
        {"id": "cover", "type": "cover", "title": "会議を半分にする（架空）", "subtitle": "3か月の試行の提案",
         "image": {"prompt": "朝の明るい会議室。木の机。左の半分は無地の壁で何も置かない"}, "talk": talk(1)},
        {"id": "p-why", "type": "statement", "headline": "会議の4割は何も決めずに終わっている", "talk": talk(2)},
        {"id": "p-sec", "type": "section", "title": "なぜ長くなるのか", "talk": talk(3)},
        {"id": "p-agenda", "type": "statement", "headline": "議題が前日までに出ていない", "talk": talk(4)},
        {"id": "p-desk", "type": "photo", "headline": "資料を読む時間が会議の中にある",
         "image": {"prompt": "机の上に紙の資料が積まれている。人は写さない"}, "talk": talk(5)},
        {"id": "p-rule", "type": "statement", "headline": "25分を基本にする", "talk": talk(6)},
        {"id": "p-seven", "type": "photo", "headline": "立ったまま話すと短くなる",
         "image": {"prompt": "窓の近くで立って話す小さなチーム。顔は写さない"}, "talk": talk(7)},
        {"id": "p-eight", "type": "photo", "headline": "決めたらその場で閉じる",
         "image": {"prompt": "ホワイトボードに丸を付ける手元"}, "talk": talk(8)},
        {"id": "p-nine", "type": "statement", "headline": "来月から2チームで試す", "talk": talk(9)},
        {"id": "closing", "type": "closing", "headline": "まず2チームで試します", "items": [{"title": "来月から2チームで試す"}],
         "contact": "経営企画（架空）",
         "talk": talk(10)},
    ]
    return {"title": "会議を半分にする（架空）", "slides": pages}


def _between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find(end, i + len(start))
    return text[i + len(start):j if j >= 0 else None]


def _current_script(prompt: str) -> dict:
    block = _between(prompt[prompt.find("# いまの台本"):], "```json\n", "\n```")
    return json.loads(block)


def revision_answer(prompt: str, n_before: int) -> dict:
    script = _current_script(prompt)
    targets = [t.strip() for t in _between(prompt, "対象の id: ", "\n").split(",") if t.strip()]
    note = _between(prompt, "<<<指示ここから\n", "\n指示ここまで>>>")
    by_id = {p["id"]: p for p in script["slides"]}
    route = "image" if "絵だけ" in note else "both" if "両方" in note else "keep" if "そのまま" in note else "script"
    pages = []
    for t in targets:
        if MODE == "bad_revision_first" and n_before == 0:
            other = next(p["id"] for p in script["slides"] if p["id"] not in targets)
            pages.append({"id": other, "route": "script", "slides": [by_id[other]]})
            continue
        item = {"id": t, "route": route}
        if route in ("script", "both"):
            page = dict(by_id[t])
            for k in ("headline", "title"):
                if isinstance(page.get(k), str):
                    page[k] = page[k] + "（直し）"
                    break
            if "絵も変えて" in note and isinstance(page.get("image"), dict):
                page["image"] = {**page["image"], "prompt": page["image"]["prompt"] + "。夕方の光"}
            slides = [page]
            if "分けて" in note:
                slides.append({"id": t + "-b", "type": "statement", "headline": "分けた後半（架空）",
                               "talk": ["分けた後半の話（架空）"]})
            item["slides"] = slides
        if route in ("image", "both"):
            item["image"] = {"note": note or "添えた画像に合わせる"}
        pages.append(item)
    first = [p["id"] for p in script["slides"]].index(targets[0]) if targets else 0
    suggestions = ([{"id": script["slides"][first - 1]["id"], "text": "前のページの見出しも短くするとよい（架空）"}]
                   if first > 0 and script["slides"][first - 1]["id"] not in targets else [])
    return {"pages": pages, "suggestions": suggestions, "summary": "指定のページを直した（架空）"}


def insert_answer(prompt: str) -> dict:
    script = _current_script(prompt)
    taken = {p["id"] for p in script["slides"]}
    n = 1
    while f"p-added-{n}" in taken:
        n += 1
    return {"slides": [{"id": f"p-added-{n}", "type": "statement", "headline": f"足したページ{n}（架空）",
                        "talk": [f"足したページ{n}の話（架空）"]}], "summary": "1 ページ足した（架空）"}


def outline_script(prompt: str, n_before: int) -> dict:
    manuscript = _between(prompt, "<<<原稿ここから\n", "原稿ここまで>>>")
    heads = re.findall(r"^##\s*\d+\.\s*(.+)$", manuscript, re.M)
    if MODE == "outline_wrong_first" and n_before == 0:
        heads = heads[:-1]
    pages = [{"id": f"p-{i}", "type": "statement", "headline": h.strip(), "talk": [f"{i}枚目の話（架空）"]}
             for i, h in enumerate(heads, 1)]
    return {"title": "構成どおりの資料（架空）", "slides": pages}


def invalid_script() -> dict:
    return {"title": "試験の資料（架空）", "slides": [{"id": "cover", "type": "no-such-type", "headline": "知らない型"}]}


def prompt_kind(prompt: str) -> str:
    if "# 直すページ" in prompt:
        return "revise"
    if "ページを足します" in prompt:
        return "insert"
    return "make"


def answer_for(kind: str, prompt: str) -> tuple[str | None, str | None]:
    """(答えの文, 失敗の種類)。"""
    n = calls(kind)          # これまでの回数（この呼び出しを含まない）
    what = prompt_kind(prompt)
    n_what = sum(1 for p in (LOG.glob("*.json") if LOG.is_dir() else [])
                 if json.loads(p.read_text(encoding="utf-8")).get("what") == what)
    record(kind=kind, what=what, keys=leaked_keys(), cwd=os.getcwd(), mode=MODE,
           targets=_between(prompt, "対象の id: ", "\n").strip() if what == "revise" else None,
           instruction=_between(prompt, "<<<指示ここから\n", "\n指示ここまで>>>") if what != "make" else None,
           outline="合意した「構成」" in prompt,
           has_contract="# 台本の決まり" in prompt and "deck-format.md" in prompt,
           has_pitch_contract="image-slide/references/intake.md" in prompt,
           has_manuscript="<<<原稿ここから" in prompt, retry="# 前の台本は検査で止まりました" in prompt,
           ng=[ln for ln in prompt.splitlines() if ln.startswith("- NG")], chars=len(prompt),
           # 設定のスタイルから来た書き方の注文（P4）
           style_lines=[ln for ln in prompt.splitlines()
                        if ln.startswith(("- 使ってよい型", "- 書き方の方針", "- 文章の調子", "- 作風の一言", "- スタイル: "))])
    if leaked_keys():
        return None, "keys"
    if MODE == "auth":
        return None, "auth"
    if MODE == "rate":
        return None, "rate"
    if MODE == "slow":
        time.sleep(float(os.environ.get("FAKE_WRITER_SLEEP", "2")))
    if what == "revise":
        return json.dumps(revision_answer(prompt, n_what), ensure_ascii=False), None
    if what == "insert":
        return json.dumps(insert_answer(prompt), ensure_ascii=False), None
    if "合意した「構成」" in prompt:
        return json.dumps(outline_script(prompt, n_what), ensure_ascii=False), None
    if MODE == "always_invalid" or (MODE == "invalid_first" and n == 0):
        return json.dumps(invalid_script(), ensure_ascii=False), None
    if MODE == "no_ids_first" and n == 0:
        script = valid_script()
        for page in script["slides"]:
            page.pop("id")
        return json.dumps(script, ensure_ascii=False), None
    if MODE == "garbage_first" and n == 0:
        return "ごめんなさい、台本は書けません。", None
    script = ten_page_script() if os.environ.get("FAKE_WRITER_PAGES") == "10" else valid_script()
    return "```json\n" + json.dumps(script, ensure_ascii=False, indent=1) + "\n```", None
