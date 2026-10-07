#!/usr/bin/env bash
# Quick Slide v2 のサーバと画面のテスト。これ1本で全部走る（エンジンのテストは skills/*/tests/run.sh）。
#
# 方針:
#   - 実の置き場（~/.quick-slide）を読まない・書かない。サーバは一時ディレクトリの置き場とポート 0 で立てる
#   - 本物のブラウザ（ヘッドレス Chrome）で画面を開く試験は tests/live。Chrome が無ければ skip する
#   - .pyc を書かない（このリポは配布物そのもので、__pycache__ を残さない）
#   - 失敗したら非ゼロで落ちる
#
# 使い方: bash tests/run.sh [-v]
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ROOT/src"
unset QUICK_SLIDE_HOME QUICK_SLIDE_BROWSER

cd "$ROOT"
"$PY" -m unittest discover -s tests -t tests "$@"
rc=$?
# 念のため（PYTHONDONTWRITEBYTECODE を外して走らせた後でも残さない）
find "$ROOT/src" "$ROOT/tests" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
exit $rc
