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
# Windows の Git Bash: 下の試験は、パスを Python のコードの文字列（-c '…'）にも埋め込む。Git Bash は引数のパスは
# Windows の形に直すが、コードの中の文字列は直さないので、始めから C:/… の形（bash でも Python でも読める）にそろえる
if command -v cygpath >/dev/null 2>&1; then
  DIR="$(cygpath -m "$DIR")"; SK="$(cygpath -m "$SK")"; TMP="$(cygpath -m "$TMP")"
fi
# 場面ごとの絵のキャッシュも使い捨てにする（実の置き場を読まない・書かない）
export MOTION_SLIDE_CACHE="$TMP/cache"
# -I でなく -E: PYTHON* の環境変数は無視するが、利用者の場所（pip --user で入れた PyYAML など）は見る。
# -X utf8: 下に埋め込んだ Python のコード（-c・ヒアドキュメント）の open() も UTF-8 で読み書きする（Windows の既定は cp932）。
# スクリプトの本体の encoding の書き忘れは tests/test_windows_compat.py が見る
PY="python3 -E -X utf8"

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

# --- 場面ごとの台本（JSON）→ HTML 1枚（scripts/motion_slide.py・split.py） -------------------------
MS="$SK/scripts/motion_slide.py"
mkdir -p "$TMP/j"
$PY "$SK/scripts/split.py" "$SK/templates/deck.html" --out "$TMP/j/script.json" >/dev/null
# 台本の JSON を直して NG を作る: mut 名前 'python の式（d は台本）'
mut() { $PY -c "import json,sys; d=json.load(open('$TMP/j/script.json')); $2; json.dump(d, open('$TMP/j/$1.json','w'), ensure_ascii=False)"; }
ngs() { set +e; out=$($PY "$MS" check --script "$TMP/j/$1.json" --quick 2>&1); r=$?; set -e; [ "$r" -eq 2 ] && echo "$out" | grep -q "$2"; }
mut ok 'pass'
mut no-title 'del d["scenes"][1]["title"]'
mut dup-id 'd["scenes"][2]["id"]="s2"'
mut bad-id 'd["scenes"][0]["id"]="S 1"'
mut bad-dur 'd["scenes"][1]["dur"]=[1000,2000]'
mut bad-ch 'd["scenes"][1]["ch"]=5'
mut bad-key 'd["scenes"][0]["beat"]=2'
mut bad-talk 'd["scenes"][1]["talk"]=["a"]'
mut bad-in 'd["scenes"][0]["html"]=d["scenes"][0]["html"].replace("data-in=\"1\"","data-in=\"7\"") if isinstance(d["scenes"][0]["html"],str) else [l.replace("data-in=\"1\"","data-in=\"7\"") for l in d["scenes"][0]["html"]]'
mut open-div 'd["scenes"][0]["html"]="<div class=\"cover\"><h1 class=\"x\" data-in=\"0\">a</h1>"'
mut has-section 'd["scenes"][0]["html"]="<section>x</section>"'
mut hook-wrapped 'd["scenes"][2]["hook"]="MS.hook(\"s3\", (b, x) => {});"'
mut hook-other 'd["scenes"][2]["hook"]="document.querySelector(\"#s2 .col\");"'
mut hook-other-el 'd["scenes"][1]["hook"]="document.getElementById(\"val\");"'
mut hook-syntax 'd["scenes"][2]["hook"]="if (b {"'
mut style-leak 'd["scenes"][0]["style"]=".cover{color:red}"'
mut dup-el 'd["scenes"][0]["html"]="<div id=\"tb\"></div>"'
run "台本 JSON: 雛形を分けた形が形の検査を通る"        $PY "$MS" check --script "$TMP/j/ok.json" --quick
run "台本 JSON: 読めない JSON は rc=2"                  bash -c "echo '{' > '$TMP/j/broken.json'; set +e; $PY '$MS' check --script '$TMP/j/broken.json' --quick >/dev/null 2>&1; [ \$? -eq 2 ]"
run "NG: title が無い（2枚目 id s2）"                   ngs no-title "NG: 2枚目（id s2）: title"
run "NG: id の重なり"                                    ngs dup-id "NG: 3枚目（id s2）: id s2 が 2枚目と重なっている"
run "NG: id の書式（大文字・空白）"                     ngs bad-id "英小文字で始まる"
run "NG: dur の数が拍と違う"                             ngs bad-dur "dur の数（2）が beats=4"
run "NG: 章の番号が範囲の外"                             ngs bad-ch "ch=5 は章の外"
run "NG: 知らない場面のキー"                             ngs bad-key "知らないキー beat"
run "NG: talk の数が拍と違う"                            ngs bad-talk "talk の数（1）が beats=4"
run "NG: data-in が拍の外"                               ngs bad-in 'data-in="7" は拍の外'
run "NG: html の閉じ忘れ"                                ngs open-div "<div> が閉じていない"
run "NG: html に <section>"                              ngs has-section "<section> は書けない"
run "NG: hook に MS.hook を書いた"                       ngs hook-wrapped "本体だけを書く"
run "NG: hook が別の場面（#s2）を引く"                   ngs hook-other "別の場面（id s2）を引いている"
run "NG: hook が別の場面の要素（#val）を引く"            ngs hook-other-el "別の場面（id s3）の要素 #val"
run "NG: hook の文法の誤り"                              ngs hook-syntax "hook の文法の誤り"
run "NG: 場面の style が #<id> で始まらない"             ngs style-leak "#s1 で始まっていない"
run "NG: 要素の id が場面をまたいで重なる"               ngs dup-el 'id="tb" が場面'
asm_ok() { $PY - "$SK/scripts" "$TMP/j/ok.json" <<'PYEOF'
import json, re, sys
sys.path.insert(0, sys.argv[1])
import deckjson
h = deckjson.assemble(json.load(open(sys.argv[2])))
assert re.search(r'<section class="scene" id="s3" data-beats="3" data-ch="1" data-title="値がマスへ飛ぶ" data-term="表" data-dur="4200,5600,6000">', h)
assert '<aside class="notes"><p>カードと空の表。</p>' in h
m = re.search(r'id="ms-scenes">(.*?)</script>', h, re.S)
code = json.loads(m.group(1))
assert list(code) == ['s3'] and 'x.fly(' in code['s3']['hook'] and 'MS.hook' not in h and h.count('<section') == 3
PYEOF
}
# Codex のレビューで出た抜け道（tests/repro.py）。検査で NG になる／組み立てても HTML の境界・他の場面に影響しない
for c in $($PY "$SK/tests/repro.py"); do
  run "抜け道 $c: NG になる"  $PY "$SK/tests/repro.py" "$TMP/j/ok.json" "$c"
done
for c in hook-breakout script-breakout style-end-scene style-end-shared html-upper-script stage-script; do
  run "抜け道 $c: 組み立てても </script が増えず leaked が出ない" $PY "$SK/tests/repro.py" "$TMP/j/ok.json" --assemble "$c"
done
title_in_comment() { $PY - "$SK/scripts" "$TMP/j/ok.json" <<'PYEOF'
import json, re, sys
sys.path.insert(0, sys.argv[1])
import deckjson
d = json.load(open(sys.argv[2]))
d["scenes"][0]["title"] = "x */ body{display:none} /*"
h = deckjson.assemble(d)
assert "display:none" not in "".join(re.findall(r"<style>(.*?)</style>", h, re.S)), "題が CSS のコメントから抜けた"
assert deckjson.validate(d).ng == [], deckjson.validate(d).ng   # 題は属性に escape して入るだけなので、台本としては通る
PYEOF
}
run "抜け道: 場面の題を CSS のコメントに入れない（*/ … /* で抜けない）" title_in_comment
shared_script_escape() { $PY - "$SK/scripts" "$TMP/j/ok.json" <<'PYEOF'
import json, re, sys
sys.path.insert(0, sys.argv[1])
import deckjson
d = json.load(open(sys.argv[2]))
d["script"] = 'const RE = /<!--/u; const T = "</SCRIPT><b>"; /* <!-- */'
h = deckjson.assemble(d)
assert len(re.findall(r"<script\b", h, re.I)) == len(re.findall(r"</script", h, re.I))
assert json.loads(re.search(r'id="ms-shared">(.*?)</script>', h, re.S).group(1)) == d["script"]   # 共通の script は書いたとおりの文字で入る
assert deckjson.validate(d).ng == []
PYEOF
}
run "共通の script は JSON に入り、</script・<!-- を含む正しい JS を壊さない" shared_script_escape
roundtrip_new() { $PY - "$SK/scripts" "$TMP/j/ok.json" "$TMP/j/new" <<'PYEOF'
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import deckjson, split
d = json.load(open(sys.argv[2]))
d["script"] = "const G = 7;"
d["scenes"][2]["script"] = "const LOCAL = 1;"
d["scenes"][2]["hook"] = "document.body.dataset.g = String(G + LOCAL);"
out = pathlib.Path(sys.argv[3]); out.mkdir(exist_ok=True)
(out / "deck.html").write_text(deckjson.assemble(d), encoding="utf-8")
back = split.split((out / "deck.html").read_text(encoding="utf-8"))
t = deckjson.text_of
b3 = back["scenes"][2]
assert t(b3["hook"]) == d["scenes"][2]["hook"] and t(b3["script"]) == "const LOCAL = 1;", b3
assert t(back["script"]) == "const G = 7;"
again = deckjson.assemble(back)
assert split.split(again)["scenes"][2]["hook"] == b3["hook"]   # もう一度組んで分けても同じ
assert deckjson.validate(back).ng == []
PYEOF
}
run "split: 台本 JSON から組んだ形（ms-scenes・ms-shared）も読み、組み直して分けても hook・script が消えない" roundtrip_new
run "組み立て: <section> の属性・notes が雛形と同じで、hook は ms-scenes の JSON に入る" asm_ok
# 場面ごとの絵のキャッシュ（docs/adr/0004）。鍵の試験は Chrome を使わない
run "キャッシュの鍵: 場面・前の場面の term・拍・待ち・テーマ・ロゴ・共通のスタイルが変われば鍵が変わり、ほかは変わらない" $PY "$SK/tests/cache_check.py" "$TMP/j/ok.json"
# deck --no-shoot は Chrome を起動しない（playwright を入れない状態にして走らせる）
no_shoot() {
  $PY - "$MS" "$TMP/j/ok.json" "$TMP/j/ns" <<'PYEOF' > "$TMP/j/ns.out" 2>/dev/null
import runpy, sys
sys.modules["playwright"] = None   # 起動しようとすれば落ちる。入っていない扱い
ms, script, out = sys.argv[1:4]
sys.argv = [ms, "deck", "--script", script, "--out", out, "--logo", "none", "--no-shoot"]
runpy.run_path(ms, run_name="__main__")
PYEOF
  grep -q "^pictures: not-taken" "$TMP/j/ns.out" && [ -f "$TMP/j/ns/index.html" ] && [ ! -e "$TMP/j/ns/slides" ] && [ ! -e "$TMP/j/ns/check" ] \
    && $PY -c "import json; r=json.load(open('$TMP/j/ns/build.json')); assert r['pictures']=={'taken': False, 'reason': 'no-shoot'} and r['check'] is None and [x['id'] for x in r['slides']]==['s1','s2','s3']"
}
run "deck --no-shoot: Chrome なしで index.html を組み、stdout に pictures: not-taken・絵は作らない" no_shoot
if $PY -c "import playwright" 2>/dev/null; then
  run "check: 形・組み立て・全拍の検査を通ると OK"      bash -c "$PY '$MS' check --script '$TMP/j/ok.json' --wait 1600 2>&1 | tail -1 | grep -qx OK"
  run "deck: 出力のフォルダに全部そろう（3場面）"      bash -c "$PY '$MS' deck --script '$TMP/j/ok.json' --out '$TMP/j/out' --wait 1600 >/dev/null 2>&1 && cd '$TMP/j/out' && ls index.html deck.html build.json deck-info.json inspect.md thumbs.png slides/slide-01.png slides/slide-02.png slides/slide-03.png >/dev/null && [ ! -e slides/slide-04.png ]"
  run "deck: build.json に場面ごとの id・題・拍・絵のパス" bash -c "$PY -c \"import json; r=json.load(open('$TMP/j/out/build.json')); s=r['slides']; assert [x['id'] for x in s]==['s1','s2','s3'] and [x['beats'] for x in s]==[2,4,3] and s[2]['page']=='slides/slide-03.png' and r['check']=={'ng':0,'warnings':0}\""
  run "deck: 組んだ index.html は外へ取りに行かず、雛形と同じ拍の数（9）" bash -c "! grep -Eq '(src|href)=\"https?://' '$TMP/j/out/index.html' && grep -q '拍 9' '$TMP/j/out/check/inspect.md' || grep -q '拍 9 ' '$TMP/j/out/inspect.md'"
  # 場面ごとのキャッシュ: 同じ台本の 2 回目は全部使い回し・1 場面だけ変えたらその場面だけ撮る・拍の絵と一覧は 1 回目と同じ
  shots() { grep '^shots:' "$1" | tr -d '\n'; }
  $PY "$MS" deck --script "$TMP/j/ok.json" --out "$TMP/j/c1" --wait 600 > "$TMP/j/c1.out" 2>/dev/null || true
  $PY "$MS" deck --script "$TMP/j/ok.json" --out "$TMP/j/c2" --wait 600 > "$TMP/j/c2.out" 2>/dev/null || true
  mut touched 'd["scenes"][1]["talk"][0]+="。"'
  $PY "$MS" deck --script "$TMP/j/touched.json" --out "$TMP/j/c3" --wait 600 > "$TMP/j/c3.out" 2>/dev/null || true
  $PY "$MS" deck --script "$TMP/j/ok.json" --out "$TMP/j/c4" --wait 600 --no-cache > "$TMP/j/c4.out" 2>/dev/null || true
  run "キャッシュ: 1 回目は 3 場面とも撮る"                       bash -c "[ \"\$(grep '^shots:' '$TMP/j/c1.out')\" = 'shots: 撮った場面 3・キャッシュから 0' ]"
  run "キャッシュ: 変えない 2 回目は 3 場面ともキャッシュから"     bash -c "[ \"\$(grep '^shots:' '$TMP/j/c2.out')\" = 'shots: 撮った場面 0・キャッシュから 3' ]"
  run "キャッシュ: 2 回目の出力は 1 回目と同じ並び（絵・一覧・記録）と同じ中身" bash -c "cd '$TMP/j' && (cd c1 && find . -type f | sort) > l1 && (cd c2 && find . -type f | sort) > l2 && diff -q l1 l2 && for f in slides/slide-01.png slides/slide-03.png thumbs.png check/beats/s02-b3.png; do cmp -s c1/\$f c2/\$f || exit 1; done && grep -c . c2/inspect.md >/dev/null && diff -q <(grep -v '^- 本体' c1/inspect.md) <(grep -v '^- 本体' c2/inspect.md)"
  run "キャッシュ: 2 回目の一覧のサムネイルは 1 回目と同じ"        bash -c "cmp -s <(grep -o 'id=\"ms-thumbs\">.*' '$TMP/j/c1/index.html') <(grep -o 'id=\"ms-thumbs\">.*' '$TMP/j/c2/index.html')"
  run "キャッシュ: 場面を 1 つ変えたらその場面だけ撮る"            bash -c "[ \"\$(grep '^shots:' '$TMP/j/c3.out')\" = 'shots: 撮った場面 1・キャッシュから 2' ]"
  run "キャッシュ: 変えなかった場面の絵は前のまま"                 bash -c "cmp -s '$TMP/j/c1/slides/slide-01.png' '$TMP/j/c3/slides/slide-01.png' && cmp -s '$TMP/j/c1/slides/slide-03.png' '$TMP/j/c3/slides/slide-03.png'"
  run "キャッシュ: --no-cache は全部撮り直す"                      bash -c "[ \"\$(grep '^shots:' '$TMP/j/c4.out')\" = 'shots: 撮った場面 3・キャッシュから 0' ]"
  run "キャッシュ: 待ちが違えば別の鍵（全部撮る）"                  bash -c "$PY '$MS' deck --script '$TMP/j/ok.json' --out '$TMP/j/c5' --wait 700 2>/dev/null | grep -qx 'shots: 撮った場面 3・キャッシュから 0'"
  run "キャッシュ: build-only は前の絵に触らず、場面のサムネイルは前の一覧を残す" bash -c "cd '$TMP/j' && cp c3/slides/slide-02.png before.png && $PY '$MS' deck --script '$TMP/j/ok.json' --out c3 --no-shoot 2>/dev/null | grep -q '^pictures: not-taken' && cmp -s before.png c3/slides/slide-02.png && [ -d c3/check ] && grep -c 'data:image/webp' c3/index.html | grep -qv '^0'"
  # 見た目の崩れ（はみ出し）と JS のエラーは NG の行になる
  mut overflow 'd["scenes"][0]["style"].append("#s1 .lead{position:absolute;left:1800px;top:500px;width:400px}")'
  mut js-error 'd["scenes"][2]["hook"]="throw new Error(\"boom\");"'
  ngc() { set +e; out=$($PY "$MS" check --script "$TMP/j/$1.json" --wait 1000 2>&1); r=$?; set -e; [ "$r" -eq 2 ] && echo "$out" | grep -q "$2"; }
  run "NG: 舞台の外へはみ出す（1枚目）"                 ngc overflow "NG: 1枚目（id s1・拍2）: 舞台（1920×1080）の外にはみ出している"
  run "NG: hook が投げた JS のエラー（3枚目）"          ngc js-error "NG: 3枚目（id s3・拍.*JS のエラー"
  hook_leak() { $PY - "$SK/scripts" "$TMP/j/ok.json" "$TMP/j/leak" <<'PYEOF'
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import deckjson
d = json.load(open(sys.argv[2]))
d["scenes"][2]["hook"] = "}, document.body.dataset.leaked=1, (b,x)=>{"
out = pathlib.Path(sys.argv[3]); out.mkdir(exist_ok=True)
(out / "deck.html").write_text(deckjson.assemble(d), encoding="utf-8")
PYEOF
    $PY "$SK/scripts/build.py" "$TMP/j/leak/deck.html" --out "$TMP/j/leak/index.html" --no-thumbs --logo none >/dev/null && $PY "$SK/tests/leak_check.py" "$TMP/j/leak/index.html"; }
  run "抜け道: hook の本体から抜ける台本は、組んでもその場面だけが落ちる（leaked なし・他の場面は動く）" hook_leak
  shared_runs() { $PY "$SK/scripts/build.py" "$TMP/j/new/deck.html" --out "$TMP/j/new/index.html" --no-thumbs --logo none >/dev/null && $PY "$SK/tests/eval_check.py" "$TMP/j/new/index.html" "document.body.dataset.g" 8; }
  run "共通の script の const は、場面の script・hook から見える（実機で G + LOCAL = 8）" shared_runs
  # 見本の往復（見本は固有名詞を含むのでリポジトリには入れない。MOTION_SLIDE_SAMPLES に <名前>/deck.html を並べたフォルダを渡すと走る）
  for d in ${MOTION_SLIDE_SAMPLES:+"$MOTION_SLIDE_SAMPLES"/*/}; do
    n="$(basename "$d")"; [ -f "$d/deck.html" ] || continue
    run "往復 $n: 分けて組み直して NG 0・場面と拍が同じ" bash -c "$PY '$SK/scripts/split.py' '$d/deck.html' --out '$TMP/r-$n.json' >/dev/null && $PY '$MS' deck --script '$TMP/r-$n.json' --out '$TMP/r-$n' >/dev/null 2>&1 && $PY -c \"import json,re; o=open('$d/deck.html').read(); r=json.load(open('$TMP/r-$n/build.json')); assert r['scenes']==len(re.findall(r'<section class=.scene', o)) and r['beats']==sum(map(int,re.findall(r'data-beats=.(\\d+)', o))) and r['check']['ng']==0\""
  done
fi

echo
echo "pass: $pass / fail: $fail"
[ "$fail" -eq 0 ]
