# 0021 型の構造を DADS に沿って直し、3つの型を足す

- 日付: 2026-10-10
- 状態: 採用

## 決めたこと

1. **色だけで意味を伝えている所を、形・札・語で補う**（DADS: 色だけに頼らない・アイコンは語と組む）。札は `chip()`（24B・高さ 40・角 full）
   - `cards`・`kpi`・`compare` の強調（`accent`）: 頭に札（台本の `badge`、無ければ「注目」）。`badge` は強調の項目にだけ書ける（rc=2）。全角 6 字まで
   - `flow`: 強める段（既定は最後）は濃い面＋ ink の 4px の輪郭。`badge` を書いたときだけ札
   - `table`: 強める列は色の地に加え、左右 4px の縦の罫（accent-mark）と太字
   - `timeline`: `current` は札「いま」と二重の丸（外 48・内 24）。札の行は全部の時期で同じ高さを空ける
   - `matrix`: 強める点は二重の輪。`focus` の象限は 4px の枠。名前は全部の点・置いた名前・区切り線を避け、点の横で上下 8px ずつずらして置く。置けなければ rc=2（点どうしの重なりも rc=2）。DOM 順は 軸 → 項目 → 象限名
   - `line-chart`: 系列ごとに点の形（丸・四角・ひし形）と線種（実線・破線・点線）を台本の順に変える。線の端に名前（と最後の値）を右に出す（名前は 2 行・幅 240 まで）。入らなければ凡例（線種と点の形も写す）。強めない線は ink・muted（rule 以上）
   - `hub`: DOM 順は 中心 → 周り。接続線は rule の 2px の実線
   - `pairs`: 札は NG/OK の語＋×・チェックのアイコン、白い面に error-2・success-2。札は箱の上に重ならず置く（DOM 順は 札 → 文。矢印は両者の間）
   - `takeaway`: 頭にアイコン（台本の `takeaway_icon`、無ければ info）。外周・角 full は前のまま
2. **新しい型**: `notice`（お知らせの枠）・`deflist`（用語と説明）・`steps`（手順の案内）。台本の書き方は deck-format.md
   - `notice`: `kind` は info・success・warning・error（既定の語「お知らせ」「完了」「注意」「重要」、`label` で替える）。白い面・意味の色の -1 の枠 2px と左の色帯 16・-2 のアイコンと語。warning は橙（枠の 3:1 に余裕がある）。1〜3 個・1 個ずつクリック。全部が入るまで文字を同じだけ段で下りる。`GROWABLE` に入れる。`takeaway` は置かない（枠そのものが言い切り）
   - `deflist`: 左に用語（太字）・右に説明、行の区切りは rule。用語の列は格子の 3 列（長ければ 4 列）。用語と説明は同じ大きさ。3〜8 行・行ごとにクリック。`GROWABLE`・`takeaway` 可
   - `steps`: 状態 done・current・todo を、丸の形（塗り・二重の輪・白に枠）・札（済・いま・これから）・線（済は実線・これからは破線）で示す。`current` か段ごとの `state` が無ければ rc=2（順番だけなら `flow`）。横 3〜5・縦 3〜6。段ごとにクリック。`takeaway` 可・`GROWABLE` には入れない（絶対配置）
3. **退役の仕組み**: `deck_types.RETIRED_TYPES`（型 → 案内。描いて警告し、deck-format.md・台本係の一覧・image-slide の型には載せない）と、`deck_style.RETIRED_LOOKS`（`look.surface: white` → `line`。読んだときに注意を出す）。今、退役した型は無い
4. **周り**: image-slide の `TYPES`・`TYPE_NOTES`・`LAYOUT_KEYS`（`PROMPT_VERSION` 10）、`contenttypes.LABELS`、型の数の試験（おまかせ＋29＝30）、catalog、見本（sample-deck に 3 つの型と札・いま・`takeaway_icon`）。
   既定ブランドの本文の色は `store/presets.py` の `gray` の 2 段目を `#333333` にして DADS の Gray-800 にそろえた（主役の色 `#0DB4F2` は入力のまま）

## 理由

- 点検（2026-10-10）で、折れ線の線・matrix の強調・timeline のいま・表の強調列・強調の箱・矢羽根の最後の段が色だけで区別されていた。DADS は色だけに頼らない
- 補足・注意・用語・進み具合は、既存の型（cards・bullets・table・flow）では「同格の並び」に見え、状態や種類が伝わらなかった

## 見送ったこと

- `checklist`（状態札つきの一覧）・`steps` の skip・error の状態: 点検の候補にあったが、まず 3 つに絞った
- 台本の `takeaway` を `{text, icon}` にすること: image-slide の中身の扱いが変わるので、別のキー `takeaway_icon` にした
- 型の退役: 見づらくて直せない型は無かった

## 残る懸念

- 既定ブランド以外のブランドの本文の色は、算出の gray（`#404040`）のまま。エンジンの既定（`#333333`）にそろえるなら `palette.py` の明度を替える（全ブランドに効く）
- `docs/P4-NOTES.md` の既定ブランドの色の記述は古い（`#404040`）
- 縦の `steps`・`notice` は絶対配置でなく流れの配置が混ざる。PPTX の位置は LibreOffice の画像で確かめた（PowerPoint は人が見る）
