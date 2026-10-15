#!/usr/bin/env python3
"""偽物の `claude`（`claude -p --output-format stream-json` の形だけ真似る）。本物の LLM は呼ばない。

道具が空（`--tools ""`）なら台本係、Web の 2 つだけ（`--tools WebSearch,WebFetch` と同じものを `--allowedTools`）なら
下調べ係として答える（`research_common.py`）。ほかの道具の渡し方は断る。"""
import json
import pathlib
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import research_common as R  # noqa: E402
import writer_common as W  # noqa: E402

WEB_TOOLS = "WebSearch,WebFetch"

HELP = """Usage: claude [options] [command] [prompt]
  -p, --print  --output-format <format>  --tools <tools...>  --strict-mcp-config
  --verbose --include-partial-messages
  --disable-slash-commands  --no-session-persistence  --safe-mode
  --allowedTools, --allowed-tools <tools...>
  --permission-mode <mode>  (choices: "acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan")
"""


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--help"]:
        print(HELP)
        return 0
    if "-p" not in argv or argv[argv.index("--output-format") + 1] != "stream-json":
        print("fake claude: -p --output-format stream-json だけ受ける", file=sys.stderr)
        return 2
    if not {"--verbose", "--include-partial-messages"} <= set(argv):
        return 2
    print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
    tools = argv[argv.index("--tools") + 1]
    prompt = sys.stdin.buffer.read().decode("utf-8")   # 本物と同じく UTF-8 で読む（Windows の既定は cp932）
    if tools == WEB_TOOLS:
        allowed = argv[argv.index("--allowedTools") + 1] if "--allowedTools" in argv else None
        mode = argv[argv.index("--permission-mode") + 1] if "--permission-mode" in argv else None
        if allowed != WEB_TOOLS or mode != "dontAsk" or not R.is_research(prompt):
            print("fake claude: Web の道具は下調べ係にだけ、その 2 つだけを許して渡す", file=sys.stderr)
            return 2
        text, failure = R.answer_for("claude", prompt, argv)
    elif tools != "" or R.is_research(prompt):
        print("fake claude: 道具を空にしていない（下調べ係なら Web の 2 つだけ）", file=sys.stderr)
        return 2
    else:
        text, failure = W.answer_for("claude", prompt)
    if failure == "keys":
        print("fake claude: API キーが渡ってきた", file=sys.stderr)
        return 3
    if failure == "auth":
        print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                          "result": "Not logged in · Please run /login"}))
        return 1
    if failure == "rate":
        print(json.dumps({"type": "result", "is_error": True, "result": "Claude usage limit reached."}))
        return 1
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
