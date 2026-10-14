import sys

from .cli import main

for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932・\r\n）でなく UTF-8・\n で出す。読むのはエージェントとサーバ
    _out.reconfigure(encoding="utf-8", newline="\n")
raise SystemExit(main())
