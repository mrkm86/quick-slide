#!/usr/bin/env bash
# motion-slide のテスト。これ1本で全部走る。
#
# 方針:
#   - ネットワークに依存しない（Chrome はローカルの Playwright）
#   - build の検査（台本の誤りを rc=2 で止める）・テーマの色・時刻表・雛形の全拍検査
#   - 失敗したら即座に非ゼロで落ちる
set -euo pipefail

# 実の置き場の設定（ロゴ・テーマ・出力）に左右されないよう、設定は無いものにする
export MOTION_SLIDE_CONFIG=/nonexistent/motion-slide-config.yaml

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SK="$(cd "$DIR/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PY="python3 -I"

pass=0
fail=0

run() {   # run "テスト名" コマンド...
  local name="$1"; shift
  if out=$("$@" 2>&1); then
    echo "  ✅ $name"
    pass=$((pass + 1))
  else
    echo "  ❌ $name"
    echo "$out" | sed 's/^/     /'
    fail=$((fail + 1))
  fi
}
rc2() { set +e; "$@" >/dev/null 2>&1; local r=$?; set -e; [ "$r" -eq 2 ]; }
kinds() { ffprobe -v error -show_entries stream=codec_type -of csv=p=0 "$1" | tr -d ' ,' | sort | tr '\n' ' '; }
secs() { ffprobe -v error -show_entries format=duration -of csv=p=0 "$1" | cut -c1-3; }
vid_with_sound() { $PY "$SK/scripts/record.py" "$TMP/v/index.html" --fps 10 --start 3.1 --seconds 3 >/dev/null 2>&1 && [ "$(kinds "$TMP/v/video.mp4")" = "audio video " ] && [ "$(secs "$TMP/v/video.mp4")" = "3.0" ]; }
vid_silent() { $PY "$SK/scripts/record.py" "$TMP/v/index.html" --fps 10 --seconds 1 --no-sfx --no-bgm --out "$TMP/v/s.mp4" >/dev/null 2>&1 && [ "$(kinds "$TMP/v/s.mp4")" = "video " ]; }

echo "=== motion-slide tests ==="

# 台本の誤りを作る
sed 's/data-beats="4" data-ch="0" data-title="出す・消す・照らす" data-term="拍" data-dur="4200,4200,4200,5200"/data-beats="4" data-ch="0" data-title="出す・消す・照らす" data-term="拍" data-dur="4200,4200"/' "$SK/templates/deck.html" > "$TMP/bad-dur.html"
sed 's/MS.hook("s3"/MS.hook("s9"/' "$SK/templates/deck.html" > "$TMP/bad-hook.html"
sed 's/id="ms-config"/id="xx"/' "$SK/templates/deck.html" > "$TMP/no-config.html"
sed 's/id="s3"/id="s2"/' "$SK/templates/deck.html" > "$TMP/dup-id.html"
sed 's/<section class="scene" id="s1" data-beats="2"/<section class="scene" id="s1" data-beats="2" data-thumb="2"/' "$SK/templates/deck.html" > "$TMP/bad-thumb.html"

run "雛形が組める（ロゴなし）"                  $PY "$SK/scripts/build.py" "$SK/templates/deck.html" --out "$TMP/ok/index.html" --logo none
run "組んだ HTML は外へ取りに行かない"           bash -c "! grep -Eq '(src|href)=\"https?://' '$TMP/ok/index.html'"
run "テーマの主色（html-slide の default の primary）が入る"  bash -c "grep -q -- \"--pri:\$(sed -n 's/^primary: *\"\\(#[0-9a-fA-F]*\\)\".*/\\1/p' \"$SK/../html-slide/themes/default.yaml\")\" \"$TMP/ok/index.html\""
run "data-dur の数が拍と違えば rc=2"             rc2 $PY "$SK/scripts/build.py" "$TMP/bad-dur.html" --out "$TMP/x.html" --logo none
run "無い場面へのフックは rc=2"                  rc2 $PY "$SK/scripts/build.py" "$TMP/bad-hook.html" --out "$TMP/x.html" --logo none
run "ms-config が無ければ rc=2"                  rc2 $PY "$SK/scripts/build.py" "$TMP/no-config.html" --out "$TMP/x.html" --logo none
run "場面の id が重なれば rc=2"                  rc2 $PY "$SK/scripts/build.py" "$TMP/dup-id.html" --out "$TMP/x.html" --logo none
run "data-thumb が拍の外なら rc=2"               rc2 $PY "$SK/scripts/build.py" "$TMP/bad-thumb.html" --out "$TMP/x.html" --logo none
run "台本と同じファイルへは書かない"              rc2 $PY "$SK/scripts/build.py" "$SK/templates/deck.html" --out "$SK/templates/deck.html"
run "時刻表: 終わりは 3.6+4.2+4.2*3+5.2+4.2+5.6+6.0 = 41.4 秒" bash -c "$PY '$SK/scripts/timeline.py' '$SK/templates/deck.html' --json | grep -q '\"end\": 41.4'"
run "時刻表: s3 の拍2 は 7.8+17.8+4.2 = 29.8 秒から"            bash -c "$PY '$SK/scripts/timeline.py' '$SK/templates/deck.html' --json | grep -q '\"s3:2\": 29.8'"
run "MIDI: 音符から events.json と song.mid"     bash -c "cd '$TMP' && $PY -c \"import sys; sys.path.insert(0,'$SK/scripts/bgm'); from midi import Song; s=Song(84); s.program(0,0); s.note(1,0,60,80,.5); s.drum(2,49,100); print(s.save('$TMP/bgm'))\" && head -c 4 '$TMP/bgm/song.mid' | grep -q MThd && grep -q '\"k\": \"pc\"' '$TMP/bgm/events.json'"
if $PY -c "import playwright" 2>/dev/null; then
  run "一覧のサムネイルが場面の数（3）だけ入る"   bash -c "$PY -c \"import re,json,sys; h=open('$TMP/ok/index.html').read(); m=re.search(r'id=.ms-thumbs.>(.*?)</script>', h); d=json.loads(m.group(1)); sys.exit(0 if sorted(d)==['s1','s2','s3'] and all(v.startswith('data:image/webp') for v in d.values()) else 1)\""
  ffmpeg -loglevel error -f lavfi -i "sine=frequency=440:duration=60" -c:a aac "$TMP/bgm.m4a"
  $PY "$SK/scripts/build.py" "$SK/templates/deck.html" --out "$TMP/v/index.html" --no-thumbs --logo none --bgm "$TMP/bgm.m4a" >/dev/null
  run "動画: 3.1 から 3 秒・10fps で映像と音が 3 秒"  vid_with_sound
  run "動画: --no-sfx --no-bgm なら音の流れが無い"   vid_silent
  run "動画: --start が台本の外なら rc=2"          rc2 $PY "$SK/scripts/record.py" "$TMP/v/index.html" --start 9.1 --seconds 1
  run "雛形の全拍: NG 0・警告 0"                 bash -c "$PY '$SK/scripts/check.py' '$TMP/ok/index.html' --out '$TMP/ok/check' --wait 1600 | tail -1 | grep -q 'NG 0 ・ 警告 0'"
  run "inspect.md に拍ごとの文字と台本が入る"     bash -c "grep -q '## s3 値がマスへ飛ぶ' '$TMP/ok/check/inspect.md' && grep -q '話す台本: 値が飛んで' '$TMP/ok/check/inspect.md'"
else
  echo "  - playwright が無いので全拍の検査は飛ばす"
fi

echo
echo "pass: $pass / fail: $fail"
[ "$fail" -eq 0 ]
