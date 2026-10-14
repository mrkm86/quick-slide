#!/usr/bin/env bash
# illust のテスト。これ 1 本で全部走る。Chrome が無ければ PNG の試験だけ飛ばす。
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # スキルの中に __pycache__ を残さない（配る物・リンク先）
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPBASE="${TMPDIR:-/tmp}"
SCRATCH="$(mktemp -d "${TMPBASE%/}/illust-tests.XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT
export TMPDIR="$SCRATCH"
echo "=== illust tests ==="
python3 -m unittest discover -s "$DIR" -p "test_*.py" "$@"
