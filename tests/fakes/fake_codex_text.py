#!/usr/bin/env python3
"""偽物の `codex`（台本係として `codex exec --json -o <ファイル> -` で呼ばれる形だけ真似る）。本物は呼ばない。

`exec` の前に `--search` があれば下調べ係として答える（`research_common.py`）。そのときは `web_search="live"` と、
シェルの道具を切る設定（`features.shell_tool=false`）が無ければ断る。"""
import json
import os
import pathlib
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import research_common as R  # noqa: E402
import writer_common as W  # noqa: E402

HELP = """Usage: codex [OPTIONS] [PROMPT]
  -c, --config <key=value>  -a, --ask-for-approval <APPROVAL_POLICY>
      --search  Enable live web search
"""


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--help"]:
        print(HELP if os.environ.get("FAKE_CODEX_NO_SEARCH") != "1" else HELP.replace("--search", "--other"))
        return 0
    if argv[:2] == ["login", "status"]:
        if W.MODE == "auth":
            print("Not logged in")
            return 1
        print("Logged in using ChatGPT")
        return 0
    if "exec" not in argv:
        print("fake codex: exec だけ受ける", file=sys.stderr)
        return 2
    rest = argv[argv.index("exec") + 1:]
    for need in ("--ignore-user-config", "--ephemeral", "--json"):
        if need not in rest:
            print(f"fake codex: {need} が無い", file=sys.stderr)
            return 2
    if rest[rest.index("-s") + 1] != "read-only":
        print("fake codex: 読み取りだけの砂場ではない", file=sys.stderr)
        return 2
    out = pathlib.Path(rest[rest.index("-o") + 1])
    head = argv[:argv.index("exec")]
    prompt = sys.stdin.read()
    if "--search" in head:
        if 'web_search="live"' not in head or "features.shell_tool=false" not in head or not R.is_research(prompt):
            print("fake codex: Web 検索は下調べ係にだけ、シェルを切って渡す", file=sys.stderr)
            return 2
        text, failure = R.answer_for("codex", prompt, argv)
    elif R.is_research(prompt):
        print("fake codex: 下調べ係なのに Web 検索を開いていない", file=sys.stderr)
        return 2
    else:
        text, failure = W.answer_for("codex", prompt)
    if failure == "keys":
        print("fake codex: API キーが渡ってきた", file=sys.stderr)
        return 3
    print(json.dumps({"type": "thread.started", "thread_id": "00000000-0000-4000-8000-000000000000"}))
    if failure == "auth":
        print(json.dumps({"type": "error", "message": "401 Unauthorized: please log in again"}))
        return 1
    if failure == "rate":
        print(json.dumps({"type": "error", "message": "You've hit your usage limit. Try again at 5pm."}))
        return 1
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}, ensure_ascii=False))
    out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
