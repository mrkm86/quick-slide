"""`python -m playwright install chromium` の偽物。呼ばれた引数を記録し、FAKE_PLAYWRIGHT_CHROMIUM のファイルを作る。"""
import json
import os
import pathlib
import sys

log = os.environ.get("FAKE_PLAYWRIGHT_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(sys.argv[1:]) + "\n")
target = os.environ.get("FAKE_PLAYWRIGHT_INSTALLS")
if target:
    pathlib.Path(target).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(target).write_text("")
