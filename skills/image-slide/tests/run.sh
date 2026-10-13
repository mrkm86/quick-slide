#!/usr/bin/env bash
# image-slide のテスト。これ1本で全部走る。
#
# 方針:
#   - 本物の Codex は呼ばない（サブスク枠を減らさない）。偽物の codex（fake_codex.py）と偽の実ホームで、
#     認証の確認が1回だけ・並列・失効で止まる・描き直し・枠切れの待ち・帯の検査・使い回しを確かめる
#   - 受け口が html-slide とずれていないか（型・キー・テーマ）を html-slide の定義と見比べる
#   - 失敗したら非ゼロで落ちる
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # スキルの中に __pycache__ を残さない（配る物・リンク先）

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$DIR/fake_codex.py"
# 雛形の XXXXXX は GNU の mktemp（Windows の Git Bash）が要る。`-t <名前>` は mac でしか通らない
TMPBASE="${TMPDIR:-/tmp}"   # mac の TMPDIR は / で終わる。// が残ると、パスを見比べる試験がずれる
SCRATCH="$(mktemp -d "${TMPBASE%/}/image-slide-tests.XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT
# 試験が tempfile で作る一時フォルダもここへ寄せ、落ちた試験の分も最後にまとめて消す
mkdir -p "$SCRATCH/tmp" "$SCRATCH/quick-slide-home" "$SCRATCH/image-slide-cache"
export TMPDIR="$SCRATCH/tmp"
# 実の置き場（~/.quick-slide・~/Library/Caches/quick-slide）を読まない・書かない
export QUICK_SLIDE_HOME="$SCRATCH/quick-slide-home"
export IMAGE_SLIDE_CACHE="$SCRATCH/image-slide-cache"

pass=0
fail=0

run() {   # run "テスト名" 関数名
  local name="$1"; shift
  if out=$(python3 "$DIR/test_logic.py" "$1" 2>&1); then
    echo "  ✅ $name"
    pass=$((pass + 1))
  else
    echo "  ❌ $name"
    echo "$out" | sed 's/^/     /'
    fail=$((fail + 1))
  fi
}

echo "=== image-slide tests ==="
echo "--- 受け口（html-slide と同じ台本）"
run "型・キー・帯の有無・テーマが html-slide と同じ／全部の型に描き方の言葉がある" same_types_as_html_slide
run "見本の台本が image-slide と html-slide の両方で通る" sample_passes_both
run "台本の誤り（一番外の知らないキー・知らない型・必須・URL・画像なし）は rc=2・codex を呼ばない" script_errors
run "描く指示: 中身と文脈は入る・talk とパスと _ のキーは入らない・帯は要るときだけ" prompt_content
run "画像の枠は html-slide と同じ置き場を見て参照画像にする・5枚を超えたら rc=2（Codex の上限）" image_refs
run "_characters: ページごとのキャラを名前つきで添える・枚数に数える・透過は白に合成して渡す" characters
run "--image-look: html-slide と同じ3語・同じ言い回し・指定したときだけ1行足す・鍵が変わる・Codex の呼び方の写しも同じ" image_look_same_as_html_slide
run "revise: 1枚だけ直す・微修正は今の絵を土台に添え、作り直しは添えない・前の絵を残す・直した絵を deck が使い回す" revise
echo "--- 構図は任せる"
run "構図は指定しない・スタイルの既定は visual・製品のレイアウトの棚は無い" composition_left_to_codex
echo "--- スタイル（--style）"
run "組み込み（standard・visual）・前の名前の案内・カスタムスタイルの YAML・誤りは rc=2・レイアウトのファイルに style は書かない" styles_builtin_and_custom
run "infographic: 5枚の台本が1枚に（exec 1回・区画・PPTX 1枚・ページ番号なし・ノートに全ページ）" infographic_one_page
run "infographic: 1枚目の _layout が効く・ほかのページの別レイアウトは rc=2・カスタムの one_page" infographic_layout_and_errors
run "templates/ のカスタムスタイル・カスタムレイアウトの雛形がそのまま読める" templates_load
run "テーマの主役の色と差し色（描く指示の使い分け・古い書き方は rc=2）" theme_primary_accent
echo "--- 持ち込みのレイアウト（--layout）"
run "描く指示: 構図・部品の文字・作風・構図の見本の番号・_layout の無いページは任せる・中身で鍵が変わる" custom_layout_prompt
run "check に出る・渡していない名前・無い部品・形の誤り・無いファイル・知らないキーは rc=2・html-slide は読み飛ばす" custom_layout_cli_and_errors
run "deck: 構図の見本は指定したページだけに添える・build.json と inspect.md に出る" custom_layout_deck
echo "--- 合成"
run "16:9 にそろえる（伸縮・左右を切る・地の色で足す）" normalize
run "帯の検査（白・物あり・無地の暗い帯）と、帯の上の文字の色" band_check
echo "--- 認証と並列（偽物の codex）"
run "並列で描く・認証の確認は1回・API キーを渡さない・隔離ホームは別々で消える・実 auth.json は不変" deck_parallel
run "出力: 既定で PPTX・PDF・ページの画像・サムネイル・--format で選ぶ・誤りは rc=2・減ったページの画像を消す・outputs.py は html-slide と同じ" formats_and_thumbs
run "同じ中身は描き直さない（codex も認証も呼ばない）・変えたページだけ・--redo" deck_cache_and_redo
run "--keep: 置いてある絵は鍵が変わっても描き直さない・鍵は前のまま・--redo が勝つ（ADR 0017）" deck_keep
run "描いた絵はキャッシュの区画・出力フォルダに raw/・base/ を残さない・前の形の raw/ は移して使い回す（ADR 0015）" cache_area
run "キャッシュの上限: 古い区画から消す・今の区画・少し前の区画・印の無いフォルダは消さない・0 は消さない・cache.py は html-slide と同じ" cache_trim
run "置き場: 根っこは QUICK_SLIDE_HOME（既定 ~/.quick-slide）・設定と出力は engines/<スキル>/・キャッシュは ~/Library/Caches/quick-slide/・環境変数と設定が優先・places.py と oscompat.py は html-slide と同じ" places
run "認証の失効で残りを止めて rc=4・PPTX を書かない" auth_dead_stops
run "並列で走っている最中の失効でも、ほかのワーカーを止めて隔離ホームを消す" auth_stops_running_workers
run "ログインしていない・auth.json が無いと rc=4" not_logged_in
run "auth --quick: codex を呼ばずに、本体とログインの記録だけを見る" quick_auth_looks_without_calling_codex
run "期限間近のトークンは実ホームで1回だけ更新する・日数だけでは更新しない（codex と同じ）・API キーのログインを見分ける" stale_token_refreshes_once
run "失敗は1回描き直す・描けなければ rc=1 で PPTX を書かない" retry_on_failure
run "枠切れ（429）は待って続ける" rate_limit_waits
run "帯が空いていなければ理由を添えて描き直す・直らなければ板を敷いて警告" band_retry_and_plate
run "ワーカーの中のトークンの更新を数えて警告する" worker_refresh_is_reported
echo "--- Codex レビュー r1 の指摘の再発防止"
run "並びの中の role・side は中身として残す／配置の指定は別に渡し、鍵にも入る" content_keeps_nested_and_layout
run "画像の枠の名前が html-slide と同じ（gallery は image1…）" image_slots_match_html_slide
run "描き直しに失敗しても、前の絵と鍵の組は崩れない" failed_redo_keeps_old_pair
run "準備の途中で落ちても、隔離ホーム（auth.json のコピー）を残さない" private_home_cleanup
run "壊れた auth.json・タイムゾーンの無い時刻・時間切れも rc=4 の扱い" auth_malformed_is_rc4
run "トークンはログ・build.json・画面に出さない" secrets_redacted
run "警告の中の語（token_revoked・429.png）を失効・枠切れと読み違えない" warning_words_not_misread
run "PPTX の帯の文字は確認用の絵と同じ大きさ（11pt）" pptx_text_size
echo "--- Codex レビュー r2 の指摘の再発防止"
run "止めたワーカーのログも、トークンを伏せてから写す" stopped_worker_logs_redacted
run "絵が返っていても失効が出たら止めて rc=4" auth_with_image_stops
run "壊れた鍵（書きかけの .json）のページは描き直す" broken_meta_redraws
run "ログの行の読み方: 時刻つきの ERROR・警告の中の語・429.png・--json の失敗のイベント" failure_lines_reading
run "読めない auth.json も rc=4" unreadable_auth_is_rc4
echo "--- ロゴ（html-slide と同じ規則・ADR 0008）"
run "ロゴの寸法と規則の数字が html-slide と同じ" logo_rules_match_html_slide
run "透明な余白を切る・縮めるだけ・右下 → 左下 → 置かない・1色でない所（ぼけ・グラデーション）には置かない・本文は帯の右下・--logo-position（左下 → 右下・右上 → 右下）" logo_place
run "deck: 表紙に帯を頼まない・ふさがった角には置かない・帯の板の上・地とロゴは別の部品・ページ番号は中央下・左下ならフッタはロゴの右・左上は止める" logo_deck
run "ページ番号は既定でオフ（page_numbers: true のときだけ）" page_numbers_default_off
run "透かし（--watermark）: 全ページの中央下の端・PPTX の絵にも焼く・下の行はフッタか番号があるときだけ上げる（透かしの有無では動かない）" watermark
run "visual は本文も全面に描かせる（帯を頼まない・描き直さない・板を敷かない）・standard は帯" visual_bleed
echo "--- PPTX"
run "PPTX: 16:9・全面の絵・代替テキスト・ロゴ・フッタ・ページ番号のフィールド・ノート" pptx_structure

echo "=== pass $pass / fail $fail ==="
[ "$fail" -eq 0 ]
