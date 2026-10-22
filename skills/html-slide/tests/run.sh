#!/usr/bin/env bash
# html-slide のテスト。これ1本で全部走る。
#
# 方針:
#   - ネットワークに依存しない。HTML は fixtures/ と examples/ の手元のもの
#   - テストするのは純ロジック（台本の検証・型の段の数・HTML の決まりの属性・変換・照合）
#   - 字体は PowerPoint 同梱の游ゴシックを読む（PowerPoint が入っている機体で走らせる）
#   - 失敗したら即座に非ゼロで落ちる
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # スキルの中に __pycache__ を残さない（配る物・リンク先）

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export FIXTURES="$DIR/fixtures"
# 雛形の XXXXXX は GNU の mktemp（Windows の Git Bash）が要る。`-t <名前>` は mac でしか通らない
TMPBASE="${TMPDIR:-/tmp}"   # mac の TMPDIR は / で終わる。// が残ると、パスを見比べる試験がずれる
SCRATCH="$(mktemp -d "${TMPBASE%/}/html-slide-tests.XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT
# 試験が tempfile で作る一時フォルダもここへ寄せ、落ちた試験の分も最後にまとめて消す
mkdir -p "$SCRATCH/tmp" "$SCRATCH/html-slide-cache" "$SCRATCH/quick-slide-home"
export TMPDIR="$SCRATCH/tmp"
# 描いた絵の置き場（キャッシュ・ADR 0014）は使い捨てのフォルダへ。実の ~/Library/Caches に書かない
export HTML_SLIDE_CACHE="$SCRATCH/html-slide-cache"
# 設定も実の置き場（~/.quick-slide）を読まない（places.py）
export QUICK_SLIDE_HOME="$SCRATCH/quick-slide-home"

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

echo "=== html-slide tests ==="

# --- renderer ---
render() { run "$1" python3 "$DIR/test_render.py" "$2"; }
render "決まりに沿った HTML: 違反なし・PPTX・段/効果/ノートの数・切り替え・!! の名前・文字と色替え run・画像の切り抜き" contract_ok
render "違反入りの HTML: transform・はみ出し・::before・section の外・ノートのずれを拾い、PPTX を書かずに rc=2" contract_bad
render "timing の XML: 整形式・自動1＋クリック2・nodeType・出方の番号・wipe の向き・data-delay・段の番号を詰める" timing_xml
render "文字の枠: 1行は折り返さず真ん中・複数行は幅で折り返し・行送り pt 固定・余白0・上揃え・ぶら下げなし・文節折りだけ改行を固定" text_frames
render "表の align: 折り返す右寄せ・中央寄せのセルの段落の揃えと右端・中心（改行を固定する経路・固定しない経路とも）が HTML と合う" table_align
render "verify: ノートの［クリック］とクリックの数のずれを rc=2" verify_mismatch
render "verify --odp: LibreOffice の読み取りの効果の数が report と一致（soffice が無ければ飛ばす）" verify_odp
render "from_video: 1文=1クリック・型の出すものは自動・html-video の印と知らない印・章カードの前後はフェード" from_video_rules
render "kpi: 増減の札の塗り・文字色と group のカードの面・枠・札が PPTX に写る・3枚は高さいっぱい" kpi_tone_group
render "kpi: 増減の札の枠が PPTX に写る・札あり／なしが混ざる行で数字の上端がそろう（3枚・4枚・強い look）" kpi_chip_align
render "table: セルの tone の地・文字色・薄い行の文字色・凡例の見本（角丸・塗り・枠）と文字が PPTX に写る" table_tone_dim_legend
render "table: summary のカード（地・枠・札・増減の札・文字）と表・凡例が PPTX に写り、カードは表の上で同じ幅・段は2クリック" table_summary
render "table: summary で札（group）のあるカードとないカードが混ざる帯でも、数字・増減の札の上端がそろい、帯は上限内で表と重ならない" table_summary_chip_mix
render "bar-chart: 棒の tone の塗り・値の文字色・値 0 の札が PPTX に写る・灰の棒はそのまま" bar_tone
render "r1-1 兄弟の z-index を描画順に並べる・flex の order は rc=2" r1_zorder
render "r1-2 縦の動画は rc=2（縦の動画は未対応）" r1_portrait
render "r1-3 自動で折り返した所の単語の間の空白を残す・改行を固定するときは行末の空白を落とす" r1_wrap_spaces
render "r1-4 塗りのある開いた SVG パスも塗る（塗りと線の閉じ方を分ける）" r1_open_fill
render "r1-5 位置指定の tspan があっても親 text の直の文字を拾う" r1_svg_tspan
render "r1-6 文字の中の inline の data-step は rc=2・contract.md に書く" r1_inline_step
render "r1-7 隠れた inline の文字は出さない・半透明は色に掛ける" r1_hidden_inline
render "r1-8 section 自身の禁止 CSS も rc=2" r1_section_checks
render "r1-9 show の対象の中の単位は対象と同じクリックで出る" r1_nested_show
render "r2-2 重ね順の文脈を作らない親の中の z-index は rc=2（section の直下と文脈を作る親の中は通す）" r2_zindex_context
render "r2-8 color: transparent の文字は出さない" r2_transparent_text
render "r3-5 img の alt を PPTX の代替テキスト（descr）に・切り抜きは小数4桁" r3_img_alt

# --- deck ---
deck() { run "$1" python3 "$DIR/test_deck.py" "$2"; }
deck "見本の台本が通る・全型を使う・talk の数＝段の数＋1" sample_ok
deck "型ごとの段の数の既定（deck-format.md の表）" default_clicks
deck "HTML の決まり: transition・ノートの［クリック］＝段の数・fx の語彙・入れ子なし・keep" contract_attrs
deck "許可リストの外の CSS・外部 URL・スクリプトを出さない" no_forbidden_css
deck "handout: 全部 step 0・delay で順番・fade・［クリック］なし" handout
deck "steps: false の1枚は step 0（talk は1つ）" steps_false
deck "talk の数が段の数と合わなければ rc=2・省けば空" talk_mismatch
deck "未知の型は rc=2" unknown_type
deck "未知のキーは rc=2" unknown_key
deck "必須の欠け・数の範囲・値の型は rc=2" missing_required
deck "写真の URL は rc=2" photo_url
deck "photo の全面の hide_text は画像だけの扉絵（見出しはページの名前・出典は右下）・左右には使えない" photo_image_only
deck "存在しない画像は rc=2" missing_image
deck "2行に入らない見出しは rc=2" too_long
deck "テーマ 3種が通る・読めない色と無い名前は rc=2" theme
deck "テーマの主役の色と差し色: 骨格は primary・強調は accent・古い書き方は rc=2" theme_primary_accent
deck "文字の大きさの決め方（縮小・tidy・折れない語）" fit
deck "新しい型（summary・kpi・line-chart・team・matrix）の検証は rc=2" new_types_errors
deck "折れ線: 強める線は accent・点の数＝時点の数・大きい値ほど上" line_chart_svg
deck "Codex r2 #3 縮めた字の大きさを font-size に書く（KPI・表紙の日付ほか）" fit_size_applied
deck "Codex r2 #4 グラフの右の要点は内幅 412px で測る（棒・折れ線）" callout_inner_width
deck "Codex r2 #5 値が全部 0 の棒グラフも max なしで組める" zero_bars
deck "Codex r2 #6 目次の source を出す" agenda_source
deck "Codex r2 #7 全型の文字を HTML に埋めるときエスケープする" escape_all_types
deck "画像の書き方 {path, prompt, focus, alt}: focus は object-position・alt・誤りは rc=2" image_object
deck "画像の既定の置き場 images/s{NN}-{枠の名前}.png" image_default_path
deck "画像が無い: 既定は rc=2・placeholder なら仮の枠＋警告（H-3 に掛かる文言）" image_missing_placeholder
deck "image_slots: 全部の枠・推奨の大きさ・prompt_full（文字を描かない・重ねる側を空ける）" image_slots
deck "画像の見本: 16枚・クリック18・警告0・全部の枠に prompt と画像・画像の型を全部使う" image_sample
deck "全面の表紙・章扉（hero）: 幕・文字を置く側・明暗・誤りは rc=2" hero
deck "中身が少ないときに大きく組む・並ぶ項目の題と本文の大きさをそろえる・詰まったページと画像のあるページは元のまま" grow
deck "grow を広げた型（表・グラフ・指標・大きな数字・放射・式・対・手順・2×2・体制・目次・引用・お知らせ）: 少ないときは段を上る・詰まったページは元のまま" grow_more_types
deck "図解の型（式・放射・対）・アイコン・締めの帯: 大きな見出し・帯は最後の段と一緒・中身は帯の上で止まる・誤りは rc=2" diagram_types
deck "画像なしの表紙・章扉（graphic）: 図形は層の中・図柄の既定は題で決まる・クリックは増やさない・誤りは rc=2" graphic
deck "写真の上の文字の幕が薄すぎれば rc=2（白い写真・黒い写真を仮定）" overlay_contrast
deck "focus: 1〜2語・% に直す・px は rc=2" focus_words
deck "写真の幅（split）と箇条書きの写真の側（side）" split_side
deck "図は切らずに見せる・写真の並びは1枚ずつ・カードの写真は全部か無しか" figure_gallery_cards
deck "引用の写真: full は写真の上（帯・ページ番号なし）・portrait は丸い写真" quote_layouts
deck "小さい画像は警告（rc は変えない）" small_image_warning
deck "Codex r3 #2 図のキャプションは focus の縦位置で出した画像の下端の下に置く" r3_figure_caption
deck "Codex r3 #3 全面の写真の引用でも source を出す" r3_quote_full_source
deck "Codex r3 #4 写真を使わない資料は幕の検査で止めない（使う置き方だけ）" r3_overlay_only_used
deck "Codex r3 #6 切り抜きの説明は在る画像の実寸で・無ければ未定" r3_crop_actual
deck "カタログ: 全部の型と置き方を網羅・deck を通る・名前が重ならない・--only の絞り込み" catalog_script
deck "カタログの一覧画像: 4列×4段・収まらなければ複数枚" catalog_sheets
deck "画像の置き方6つ（帯・写真つきの締め・コラージュ・写真つきの比較・写真の帯＋数字・注釈）" six_image_layouts
deck "注釈の印の位置（cover は枠・contain は描いた画像の %）と、印と説明が同じクリック" annotate_marks
deck "台本の改行は <br>・行数に数える" line_breaks
deck "Codex r4 #2・#3 注釈6件と画像つき KPI の2行見出しでも、件数と高さから字を縮めて入れる" r4_annotate_kpi_fit
deck "Codex r4 #4・#5 改行は全部の文字の欄で <br>・強調は改行をまたげる" r4_line_breaks_everywhere
deck "kpi: 増減の札の tone（trend から決まる・上書き・誤りは rc=2）・group（札は必ず出る・同じ group は同じ面・一部だけ・accent と同時は rc=2）・3枚の高さ" kpi_tone_group
deck "kpi: 同じ役（label・value・unit・delta・note）の文字は長短を混ぜても全部のカードでそろい、高さは上限を守る" kpi_role_sizes
deck "bar-chart: 棒の tone（5段＋neutral・誤りは rc=2・highlight と同時・値 0 の札・全部 0・note/callout）・tone なしと neutral は旧台本と同じ" bar_tone
deck "group の a・b・c はテーマを変えても同じ（c は固定の青）・重ならず文字 4.5:1・枠 3:1" group_c_fixed
deck "kpi: 強い look と全テーマ・主色違いでも増減の札の文字×札の地は 4.5:1" kpi_tone_chip_strong
deck "kpi: look（強める箱・線の面）に増減の札と group の面が潰されない" kpi_look_priority
deck "kpi: tone・group を書かない旧台本は札も色も付かず、4枚は高さを固定しない" kpi_old_scripts_same
deck "table: セルの tone・dim_rows・legend（class・凡例の位置と順・書かなければ何も足さない・誤りは位置付きで rc=2・最小の行の高さ）" table_tone_dim_legend
deck "table: tone の文字×地 4.5:1・凡例の枠×地 3:1・薄い行の muted×地 4.5:1（全テーマ）・壊せば contrast_problems が拾う" table_tone_contrast_all_themes
deck "table: summary（1〜4枚・幅は等分・段1と段2・accent/badge と範囲外は rc=2・文字の段が下りる・高さ不足は rc=2・旧台本は今のまま）" table_summary
deck "table: summary のあるページは2クリック（属性・ノート・handout・steps: false・talk の照合）" table_summary_clicks
deck "table: summary を大きく組む（小さな表）と数字は64px以下・label/delta は36px以下・note は28px以下・帯は384px以下で表と重ならない" table_summary_big
deck "outline と role は読み飛ばし、警告も出さない（ADR 0004）" outline_role_ignored
deck "スタイルの YAML の誤り（知らないキー・型・変化形・値・動き・無いファイル）は rc=2・組み込みと雛形は読める" style_validation
deck "visual: 画像のあるページだけ hero／台本が勝つ／split・collage の条件／動きの既定（1枚目・handout は fade）" style_apply
deck "standard（既定）は今までの見本に何も足さない" style_standard_keeps_samples
deck "見た目の調子（look）: 誤りは rc=2・visual の CSS（影・帯・角丸・強調の塗り・主張の全面）・standard は空" style_look
deck "Codex r4 #1・#6 写真つきの締め: 連絡先が見出しに重なれば rc=2・出典は左の列の幅" r4_closing_image_guards
deck "ロゴの大きさ: 329×44 の枠に縦横比を保って縮めるだけ・透明な余白は切る・小さければ警告" logo_geometry
deck "ロゴのページごとの扱い（Chrome）: 右下が写真なら左下（文字の左端）・両方だめなら置かない・地はその場所の色・番号はロゴの左" logo_pages
deck "ロゴの位置（--logo-position・Chrome）: 左下 → 右下・右上 → 右下・左下なら下の文字をロゴの右へ・角ごとに部品名・左上は止める" logo_positions
deck "ページ番号は既定でオフ・出すときは中央下（下の文字はその手前まで）" page_numbers_default_off
deck "透かし（--watermark）: 全ページの中央下の端・CSS と数字がそろう・PPTX では選べない・動かせない !!watermark・付けなければ足さない・改行は止める" watermark
deck "DADS: トークンの正本（skills/dads/tokens/slide.json）と定数が同じ（文字の段・行高・余白・格子・角丸・影・色）" dads_tokens
deck "DADS: 色の役（primary・mark・decor・strong・rule・bar・faint・意味の色）が3つのテーマとブランドの色で 4.5:1・3:1 を満たす" dads_palette
deck "DADS: 組んだ HTML の文字は段・行高は段・角丸は段・影は elevation-2 だけ・雛形の余白は 8 の倍数" dads_scale_rhythm
deck "字体: 既定は Noto Sans JP・ファイルが分からなければ游ゴシックに落ちる（警告にしない）・字体の上下の幅を 1em に縮める" font_default_and_fallback
deck "下の文字は1行（22px → 20px）・入らなければ透かしがあるとき rc=2・無ければ警告" footer_one_line
deck "表の align: 省略は今どおり（1列目左・残り中央）・列ごとの揃え・highlight と併用・数の違い・未知の値・配列でない値は rc=2" table_align
deck "型の構造: 強める項目は色だけで示さない（札 badge・太い輪郭・縦の罫と太字・二重の丸・象限の枠）・badge は強める項目にだけ" dads_emphasis_not_by_color
deck "matrix: 名前は点・ほかの名前・区切り線と重ならないようにずらす・点どうしが重なれば rc=2" dads_matrix_overlap
deck "line-chart: 点の形と線種で系列を区別・線の端に名前・入らなければ凡例に線種と点の形も写す" dads_line_chart_marks
deck "DOM 順（hub は中心 → 周り・pairs は札 → 文）・pairs の札は語とアイコン・takeaway はアイコンつき" dads_reading_order
deck "新しい型（notice・deflist・steps）: 種類・状態を形と語で示す・クリックは枠・行・段ごと・誤りは rc=2" dads_new_types
deck "モーフは退役（ADR 0023）: 切り替えは fade・none だけ・台本とスタイルの morph は fade で描いて警告・PPTX にモーフを書かない" no_morph
deck "退役の仕組み（RETIRED_TYPES・RETIRED_LOOKS）: 描いて警告・deck-format に載せない・surface: white は line と同じ" retired

# --- 画像の枠を Codex で埋める（ADR 0011・偽物の codex） ---
# --- 出力（ADR 0012） ---
outputs() { run "$1" python3 "$DIR/test_outputs.py" "$2"; }
outputs "--format: 省けば4つ・決まった順・誤りは止める" formats
outputs "サムネイル: 16ページまでは thumbs.png・超えたら番号つき・前の回の分を消す・見出しの選び方・減ったページの画像を消す" thumbs
outputs "PDF 用の写し: 不透明な PNG は JPEG・透過のある絵は PNG のまま・相対パスは file: URL" print_html
outputs "deck（Chrome）: 既定で4つ・--format で PPTX を作らない・減ったページの画像を消す・PDF は PPTX と同じ大きさ" deck_formats
outputs "--parts と assemble: テーマ違いの 2 回のページを混ぜて PPTX・PDF を組む・ページの中身は元のまま・読めない PDF は止める（ADR 0024）" assemble

fill() { run "$1" python3 "$DIR/test_fill.py" "$2"; }
fill "fill: まだ絵の無い生成の枠だけ描く・持ち込みは触らない・2回目は描かない・API キーを渡さない・ページ番号を指示に入れない" fill_generates_missing
fill "並べ替え・見出しの変更では描き直さない・画風を変えると描き直し、戻すと描かない・visual の既定は photo" reorder_and_look
fill "持ち込みの絵が無ければ描く前に rc=2" brought_missing_stops
fill "_characters はそのページの最初の生成の枠に（透過は白に）・枠が無ければ警告・キャラの絵が変われば描き直す" characters_to_first_slot
fill "Codex の認証が失効していれば rc=4" auth_dead_is_rc4
fill "deck は先に描いて埋める・描けなければ仮の枠で組んで rc=1・--no-fill は描かない・check は描く前でも通る" deck_fills_and_falls_back
fill "revise: 微修正は今の絵を土台に・--remake は添えない・前の絵を残す・鍵は元のまま・持ち込みは直さない" revise
fill "描いた絵はキャッシュの区画（台本のフォルダごと）・assets/ はハードリンク・前の置き場（images/gen-）は写して使い元は残す（ADR 0014）" cache_area
fill "スタイルの image_look・expects_images（visual は表紙に画像が無ければ警告）" style_image_look

# --- 既存の PPTX の取り込み（ADR 0025） ---
imp() { run "$1" python3 "$DIR/test_import.py" "$2"; }
imp "取り込みの台本: html 型・見出し・ノート・テーマ・画像は media/・非表示とスライドの外を報告・切り替え" structure
imp "色と文字: 塗りと文字の色・pt × 2 px・字体の名前は単引用符・回した文字の箱" colors_and_text
imp "4:3 は中央に置いて左右を背景で埋める" geometry_4x3
imp "アニメーション: 段落ごと・自動の段 0・ワイプの向き・同時の遅れ・ホイール・終了は写さず報告" animations
imp "エンジンの html 型: 画像を assets/ へ・段を数える・handout は段を外す・script・on*・url()・a・URL の画像・背景の url() は rc=2" engine_html_type
imp "deck（Chrome）: 取り込んだ台本が rc=0・クリックの数・取り込みの印・回した文字の rot・ノート" deck_end_to_end
imp "PPTX でないもの・外へ出る部品の名前は rc=2" bad_files
imp "XML の実体参照を解かない" xml_entities
imp "PNG の控えの無い SVG の画像は Chrome で描いて PNG に（同じものは 1 つ・回転・ネットへ読みに行かない）" svg_only_image

imp "EMF: 画像枠で変換・意図した余白とクロップ・通常の PNG を保つ（LibreOffice があるとき実変換）" metafile_image

# --- Node のモジュールと Chrome の在りか（ADR 0016） ---
browser() { run "$1" python3 "$DIR/test_browser.py" "$2"; }
browser "在りかの順: 環境変数 → 設定 → Quick Slide の置き場 → 前からの置き場・環境変数と設定は無くても落ちない" order
browser "どこにも無ければ入れる先（Quick Slide の置き場）を無いと言う・無い Chrome は渡さない" nothing_installed
browser "Chrome は入れたフォルダの一番新しい版（数字で比べる）" newest_version
browser "python3 browser.py は JSON 1 行" cli_json
browser "足りなければ check・deck は描く前に rc=4・入れ方を言う・Codex を呼ばない" rc4_before_drawing

echo
echo "pass: $pass / fail: $fail"
[ "$fail" -eq 0 ]
