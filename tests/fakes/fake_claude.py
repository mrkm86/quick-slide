#!/usr/bin/env python3
"""偽物の `claude`（`claude -p --output-format json` の形だけ真似る）。本物の LLM は呼ばない。"""
import json
import pathlib
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import writer_common as W  # noqa: E402

HELP = """Usage: claude [options] [command] [prompt]
  -p, --print  --output-format <format>  --tools <tools...>  --strict-mcp-config
  --disable-slash-commands  --no-session-persistence  --safe-mode
"""


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--help"]:
        print(HELP)
        return 0
    if "-p" not in argv or argv[argv.index("--output-format") + 1] != "json":
        print("fake claude: -p --output-format json だけ受ける", file=sys.stderr)
        return 2
    if argv[argv.index("--tools") + 1] != "":
        print("fake claude: 道具を空にしていない", file=sys.stderr)
        return 2
    text, failure = W.answer_for("claude", sys.stdin.read())
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
