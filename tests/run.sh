#!/usr/bin/env bash
# Quick Slide v2 のサーバと画面のテスト。これ1本で全部走る（エンジンのテストは skills/*/tests/run.sh）。
#
# 方針:
#   - 実の置き場（~/.quick-slide）を読まない・書かない。サーバは一時ディレクトリの置き場とポート 0 で立てる
#   - 本物のブラウザ（ヘッドレス Chrome）で画面を開く試験は tests/live。Chrome が無ければ skip する
#   - 本物の claude / codex（台本係・絵を描くエンジン）は呼ばない。既定の差し先を「呼ばれたら落ちる」偽物にしておき、
#     試験は自分で偽物（tests/fakes/）を差す。描いた絵のキャッシュも使い捨てのフォルダへ
#   - .pyc を書かない（このリポは配布物そのもので、__pycache__ を残さない）
#   - 失敗したら非ゼロで落ちる
#
# 使い方: bash tests/run.sh [-v] [-j 並列数]（既定 4、-j 1 は直列）
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ROOT/src"
unset QUICK_SLIDE_HOME QUICK_SLIDE_BROWSER
REFUSE="$ROOT/tests/fakes/refuse.py"
chmod +x "$ROOT"/tests/fakes/fake_*.py "$REFUSE" "$ROOT"/skills/*/tests/fake_codex.py 2>/dev/null
export QUICK_SLIDE_CLAUDE="$REFUSE" QUICK_SLIDE_CODEX_TEXT="$REFUSE" HTML_SLIDE_CODEX="$REFUSE" IMAGE_SLIDE_CODEX="$REFUSE"
# PATH の外のよくある置き場（npm のグローバル・Homebrew など）は探させない。そこにある本物の claude・codex を見つけてしまう
export QUICK_SLIDE_CLI_DIRS=""
# 雛形の XXXXXX は GNU の mktemp（Windows の Git Bash）が要る。`-t <名前>` は mac でしか通らない
TMPBASE="${TMPDIR:-/tmp}"   # mac の TMPDIR は / で終わる。// が残ると、パスを見比べる試験がずれる
SCRATCH="$(mktemp -d "${TMPBASE%/}/quick-slide-tests.XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT
# 試験が tempfile で作る一時フォルダもここへ寄せ、落ちた試験の分も最後にまとめて消す
mkdir -p "$SCRATCH/tmp"
export TMPDIR="$SCRATCH/tmp"
export HTML_SLIDE_CACHE="$SCRATCH/html-slide-cache" IMAGE_SLIDE_CACHE="$SCRATCH/image-slide-cache" MOTION_SLIDE_CACHE="$SCRATCH/motion-slide-cache"
export CODEX_HOME="$SCRATCH/no-codex-home"

cd "$ROOT"
# `python -m unittest` と同じ。ただし試験を回すこのプロセスの表示（-v の試験名・失敗の報告）だけは UTF-8 にする
# （Windows の既定は cp932）。PYTHONIOENCODING や -X utf8 は使わない: 子のプロセスや本体の open() にも効いて、
# 本体の文字コードの書き忘れを隠してしまう
"$PY" -c 'import sys
for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8")
sys.argv[0] = "python -m unittest"
import runpy
runpy.run_path("tests/parallel.py", run_name="__main__")' "$@"
rc=$?
# 念のため（PYTHONDONTWRITEBYTECODE を外して走らせた後でも残さない）
find "$ROOT/src" "$ROOT/tests" "$ROOT/skills" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null   # エンジンを子で呼ぶ試験が skills にも残す
exit $rc
