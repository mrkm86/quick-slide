"""偽物の台本係（claude 風・codex 風）が共有する部品。本物の LLM は呼ばない。

振る舞いは環境変数で変える:

    FAKE_WRITER_LOG     呼ばれるたびに 1 つ JSON を書くフォルダ（必須）
    FAKE_WRITER_MODE    ok（既定）/ invalid_first（1 回目はエンジンの検査で止まる台本・2 回目から正しい）/
                        no_ids_first（1 回目はページの id が無い台本）/
                        always_invalid / garbage_first（1 回目は JSON ではない文）/ auth（ログインしていない）/
                        rate（利用枠に達した）/ slow（FAKE_WRITER_SLEEP 秒待ってから答える）
    FAKE_WRITER_IMAGES  0 にすると画像の枠を置かない
    FAKE_WRITER_SLEEP   slow の秒数（既定 2）

**API キーの環境変数が 1 つでも届いていたら、答えずに失敗する**（サブスク枠だけで動かす約束の試験）。
"""
import json
import os
import pathlib
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


def invalid_script() -> dict:
    return {"title": "試験の資料（架空）", "slides": [{"id": "cover", "type": "no-such-type", "headline": "知らない型"}]}


def answer_for(kind: str, prompt: str) -> tuple[str | None, str | None]:
    """(答えの文, 失敗の種類)。"""
    n = calls(kind)          # これまでの回数（この呼び出しを含まない）
    record(kind=kind, keys=leaked_keys(), cwd=os.getcwd(), mode=MODE,
           has_contract="# 台本の決まり" in prompt and "deck-format.md" in prompt,
           has_pitch_contract="image-slide/references/intake.md" in prompt,
           has_manuscript="<<<原稿ここから" in prompt, retry="# 前の台本は検査で止まりました" in prompt,
           ng=[ln for ln in prompt.splitlines() if ln.startswith("- NG")], chars=len(prompt))
    if leaked_keys():
        return None, "keys"
    if MODE == "auth":
        return None, "auth"
    if MODE == "rate":
        return None, "rate"
    if MODE == "slow":
        time.sleep(float(os.environ.get("FAKE_WRITER_SLEEP", "2")))
    if MODE == "always_invalid" or (MODE == "invalid_first" and n == 0):
        return json.dumps(invalid_script(), ensure_ascii=False), None
    if MODE == "no_ids_first" and n == 0:
        script = valid_script()
        for page in script["slides"]:
            page.pop("id")
        return json.dumps(script, ensure_ascii=False), None
    if MODE == "garbage_first" and n == 0:
        return "ごめんなさい、台本は書けません。", None
    return "```json\n" + json.dumps(valid_script(), ensure_ascii=False, indent=1) + "\n```", None
