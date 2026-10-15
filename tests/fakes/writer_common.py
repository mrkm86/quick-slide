"""偽物の台本係（claude 風・codex 風）が共有する部品。本物の LLM は呼ばない。

振る舞いは環境変数で変える:

    FAKE_WRITER_LOG     呼ばれるたびに 1 つ JSON を書くフォルダ（必須）
    FAKE_WRITER_MODE    ok（既定）/ invalid_first（1 回目はエンジンの検査で止まる台本・2 回目から正しい）/
                        no_ids_first（1 回目はページの id が無い台本）/
                        emoji_first（1 回目は文字に絵文字が入った台本）/ always_emoji（どの回も絵文字入り）/
                        always_invalid / garbage_first（1 回目は JSON ではない文）/ auth（ログインしていない）/
                        rate（利用枠に達した）/ slow（FAKE_WRITER_SLEEP 秒待ってから答える）
    FAKE_WRITER_IMAGES  0 にすると画像の枠を置かない
    FAKE_WRITER_SLEEP   slow の秒数（既定 2）
    FAKE_WRITER_MOTION_FILE  モーションの 1 回目の答えにする JSON のファイル（本物のエンジンの見本から作った台本。画面の確かめ用）
    FAKE_WRITER_PAGES   10 にすると 10 ページの台本（直す試験用。どのページも talk を 1 つ持つ）

モーション（指示文に「モード: モーション」がある）のときは、場面ごとの台本（`slides` に場面。拍の数 `beats`・`html`・`talk`）を返す。
FAKE_WRITER_MODE=scenes_key なら、場面の配列を `scenes` のキーで返す（`slides` に読み替えられるか）。invalid_first・
always_invalid は `id` が `bad-scene` の場面（偽物のエンジンが NG にする）、emoji_first・always_emoji は場面の文字に絵文字。

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


def is_motion(prompt: str) -> bool:
    return "モード: モーション" in prompt


def motion_scene(sid: str, title: str, beats: int = 2, *, text: str | None = None) -> dict:
    cells = "".join(f'<p class="x" data-in="{b}">{text or title}の{b + 1}つ目（架空）</p>' for b in range(beats))
    return {"id": sid, "title": title, "beats": beats, "html": f'<div class="stack">{cells}</div>',
            "talk": [f"{title}の{b + 1}つ目を話す（架空）" for b in range(beats)]}


def motion_script(key: str = "slides") -> dict:
    return {"title": "試験のモーション（架空）", "config": {"crumb": "試験", "chapters": [], "dur": 4200},
            key: [motion_scene("cover", "会議を半分にする", 2), motion_scene("why", "会議の4割は何も決めない", 3),
                  motion_scene("rules", "3つのルールで短くする", 2), motion_scene("closing", "まず2チームで試す", 2)]}


def motion_invalid() -> dict:
    return {"title": "試験のモーション（架空）", "slides": [motion_scene("bad-scene", "決まりに合わない場面", 2)]}


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


def headline_of_page(page: dict) -> str:
    return str(page.get("headline") or page.get("title") or "ページ")


def revision_answer(prompt: str, n_before: int) -> dict:
    script = _current_script(prompt)
    targets = [t.strip() for t in _between(prompt, "対象の id: ", "\n").split(",") if t.strip()]
    note = _between(prompt, "<<<指示ここから\n", "\n指示ここまで>>>")
    by_id = {p["id"]: p for p in script["slides"]}
    motion = is_motion(prompt)
    route = "image" if "絵だけ" in note else "both" if "両方" in note else "keep" if "そのまま" in note else "script"
    pages = []
    for t in targets:
        if MODE == "bad_revision_first" and n_before == 0:
            other = next(p["id"] for p in script["slides"] if p["id"] not in targets)
            pages.append({"id": other, "route": "script", "slides": [by_id[other]]})
            continue
        item = {"id": t, "route": route}
        page_pic = re.search(r"^- (attachments/page-[^（\s]+)（.*?出典: (.+?)）$", prompt, re.M)
        if route == "script" and page_pic and not motion:
            # 指示に書かれたページの画像を使う（docs/adr/0016）: 写真の型にして path と出典を書く
            item["slides"] = [{"id": t, "type": "photo", "layout": "right", "headline": headline_of_page(by_id[t]),
                               "image": {"path": page_pic.group(1)}, "caption": "出典: " + page_pic.group(2)}]
            pages.append(item)
            continue
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
                slides.append(motion_scene(t + "-b", "分けた後半", 2) if motion else
                              {"id": t + "-b", "type": "statement", "headline": "分けた後半（架空）",
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
    if is_motion(prompt):
        return {"slides": [motion_scene(f"p-added-{n}", f"足した場面{n}", 2)], "summary": "1 場面足した（架空）"}
    return {"slides": [{"id": f"p-added-{n}", "type": "statement", "headline": f"足したページ{n}（架空）",
                        "talk": [f"足したページ{n}の話（架空）"]}], "summary": "1 ページ足した（架空）"}


def outline_script(prompt: str, n_before: int) -> dict:
    manuscript = _between(prompt, "<<<原稿ここから\n", "原稿ここまで>>>")
    heads = re.findall(r"^##\s*\d+\.\s*(.+)$", manuscript, re.M)
    if MODE == "outline_wrong_first" and n_before == 0:
        heads = heads[:-1]
    if is_motion(prompt):
        return {"title": "構成どおりの資料（架空）", "slides": [motion_scene(f"p-{i}", h.strip(), 2) for i, h in enumerate(heads, 1)]}
    pages = [{"id": f"p-{i}", "type": "statement", "headline": h.strip(), "talk": [f"{i}枚目の話（架空）"]}
             for i, h in enumerate(heads, 1)]
    return {"title": "構成どおりの資料（架空）", "slides": pages}


def invalid_script() -> dict:
    return {"title": "試験の資料（架空）", "slides": [{"id": "cover", "type": "no-such-type", "headline": "知らない型"}]}


def prompt_kind(prompt: str) -> str:
    if "# 今回の仕事: モーションの共通設計" in prompt:
        return "motion_plan"
    if "# 今回の仕事: モーションの場面1つ" in prompt:
        return "motion_scene"
    if re.search(r"(挿絵|図解|画像)を 1 つ重ねます", prompt):   # 取り込んだページに重ねる（docs/adr/0013・0015）
        return "overlay"
    if "画像を 1 つ差し替えます" in prompt:
        return "replace"
    if "型のページ 1 枚に組み直します" in prompt:
        return "rebuild"
    if "取り込んだページの**文字だけ**" in prompt:   # 取り込んだページの文字を直す（docs/adr/0014）
        return "imported_text"
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
           motion="モード: モーション" in prompt, has_motion_contract="motion-slide/references/deck-format.md" in prompt
           and "motion-slide/references/authoring.md" in prompt,
           prompt_lines=[ln for ln in prompt.splitlines() if ln.startswith("- ")][:200],
           has_manuscript="<<<原稿ここから" in prompt,
           request=_between(prompt, "<<<依頼ここから\n", "\n依頼ここまで>>>") if "# 利用者の依頼" in prompt else None,
           retry="# 前の台本は検査で止まりました" in prompt,
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
    if what in ("motion_plan", "motion_scene"):
        fixed = os.environ.get("FAKE_WRITER_MOTION_FILE")
        deck = json.loads(pathlib.Path(fixed).read_text(encoding="utf-8")) if fixed else (
            outline_script(prompt, n_what) if "合意した「構成」" in prompt else motion_script())
        scenes = deck.get("slides", deck.get("scenes", []))
        count = re.search(r'場面数: (\d+) 場面', prompt)
        if count and not fixed:
            n_scenes = int(count.group(1))
            scenes = (scenes + [motion_scene(f'extra-{i}', f'追加{i}（架空）', 2)
                                for i in range(len(scenes), n_scenes)])[:n_scenes]
        if what == "motion_plan":
            plan = {k: v for k, v in deck.items() if k not in ("slides", "scenes")}
            plan.update(design="試験の共通設計（架空）", scenes=[
                {"id": v["id"], "title": v["title"], "beats": v["beats"], "brief": v["title"]} for v in scenes])
            return json.dumps(plan, ensure_ascii=False), None
        spec = json.JSONDecoder().raw_decode(prompt.split("# 対象場面\n", 1)[1])[0]
        scene = next((dict(v) for v in scenes if v['id'] == spec['id']),
                     motion_scene(spec['id'], spec['title'], spec['beats']))
        if os.environ.get('FAKE_MOTION_SLEEP_SCENE') == scene['id']:
            time.sleep(5)
        retry = "# 前の台本は検査で止まりました" in prompt
        if MODE == "always_invalid" or (MODE in ("invalid_first", "invalid_repair_garbage") and not retry):
            if scene['id'] == scenes[0]['id']:
                scene['html'] = '<p data-fake-invalid="true">形の試験（架空）</p>'
        if MODE == "invalid_repair_garbage" and retry:
            n_scene = sum(1 for p in LOG.glob('*.json')
                          if (v := json.loads(p.read_text(encoding='utf-8'))).get('what') == 'motion_scene'
                          and pathlib.Path(v['cwd']).name == scene['id'])
            if n_scene == 2:
                return 'broken JSON', None
        if MODE == "always_emoji" or (MODE == "emoji_first" and not retry):
            if scene['id'] == 'rules':
                scene['html'] = '<p>✅ 25分を基本にする</p>'
        return json.dumps(scene, ensure_ascii=False), None
    if what == "overlay":
        # overlay_bad_first: 1 回目は文字の箱に重ねる（書き直させる）。2 回目からは右の空いた所
        laid = prompt.count("- 重ねた絵")   # 前に重ねた絵の数だけ下にずらす（重ならないように）
        box = {"x": 100, "y": 300, "w": 600, "h": 400} if MODE == "overlay_bad_first" and n_what == 0 else \
            {"x": 1450, "y": 60 + laid * 240, "w": 220, "h": 220}
        svg = (f'<svg viewBox="0 0 {box["w"]} {box["h"]}"><rect width="{box["w"]}" height="{box["h"]}" rx="40" fill="#e6f1f3"/>'
               '<icon name="users" x="40" y="60" size="320" fill="#006f83"/></svg>')
        extra = {"prompt": "明るい会議室の机の上の時計（架空）"} if "prompt に何を描くか" in prompt else {}
        return json.dumps({**box, "svg": svg, **extra, "summary": "右の空いた所に重ねた（架空）"}, ensure_ascii=False), None
    if what == "replace":
        extra = {"prompt": "夕方の会議室（架空）"} if "prompt に何を描くか" in prompt else {}
        return json.dumps({"n": 1, **extra, "summary": "1 番の画像を替えた（架空）"}, ensure_ascii=False), None
    if what == "rebuild":
        pid = re.search(r'page の id は "([^"]+)"', prompt).group(1)
        path = re.search(r'image: \{"path": "([^"]+)"\}', prompt)
        if "画像を主役にする型" in prompt:
            image = {"path": path.group(1)} if path else {"prompt": "会議室の机（架空）"}
            page = {"id": pid, "type": "photo", "headline": "組み直した見出し（架空）", "image": image}
        else:
            page = {"id": pid, "type": "flow", "headline": "組み直した流れ（架空）",
                    "items": [{"label": "課題"}, {"label": "原因"}, {"label": "解決"}]}
        return json.dumps({"page": page, "summary": "型のページに組み直した（架空）"}, ensure_ascii=False), None
    if what == "imported_text":
        # 番号の行（- n: 文字）を読み、印でない行の頭 2 つに「（直し）」を付ける。long_first は 1 回目だけ長すぎる
        segs = [(int(m.group(1)), m.group(2)) for m in re.finditer(r"^- (\d+): (.*?)(（箇条書きの印。直さない）)?$",
                                                                 prompt, re.M) if not m.group(3)]
        long_first = MODE == "imported_text_long_first" and n_what == 0
        # overflow_first: 1 回目は字数の決まりには収まるが、見出しの箱からはみ出す長さにする
        wide_first = MODE == "imported_text_overflow_first" and n_what == 0
        changes = [{"n": n, "text": (t * 5 if long_first else t + "を長くして箱からはみ出させる続き"
                                      if wide_first and i == 0 else t + "（直し）")} for i, (n, t) in enumerate(segs[:2])]
        return json.dumps({"changes": changes, "summary": "見出しと要点を直した（架空）"}, ensure_ascii=False), None
    if what == "revise":
        return json.dumps(revision_answer(prompt, n_what), ensure_ascii=False), None
    if what == "insert":
        return json.dumps(insert_answer(prompt), ensure_ascii=False), None
    if "合意した「構成」" in prompt:
        return json.dumps(outline_script(prompt, n_what), ensure_ascii=False), None
    motion = is_motion(prompt)
    if MODE == "always_invalid" or (MODE == "invalid_first" and n == 0):
        return json.dumps(motion_invalid() if motion else invalid_script(), ensure_ascii=False), None
    if MODE == "no_ids_first" and n == 0:
        script = motion_script() if motion else valid_script()
        for page in script["slides"]:
            page.pop("id")
        return json.dumps(script, ensure_ascii=False), None
    if MODE == "always_emoji" or (MODE == "emoji_first" and n == 0):
        script = motion_script() if motion else valid_script()
        if motion:
            script["slides"][2]["html"] = '<p class="x" data-in="0">✅ 25分を基本にする</p>'
        else:
            script["slides"][2]["items"][0]["title"] = "✅ 25分を基本にする"
        return json.dumps(script, ensure_ascii=False), None
    if MODE == "garbage_first" and n == 0:
        return "ごめんなさい、台本は書けません。", None
    if motion:
        fixed = os.environ.get("FAKE_WRITER_MOTION_FILE")   # 本物のエンジンの見本から作った台本（`scenes` のまま。読み替えを通す）
        if fixed:
            return pathlib.Path(fixed).read_text(encoding="utf-8"), None
        return json.dumps(motion_script("scenes" if MODE == "scenes_key" else "slides"), ensure_ascii=False), None
    script = ten_page_script() if os.environ.get("FAKE_WRITER_PAGES") == "10" else valid_script()
    # 挿絵（image.svg）のページを足す（ADR 0011）。bad_first は 1 回目だけ描けない挿絵（viewBox が無い）を書く
    illust = os.environ.get("FAKE_WRITER_ILLUST")
    if illust:
        svg = ('<svg viewBox="0 0 960 1080"><rect width="960" height="1080" fill="#e6f1f3"/>'
               '<icon name="users" x="250" y="330" size="460" fill="#006f83"/></svg>')
        if illust == "bad_first" and n == 0:
            svg = '<svg><rect width="10" height="10"/></svg>'
        script["slides"].insert(-1, {"id": "p-illust", "type": "photo", "layout": "right", "headline": "挿絵のページ（架空）",
                                     "image": {"svg": svg}})
    return "```json\n" + json.dumps(script, ensure_ascii=False, indent=1) + "\n```", None
