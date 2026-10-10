#!/usr/bin/env bash
# yomiyasu の写しを、指定のタグの版に更新する。ネットワークに出るのはこのスクリプトだけ。
#
# 使い方: bash src/quick_slide/vendor/yomiyasu/update.sh <タグ> [--keep-on-fail]   例: v1.1.1
#
# 1. タグの形を確かめる（例: v1.1.1）。合わなければ何も触らずに止まる
# 2. 指定のタグを一時フォルダに浅く clone する
# 3. 写すファイルとライセンス文・書き換えた README を一時フォルダにそろえ、そろっているか確かめる（ここまでは vendor に触れない）
# 4. 確かめたものを vendor に入れ替える。途中で失敗したら、元のファイルに戻す
# 5. リポジトリの試験（tests/run.sh）を流す。落ちたら元に戻す（--keep-on-fail を付けると新しい版のまま残す）
set -euo pipefail

TAG=""
KEEP=0
for arg in "$@"; do
  case "$arg" in
    --keep-on-fail) KEEP=1 ;;
    -*) echo "知らない引数: $arg" >&2; exit 2 ;;
    *) if [[ -z "$TAG" ]]; then TAG="$arg"; else echo "タグは 1 つだけ" >&2; exit 2; fi ;;
  esac
done
if [[ -z "$TAG" ]]; then
  echo "使い方: bash $0 <タグ> [--keep-on-fail]   例: v1.1.1" >&2
  exit 2
fi
if [[ ! "$TAG" =~ ^v?[0-9]+(\.[0-9]+){1,3}$ ]]; then
  echo "タグの形が違う: ${TAG}（例: v1.1.1）" >&2
  exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
URL="https://github.com/nanaism/yomiyasu.git"
FILES=(yomiyasu_lint.py markdown_visibility.py LICENSE UNICODE-LICENSE.txt README.md)
TMP="$(mktemp -d -t yomiyasu-update)"
trap 'rm -rf "$TMP"' EXIT

git clone --quiet --depth 1 --branch "$TAG" "$URL" "$TMP/src"
COMMIT="$(git -C "$TMP/src" rev-parse HEAD)"

# 一時フォルダにそろえる
NEW="$TMP/new"
mkdir -p "$NEW"
SCRIPTS="$TMP/src/skills/yomiyasu/scripts"
cp "$SCRIPTS/yomiyasu_lint.py" "$SCRIPTS/markdown_visibility.py" "$NEW/"
cp "$TMP/src/LICENSE" "$TMP/src/UNICODE-LICENSE.txt" "$NEW/"
# README の版・コミット・日付は、値を環境変数で渡して文字列として書き換える（シェルや sed に値を埋め込まない）
TAG="$TAG" COMMIT="$COMMIT" TODAY="$(date +%Y-%m-%d)" SRC_README="$HERE/README.md" OUT_README="$NEW/README.md" \
python3 -I - <<'PY'
import os, re
text = open(os.environ["SRC_README"], encoding="utf-8").read()
for label, key in (("版", "TAG"), ("コミット", "COMMIT"), ("写した日", "TODAY")):
    value = os.environ[key]
    text, n = re.subn(rf"(?m)^- {label}: .*$", lambda _m: f"- {label}: {value}", text)
    if n != 1:
        raise SystemExit(f"README に「- {label}:」の行が 1 つではない（{n}）")
open(os.environ["OUT_README"], "w", encoding="utf-8").write(text)
PY

# そろっているか確かめる
for f in "${FILES[@]}"; do
  [[ -s "$NEW/$f" ]] || { echo "必要なファイルが無い・空: ${f}（このタグでは写せない）。vendor は変えていない" >&2; exit 1; }
done
grep -q "MIT License" "$NEW/LICENSE" || { echo "LICENSE が MIT ではない。vendor は変えていない" >&2; exit 1; }
grep -qi "UNICODE LICENSE" "$NEW/UNICODE-LICENSE.txt" || { echo "UNICODE-LICENSE.txt の中身が違う。vendor は変えていない" >&2; exit 1; }
grep -q "^- 版: $TAG\$" "$NEW/README.md" || { echo "README の版を書き換えられなかった。vendor は変えていない" >&2; exit 1; }
for f in yomiyasu_lint.py markdown_visibility.py; do
  python3 -I -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$NEW/$f" \
    || { echo "$f が Python として読めない。vendor は変えていない" >&2; exit 1; }
done

# 元に戻せるように控えを取ってから入れ替える
BACKUP="$TMP/backup"
mkdir -p "$BACKUP"
for f in "${FILES[@]}"; do
  if [[ -f "$HERE/$f" ]]; then cp -p "$HERE/$f" "$BACKUP/$f"; fi
done
restore() {
  rm -f "$HERE"/*.new
  for f in "${FILES[@]}"; do
    if [[ -f "$BACKUP/$f" ]]; then cp -p "$BACKUP/$f" "$HERE/$f"; else rm -f "$HERE/$f"; fi
  done
  echo "元の版に戻した" >&2
}
for f in "${FILES[@]}"; do
  if ! cp "$NEW/$f" "$HERE/$f.new" || ! mv "$HERE/$f.new" "$HERE/$f"; then
    echo "入れ替えに失敗した: $f" >&2
    restore
    exit 1
  fi
done
echo "写した: $TAG ($COMMIT)"

if ! bash "$REPO/tests/run.sh"; then
  if [[ "$KEEP" == 1 ]]; then
    echo "試験が落ちた。--keep-on-fail なので新しい版のまま残す" >&2
  else
    echo "試験が落ちた" >&2
    restore
  fi
  exit 1
fi
