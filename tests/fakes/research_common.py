"""偽物の下調べ係（claude 風・codex 風が共有）。本物の LLM も Web も呼ばない。

偽物の `claude` は `--tools WebSearch,WebFetch`、偽物の `codex` は `--search` で呼ばれたら、ここで答える
（Web の道具を持たせたまま台本係の指示文が来たら、あるいは道具を絞らずに下調べの指示文が来たら、答えずに失敗する）。

振る舞いは環境変数で変える（記録は台本係と同じ `FAKE_WRITER_LOG` に `kind=research-<claude|codex>` で書く）:

    FAKE_RESEARCH_MODE  ok（既定）/ garbage_first（1 回目は JSON ではない文）/ no_source_first（1 回目は出典の行が無い原稿）/
                        always_bad（どの回も出典の行が無い）/ secret_first（1 回目は原稿に鍵の形の文字列）/
                        auth（ログインしていない）/ rate（利用枠に達した）
    FAKE_RESEARCH_SLEEP 答える前に待つ秒数（帯の「下調べをしています」を画面の試験が見るため。既定 0）

依頼に「N ページ」「N 枚」とあれば、page_count を N にして N 節の原稿を返す（無ければ page_count は null で 4 節）。
"""
import json
import os
import re
import time

import writer_common as W

MODE = os.environ.get("FAKE_RESEARCH_MODE", "ok")


def is_research(prompt: str) -> bool:
    return "下調べ係" in prompt


def _request(prompt: str) -> str:
    return W._between(prompt, "<<<依頼ここから\n", "\n依頼ここまで>>>")


def answer(prompt: str) -> dict:
    request = _request(prompt)
    m = re.search(r"(\d+)\s*(?:ページ|枚)", request)
    pages = int(m.group(1)) if m else None
    n = pages or 4
    heads = ["表紙: 会議の進め方の今（架空）"] + [f"{i}つ目の論点（架空）" for i in range(1, n - 1)] + ["まとめと次の一歩（架空）"]
    body = "\n\n".join(f"## {i}. {h}\n- 調べた事実 {i}（架空）\n出典: 架空の調査{i} https://example.com/report-{i}"
                       for i, h in enumerate(heads[:n], 1))
    return {"title": "会議の進め方の今（架空）", "purpose": "会議を短くする手がかりを伝える（架空）",
            "audience": "社内の管理職（架空）", "page_count": pages, "manuscript": body,
            "sources": [{"name": f"架空の調査{i}", "url": f"https://example.com/report-{i}"} for i in range(1, n + 1)]}


def answer_for(kind: str, prompt: str, argv: list[str]) -> tuple[str | None, str | None]:
    """(答えの文, 失敗の種類)。"""
    n = W.calls(f"research-{kind}")
    W.record(kind=f"research-{kind}", what="research", keys=W.leaked_keys(), cwd=os.getcwd(), mode=MODE, argv=argv,
             request=_request(prompt), retry="# 前の答えは検査で止まりました" in prompt,
             ng=[ln for ln in prompt.splitlines() if ln.startswith("- NG")], form=re.findall(r"資料の形式は「(.+?)」", prompt))
    time.sleep(float(os.environ.get("FAKE_RESEARCH_SLEEP", "0")))
    if W.leaked_keys():
        return None, "keys"
    if MODE == "auth":
        return None, "auth"
    if MODE == "rate":
        return None, "rate"
    if MODE == "garbage_first" and n == 0:
        return "調べましたが、まとめられませんでした。", None
    out = answer(prompt)
    if MODE == "always_bad" or (MODE == "no_source_first" and n == 0):
        out["manuscript"] = re.sub(r"\n出典: .*", "", out["manuscript"])
    if MODE == "secret_first" and n == 0:
        out["manuscript"] += "\n- 鍵 ghp_" + "a1B2" * 9
    return json.dumps(out, ensure_ascii=False), None
