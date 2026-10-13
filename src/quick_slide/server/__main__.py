import sys

from .cli import main

for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932）でなく UTF-8 で出す。読むのはエージェントとサーバ
    _out.reconfigure(encoding="utf-8")
raise SystemExit(main())
