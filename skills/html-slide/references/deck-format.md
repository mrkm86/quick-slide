# 資料の台本（deck）の書き方

資料用の台本は JSON 1枚。型を選んで中身を書けば、座標・文字の大きさ・動きは型が決める（`scripts/deck.py`・`scripts/deck_types.py`）。
見本は [`../examples/sample-deck.json`](../examples/sample-deck.json)（文字と図の型・架空の題材）と
[`../examples/image-deck.json`](../examples/image-deck.json)（画像の型と置き方を全部使う・画像は `examples/images/`）。出す HTML は [`contract.md`](contract.md) の決まりに沿う。
**ストーリー（何をどの順で言うか・枚数）は台本を書く側が決める。** このスキルはストーリーの型を持たない（[ADR 0004](../docs/adr/0004-no-story-templates.md)）。迷ったら「書く前に」の 3. の筋に沿う。

## 書く前に（80点を外さないための10）

1. **見出しは主張で書く。** 名札（「現状」「課題」）にしない。「会議の時間は3年で1.4倍に増えた」のように、その1枚で言いたいことを1文で。名札は `kicker` に置く
2. **1枚1メッセージ。** 言いたいことが2つあれば2枚に分ける
3. **筋は 結論 → 課題 → 原因 → 解決 → 効果 → 次の一手。** 段ごとに1枚にし、型は 9. のとおり中身で選ぶ（筋そのものを1枚で見せたいときだけ `flow`）
4. **数字は大きく。** 言いたい数字が1つなら `big-number`、同格の数字が3〜4なら `kpi`、大小の比較なら `bar-chart`、推移なら `line-chart`
5. **写真や絵に文字を描かない。** 文字は `photo` の型が上に重ねる（あとで直せるように）
6. **数字には母集団と単位を書く。** 「62%」だけにせず「回答者が先月出た会議のうち 62%」。`n=240` のような記号は「回答240人」と言葉にする
7. **同じ表・同じグラフで単位を混ぜない。** 週と月、人と件を並べない。換算したら注記に書く
8. **ラベルも日本語で書く。** 「RULE 1」ではなく「ルール1」（固有名詞は英語のままでよい）
9. **型は中身の形で選び、同じ型を続けない。** 順番のある段は `flow`、並列の要点は `cards`、観点×項目は `table`、2軸の位置づけは `matrix`、時期は `timeline`、
   一番言いたい1文は `statement`、冒頭の結論と根拠は `summary`、数字は 4. のとおり。順番の無い並びを `flow` にしない。
   進み具合（どこまで済んで今どこか）を見せるなら `steps`、補足・注意・決まりごとは `notice`、用語と説明・項目と内容の対は `deflist`（比べるなら `table`）。
   同じ型を3枚続けない。1つの型は表紙・締めを除いた枚数の3分の1まで。10枚を超えたら話の区切りに `section`（か `agenda` の再掲）を入れる
10. **箱の中は名詞と短い補足まで。** 文で説明しない。題は全角10字まで、補足は20字まで（「文字起こし／Whisper・MLX」）。
   言い切りは見出しに、結論の1行は `takeaway` に置く。構成要素は `equation`、中心と周りは `hub`、直し方は `pairs`、並列の要点には `icon` を添える。
   強める項目（`accent`・`highlight`・`current`）は色だけに頼らない: 型が札（「注目」など）・太い輪郭・二重の丸で示す。札の語は `badge` で替えられる（全角6字まで）

**絵文字は使わない**（文字にも `talk` にも）。印が要るなら `icon` キー。

> 6〜8 は、見本の台本を3ラウンド検品して実際に落ちた点。10 は、文で埋めた箱が冗長に見えた実例から。9 は、13枚の台本で7種しか使わず矢羽根が4枚に偏った実例から。

## 一番外

| キー | 必須 | 中身 |
|---|---|---|
| `title` | | 資料の名前（HTML の title。無ければ台本のファイル名） |
| `theme` | | テーマの名前（`default`・`forest`・`warm`）か YAML のパス（台本からの相対でよい）。`--theme` が優先 |
| `mode` | | `talk`（話す資料・既定）か `handout`（配る資料） |
| `footer` | | 中身の型の左下に毎枚出す文字（会社名・資料名・「社外秘」など） |
| `page_numbers` | | ページ番号を中央下に出すか（既定 `false`。2026-10-06 から。ロゴは角に置くので重ならない） |
| `outline` | | 読み飛ばす（前の版の構成の型の名前。ADR 0004 でやめた。書いてあっても止めない） |
| `slides` | ✓ | スライドの配列（上から順） |

## 1枚に共通のキー

| キー | 中身 |
|---|---|
| `type` | 型の名前（下の一覧）。**必須** |
| `role` | 読み飛ばす（前の版の章の ID。ADR 0004） |
| `talk` | 話す台本（文字の配列）。**1つ目は画面に入ったとき、2つ目以降は各クリックのときに話すこと**。省けば空 |
| `transition` | `fade`（既定）／`none`。`morph` は退役した（書いてあれば fade で描き、警告する） |
| `steps` | `false` にすると、その1枚のクリックをなくす（全部が自動で順に出る。`talk` は1つにする） |
| `takeaway` | 中身の下に置く**結論の1行の帯**（全角でおよそ40字まで）。最後のクリックと一緒に出る（クリックは増えない）。頭にアイコンが付く。中身の型（`cards`・`flow`・`compare`・`bullets`・`timeline`・`summary`・`kpi`・`bar-chart`・`line-chart`・`table`・`matrix`・`equation`・`hub`・`pairs`・`deflist`・`steps`）だけ。`notice` には置かない |
| `takeaway_icon` | 帯の頭のアイコンの名前（下の「アイコン」の一覧から。省くと `info`）。`takeaway` と一緒に書く |
| `fx` | 並びの1つずつ（カード・矢羽根・比較の列・箇条書きの項目・大きな数字の補足・要約の根拠・指標・人）の出方を変える: `rise`・`fade`・`pop`・`wipe-right` など（contract.md の語彙） |

**文字の大きさ**は型が決める。項目が少なくて場所が余るページ（並びの型で画像の無いもの）は、型が文字を大きくして組む。並ぶ項目の題・本文の大きさはそろう（[ADR 0018](../docs/adr/0018-grow-sparse-pages.md)）。

**文字の強調**は、文字の中で `**語**` と書く（テーマの差し色 accent になる。差し色が無ければ主役の色）。1枚に1〜2か所まで。

**改行**は、文字の中に `\n` を書く（`<br>` になる）。見出しは文節で折り返すが、まれに語の途中で折れる（「全部／署」）。そのときだけ切れ目を `\n` で決める（例: `"次は1月から、\n全部署の定例に広げたい"`）。

### talk と動きの照合（rc=2）

**`talk` の数 − 1 ＝ その型が作るクリックの段の数** にならなければ止まる（話す台本と動きがずれるため）。
段の数は下の一覧の「クリック」の列。PPTX のノートは `talk` を「［クリック］」でつないだ文になる。

- 例: `cards` で3枚 → クリック3回 → `talk` は4つ（入ったとき＋カード3枚ぶん）
- 段を減らしたいときは `steps: false`（クリック0回 → `talk` は1つ）

### mode: handout（配る資料）

同じ台本のまま、**動きのない資料**にする（読む・配る・PDF にする・受け取った人が直す用。2026-10-06）。
出方の効果も画面の切り替えも付けない（全部が最初から出ている・`transition` は `none`）。
ノートに［クリック］は入らない（`talk` は改行でつなぐ）。`talk` の数の照合は talk モードと同じ（同じ台本を両方で使えるように）。

## 型の一覧

| 型 | いつ使うか | クリック |
|---|---|---|
| `cover` | 表紙。資料の題と、誰がいつ出したか。`layout: hero` で写真を全面に、`graphic` で写真の代わりに図形で飾る | 0 |
| `agenda` | 目次。話の順番を先に見せる（2〜6項目）。章の頭で `highlight` を付けて再掲もできる | 0 |
| `section` | 章扉。話の区切りを明確にする。`layout: graphic` で写真の代わりに図形で飾る | 0 |
| `statement` | 一番言いたいことを1文で大きく言う（結論・まとめ） | 0 |
| `summary` | 冒頭で結論1文と、それを支える根拠2〜3を1枚で見せる（エグゼクティブサマリー・ハイライト・稟議の要旨） | 1 |
| `big-number` | 1つの数字で状況を言い切る（＋補足の数字2〜3） | 1（`side` があれば 2） |
| `kpi` | 同格の数字を3〜4個並べ、前年比などの増減を添える（業績・週次の指標・効果の目標） | 1 |
| `cards` | 並列の要点 2〜4（ルール・特長・選択肢） | カードの枚数 |
| `flow` | 順序のある3〜5段（課題→原因→解決→効果・手順） | 段の数 |
| `compare` | 2つを並べて違いを見せる（いま／これから・A案／B案） | 2 |
| `bar-chart` | 数の大小・少ない時点の推移を見せる（2〜8本） | 1 |
| `line-chart` | 推移を線で見せる（3〜12 時点・1〜3本。月次の推移・トラクション・試行の前後） | 1 |
| `table` | 複数の観点で比べる・一覧を見せる（2〜5列・1〜7行）。上に数字のカードの帯（`summary`）も置ける | 1（`summary` ありは 2） |
| `bullets` | 箇条書き 2〜5（お願い・要点）。右に写真も置ける | 項目の数 |
| `timeline` | 時期と出来事（計画・ロードマップ・経緯 3〜6） | 1 |
| `photo` | 写真や絵を大きく見せ、主張を重ねる（全面・左右・上の横長の帯。左右は写真の幅を 1/2・2/5・1/3 から選ぶ） | 0 |
| `quote` | 誰かの言葉で語らせる（お客様の声・現場の声）。`layout: full` で写真の上、`portrait` で人物の写真を脇に | 0 |
| `gallery` | 写真を2〜4枚並べて見せる（現場・事例）。`layout: collage` で主役1枚＋脇2枚。各枚に短いキャプション | 写真の枚数 |
| `figure` | スクリーンショット・図面・地図など、**端を切ると意味が変わる画像**を切らずに見せる | 0 |
| `annotate` | 写真や図の**決まった場所を指して説明する**（番号の丸印＋横に番号つきの説明） | 印の数 |
| `team` | 体制や人を、写真（無ければ頭文字の丸）・名前・役割で並べる（2〜6人） | 1 |
| `matrix` | 2×2 で位置づける（選択肢の比較・優先度・競合の位置） | 1 |
| `closing` | 締め。次の一手（1〜4）と連絡先 | 0 |
| `equation` | 式で言い切る（A＋B＋C＝D・構成要素とその結果）。要素2〜4と答え | 要素の数＋1 |
| `hub` | 中心と周り（司令塔と道具・1つの仕組みと関わる人や部品 3〜6） | 1 |
| `pairs` | よくない例 → よい例 を 2〜4 組（直し方・やり方の違い） | 組の数 |
| `notice` | 補足・注意・決まりごとを、種類（お知らせ・完了・注意・重要）つきの枠で 1〜4 個（DADS の注釈ブロック） | 枠の数 |
| `deflist` | 用語と説明・項目と内容の対を、左右 2 列で 3〜8 行（DADS の説明リスト）。比べるなら `table` | 行の数 |
| `steps` | 手順の進み具合（済・いま・これから）を、番号の丸と札で見せる。横 3〜5 段・縦 3〜6 段（DADS のステップナビゲーション）。順番の説明だけなら `flow` | 段の数 |

## 型ごとのキー

中身の型（`cover`・`section`・`photo` 以外）は左上に見出しを持つ:
`kicker`（小さな名札・任意）、`headline`（**主張**・必須）、`sub`（リード文・任意・2行まで）、`source`（出典・注記・任意・下端に1行）。

**文字の長さの目安**: 見出しは 2行（全角でおよそ 28字×2）まで。入らなければ DADS の段（64→57→48→45）を1つずつ下り、45px まで下りても入らなければ rc=2。
ほかの文字も型ごとに行数の上限と下限（本文 24px・注記 22px）があり、下限まで下りても入らなければ rc=2（**文を短くする**。最終的なはみ出しは描き出しの Chrome の検査が見る）。

| 型 | キー（★は必須） |
|---|---|
| `cover` | `title`★（3行まで）・`label`（題の上の小さな文字）・`subtitle`（2行）・`meta`（日付・組織など文字か3つまでの配列）・`image`・`layout`（`split`＝右の 880×1080 に写真・既定／`hero`＝全面の写真／`graphic`＝右の 880×1080 をテーマの色の図形で飾る。image は書かない）・`motif`（graphic の図柄: `circles`＝重なる円・`grid`＝点の格子と四角・`bands`＝斜めの帯・`blocks`＝色の面の組み合わせ。省くと資料の題で決まり、資料の中でそろう）・`text_side`（hero の文字の側 `left` 既定・`right`）・`tone`（hero の幕 `light`＝明るい幕に濃い文字・既定／`dark`＝暗い幕に白い文字） |
| `agenda` | `headline`（省くと「本日お話しすること」）・`items`★（文字か `{title, note}`・2〜6）・`highlight`（強める項目・1から） |
| `section` | `title`★・`subtitle`・`number`（省くと章扉の通し番号）・`label`（「CHAPTER」など）・`image`（あれば右半分に写真、無ければ左に主役の色の帯）・`layout`・`text_side`・`tone`・`motif`（cover と同じ。`hero` で全面の写真、`graphic` で右半分を図形で飾る） |
| `statement` | `headline`★（主張・3行まで）・`body`（補足・3行）・`kicker` |
| `big-number` | `value`★（数字の文字）・`unit`（単位）・`label`（説明・2行）・`note`・`side`（`{value, text}`・0〜3） |
| `cards` | `items`★（`{label, title★, body, accent, image, icon, badge}`・2〜4）。`accent: true` の1枚を強める（頭に札「注目」が付く。`badge` でその語を替える）。`image` はカードの上に 16:9 で、書くなら全部のカードに |
| `flow` | `items`★（`{label★, title, body, accent, badge}`・3〜5）。`label` は矢羽根の中の短い語（「課題」）。既定で最後の段を強める（濃い面と太い輪郭。`badge` を書けば、その段の頭に札が付く） |
| `compare` | `left`★・`right`★（`{label, title, points: [文字…], image, badge}`・points は5つまで。強める側の頭に札「注目」が付く（`badge` でその語を替える）。`image` は列の上に 16:9 で、書くなら両方に＝ビフォー・アフター）・`accent`（`right` 既定／`left`／`none`）・`arrow`（既定 true） |
| `bar-chart` | `bars`★（`{label★, value★（0以上の数）, highlight, display, tone}`・2〜8。`tone` は `good-strong`・`good`・`good-soft`（良い側の濃い・中・淡）／`warn`／`bad`／`neutral`。`neutral` と省略は今の灰。色は棒と値の文字に付き、`label` は必ず出る。`highlight` と同時なら色は `tone`・強調はラベルの太字だけ。有彩色の `tone` で値が 0 の棒は、軸の上に高さ 16px の短い帯（0 の札）で残る。量の高さではない）・`unit`（値の後ろに付ける）・`max`（軸の上限）・`note`（グラフの注記）・`callout`（`{value, text}` 右に要点） |
| `table` | `columns`★（2〜5）・`rows`★（各行は columns と同じ数・1〜7行）・`highlight`（強める列・1から。色の地に加えて、太い縦の罫と太字で示す）・`widths`（列の幅の比）・`align`（列ごとの揃え。columns と同じ数の配列で、値は left / center / right。見出しの行も同じ揃え。省略すると 1 列目 left・残り center。数の列は right にすると桁がそろう）・セルは文字・数のほかに `{"text": "20日", "tone": "warn"}`（`text` 必須・`tone` は `good` / `warn` / `bad`。淡い地＋濃い文字。見出しの行 `columns` には書けない。しきい値の判定は台本を書く側で行い、エンジンは色を付けるだけ）・`dim_rows`（薄くする行の番号の配列・1から。見出しの行は数えない。文字を `muted` にする。`tone` を書いたセルでも薄くするほうが優先で、地も付かない。1列目・`highlight` の列の太字は残る。重複はまとまる）・`legend`（色の凡例。1〜4 項目の `{tone, text}`・どちらも必須。`tone` は `good` / `warn` / `bad` / `dim`（`dim` は `dim_rows` の薄い行）。表の下に「色見本＋説明」を1行で並べ、`source` はその下。1行に入らなければ rc=2。書いていない色を載せてもよい）。**色だけで意味を伝えない:** `tone`・`dim_rows` を使うときは `legend` で色の意味を文字でも書く（`source` は出典・注記に使う）。`legend` は `table` だけ）・`summary`（見出しと表の間の数字のカードの帯。**1〜4 枚**で、各カードは `kpi` の `items` と同じ `label`★・`value`★・`unit`・`delta`・`trend`・`note`・`tone`・`group`（意味も見た目も `kpi` と同じ）。`accent`・`badge` は使えない。幅は本文の幅を枚数で等分（間は溝）。カードの高さの上限は 384px と使える高さの小さいほう。入らなければ全部のカードの文字を同じだけ小さくし、それでも入らなければ rc=2。表・`legend`・`source` はその下に組み、残りの高さに入らなければ rc=2。**2 クリック**: カードが1クリック目に順に出て、表と `legend` が2クリック目。`summary` を書かない表は1クリック。**`summary`・`legend`・`source`・`takeaway` を併用すると、内容によって表の行がかなり限られる**（帯・凡例・source・結論の帯が高さを使うため）。明細が多いときはページを分ける。入らなければ rc=2 で、縮める所を言う） |
| `bullets` | `items`★（文字か `{title, body, icon}`・2〜5。icon は書くなら全部の項目に）・`image`（脇に写真）・`side`（写真の側 `right` 既定・`left`）・`split`（写真の幅 `1/3` 既定＝格子の4列 528・`2/5`＝5列 672・`1/2`＝6列 816） |
| `timeline` | `items`★（`{when★, title★, body}`・3〜6）・`current`（今の時期・1から。札「いま」と二重の丸で示す） |
| `photo` | `image`★・`headline`★・`hide_text`（`layout: full` だけ。true で画像だけの扉絵。下を見る）・`layout`（`full` 既定／`left`／`right`／`band`＝上に横長の写真の帯 1920×480、下に見出しと本文。下の文字・ページ番号も出す）・`split`（left・right の写真の幅 `1/2` 既定＝960・`2/5`＝768・`1/3`＝640）・`kicker`・`body`・`caption`（写真の出典） |
| `quote` | `quote`★（4行まで）・`by`（誰の）・`position`（肩書き。`role` は章の ID に使う）・`kicker`・`layout`（`plain` 既定／`full`＝全面の写真に一様な暗幕を敷いて白い文字。帯・ページ番号は出さない／`portrait`＝左に人物の丸い写真）・`image`（full・portrait で必須） |
| `gallery` | `items`★（`{image★, caption}`・2〜4）。枠は全部同じ大きさ（縦横比は枚数で決まる。2:3 より縦長にしない）・`layout`（`row` 既定／`collage`＝3枚を大1枚（左は格子の 7 列）＋小2枚（5 列）） |
| `annotate` | `image`★・`items`★（`{x★, y★（0〜100 の %）, title★, body}`・1〜6）・`fit`（`cover` 既定＝枠いっぱいに切り抜き、% は枠に対して／`contain`＝切らずに縮め、% は描いた画像に対して）。画像は左の格子の 8 列（1104 幅）、説明は右。印と説明は同じクリックで1つずつ |
| `figure` | `image`★（`object-fit: contain` で縮めて全体を見せる。余りは地の色）・`caption`（図のすぐ下に1行） |
| `closing` | `items`（次の一手 `{title, body}`・0〜4）・`contact`（文字・配列・`{label, name, lines}`）。どちらかは必須・`image`（右に写真 768×1080。次の一手と連絡先は左に縦に積む。ページ番号は出さない） |
| `summary` | `headline`★（結論）・`points`★（`{title★, body, value}`・2〜3）・`label`（根拠の番号の前の語。既定「根拠」）。`kicker` は省くと「結論」 |
| `kpi` | `items`★（`{label★, value★, unit, delta（前年比など。淡い地の札になる）, trend（up・down・flat → ▲▼→）, tone（good・bad・neutral。増減の札の色）, note, accent, badge, group}`・3〜4。accent の指標の頭に札「注目」が付く）・`image`（上に現場の写真の帯 1920×296。見出しと数字はその下）。`tone` を省くと trend から決まる（up＝good 緑・down＝bad 赤・flat と trend なし＝neutral 灰）。増えるほど悪い数字（滞留日数など）は `tone: bad` で上書きする。▲▼→ は trend のまま変わらない（色だけで示さない）。`delta` が無ければ札は出ない。`group` は `{label★, tone★}`: カードの種類分けで、tone は `a`（緑系）・`b`（橙系）・`c`（青系）。どれもテーマを変えても同じ色。同じ group のカードは同じ淡い地と枠・同じ札（label は全角6字くらいまで）になる。**札は必ず出る**。評価の良し悪しとは関係がない。group を書かないカードはいつもの面のまま（一部のカードだけに書いてよい）。`accent` と `group` は同時に書けない（rc=2）。3枚のときはカードが使える高さいっぱいに伸び、中身は縦の中央に並ぶ |
| `line-chart` | `labels`★（横軸の時点・3〜12）・`series`★（`{name, values★（labels と同じ数）, highlight}`・1〜3。2本以上は `name` 必須。線は点の形（丸・四角・ひし形）と線種（実線・破線・点線）を台本の順に変え、線の端に名前と最後の値を出す。名前が長くて入らないときだけ凡例）・`unit`・`min`（既定 0。負の値があれば必須）・`max`（省くと切りのよい上限）・`note`・`callout`（`{value, text}`）・`value_labels`（`all`／`last`／`none`。既定は1本で8時点までなら all、ほかは last） |
| `team` | `members`★（`{name★, role, note, image, initials}`・2〜6）。`image` が無ければ `initials`（省くと名前の1文字目）の丸。5〜6人は3人ずつ2段 |
| `equation` | `items`★（`{title★, body, icon}`・2〜4）・`result`★（`{title★, body, icon}`・答え。主役の色で塗った箱。`body` は札になる）。見出しは大きく出る |
| `hub` | `center`★（`{title★, body, icon}`・中心の丸）・`items`★（`{title★, body, icon, tag}`・3〜6。左右に分けて並べ、中心と線でつなぐ。title・body・tag はそれぞれ1行）。見出しは大きく出る |
| `pairs` | `items`★（`{label★, before★, after★, icon}`・2〜4。label は何の話か・before はよくない例・after はよい例。どれも短く）・`bad_label`（既定「NG」）・`good_label`（既定「OK」）。札には×・チェックのアイコンが付く。見出しは大きく出る |
| `notice` | `items`★（`{kind, label, title★, body, points}`・1〜4）。`kind` は `info`（お知らせ・既定）／`success`（完了）／`warning`（注意）／`error`（重要）で、アイコン・種類の語・枠の色が決まる（`label` を書けばその語）。`points` は短い箇条（4つまで）。`label_style`（一番外）は `icon`（アイコン＋語の行・既定）か `chip`（頭の行をアイコンでなく kind の色の札にする。語は残る）。4個のときは枠の余白と文字を詰めるので、題は1〜2行・本文は短く。1つずつクリックで出る。見出しの下に枠を縦に並べる |
| `deflist` | `items`★（`{term★, body★, icon}`・3〜8）。左に用語（太字）・右に説明。用語の列は格子の 3 列（長ければ 4 列）で固定。`icon` は書くなら全部の行に。1行ずつクリックで出る |
| `steps` | `items`★（`{title★, body, state}`・横 3〜5・縦 3〜6）・`current`（いま何段目か・1から。それより前が済・後ろがこれから）・`layout`（`row` 既定＝横／`column`＝縦）。`state`（`done`＝済・`current`＝いま・`todo`＝これから）を段ごとに書けば `current` より優先。どちらも無ければ rc=2。状態は丸の形（済は塗り・いまは二重の輪・これからは白に枠）・札の語・線（済は実線・これからは破線）で示す。1段ずつクリックで出る |
| `matrix` | `x`★・`y`★（`{label★, low, high}`）・`items`★（`{label★, x★, y★（0〜100）, accent}`・1〜8。accent の点は二重の輪。名前どうし・区切り線との重なりはずらし、置けなければ rc=2＝x・y を離す）・`quadrants`（`{title, body}` を 左上・右上・左下・右下 の順に4つ）・`focus`（淡く塗る象限・同じ順で1〜4）・`callout`（`{title, text}` 右に要点） |

### アイコン（`icon`）

`cards`・`bullets`・`equation`・`hub`・`pairs`・`deflist` の項目と、`takeaway_icon`（結論の帯）に、アイコンを名前で添えられる（[ADR 0019](../docs/adr/0019-diagram-types-icons-takeaway.md)）。
PPTX では図形のまま（色はテーマの主役の色）。合う名前が無ければ書かない。カードは icon と image のどちらか。

doc（書類・原稿）・list（一覧・手順）・checklist（決まり・確かめること）・code（プログラム）・chart（数字・グラフ）・trend（伸び・増える）・scissors（切る・削る）・image（写真・画像）・video（動画・再生）・mic（声・話す）・speaker（音）・captions（字幕・文字）・chat（会話・相談）・mail（連絡・メール）・sparkle（強調・仕上げ・AI）・bulb（思いつき・アイデア）・target（目標・狙い）・flag（決めたこと・到達点）・check（できた・よい）・cross（やめる・よくない）・alert（注意・困りごと）・search（探す・調べる）・folder（フォルダ・置き場）・layers（重ねる・仕組み）・gear（設定・仕組み）・shield（守る・安全）・lock（鍵・閉じる）・link（つなぐ）・refresh（繰り返す・見直す）・upload（入れる・出す）・download（受け取る）・calendar（日付・予定）・clock（時間）・yen（お金・費用）・user（人・担当）・users（人たち・チーム）・building（会社・建物）・laptop（パソコン・手元）・phone（スマホ）・cloud（外のサービス・クラウド）・book（学ぶ・手引き）・pin（場所）・star（おすすめ・大事）・rocket（始める・速く）・hub（司令塔・まとめ役）・info（情報・補足）・notice（お知らせ・告知）・help（疑問・ヘルプ）・error（エラー・失敗）・done（チェックの印だけ（丸なし））・arrow-right（次へ・進む）・arrow-left（戻る）・arrow-up（上・昇順）・arrow-down（下・降順）・chevron-right（リンクの強調・次）・chevron-left（前）・chevron-down（開く（アコーディオン・選択））・chevron-up（閉じる）・compare（比べる）・trend-down（減る・下がる）・add（追加）・remove（削除・引く）・close（閉じる・×）・edit（編集）・delete（ごみ箱・削除）・copy（複製）・external-link（新しいタブで開く）・eye（見る・表示）・filter（絞り込み）・sort（並べ替え）・menu（メニュー）・more-vert（縦三点（メニューを開く））・home（ホーム・トップ）・article（記事・本文）・table（表）・slideshow（スライド・資料）・assignment（課題・申請）・fact-check（検証・照合）・database（データ・保存）・pie（割合・構成比）・analytics（分析・気づき）・timer（所要時間・計る）・hourglass（待つ・処理中（動かない砂時計））・history（履歴・経過）・event（催し・日程）・terminal（コマンド・端末）・key（鍵・認証）・bolt（速い・電気・即時）・robot（AI・ロボット）・brain（考える・判断）・tree（構造・分岐）・puzzle（部品・拡張）・tool（道具・直す）・speed（速さ・性能）・school（学ぶ・研修）・work（仕事・業務）・handshake（合意・連携）・globe（世界・公開）・translate（言語・翻訳）・award（成果・表彰）

### 画像

画像の枠（`image`・team の `members[].image`）は、文字（ファイルのパス）か、次のオブジェクトで書く。

| キー | 中身 |
|---|---|
| `path` | ローカルのファイルのパス（台本からの相対か絶対）。png / jpg / webp。URL（`https:`・`data:`）は rc=2 |
| `prompt` | 何を描くか（画像生成への指示）。`path` が無ければ、`deck` が Codex で描いて埋める（下の「絵の出どころ」） |
| `focus` | 枠に合わせて切り抜くときに残す位置: `center`（既定）・`left`・`right`・`top`・`bottom` か `"40% 30%"`（横・縦の %） |
| `alt` | 画像の説明（PPTX の代替テキストになる） |

- `path` か `prompt` のどちらかは必須。`path` が無ければ、まず `<台本のフォルダ>/images/s{NN}-{枠の名前}.png`（`NN` は何枚目か、枠の名前は `image`・`member1` など。.jpg・.webp も見る）を探し、
  無ければ **Codex で描いた絵（キャッシュの区画の `gen-<鍵>.png`）** を使う（鍵は描く指示から作る。ページ番号は入れないので、並べ替えても絵はついていく）
- `deck` は**既定で、まだ絵の無い生成の枠を Codex で描いて埋めてから組む**（[ADR 0011](../docs/adr/0011-fill-images-with-codex.md)）。描けなかった枠は仮の枠で組み、rc=1。
  `--no-fill` は描かずに仮の枠（急ぐ下書き）。`--placeholder` は持ち込みの無い絵も仮の枠にする。仮の枠には「ここに画像」と出るので、そのまま出すと検品の H-3 で落ちる
- `path` を書いた枠（持ち込み）が無ければ、描く前に rc=2（置き場のパスを言う）
- 枠の一覧（推奨の大きさ・文字を重ねる側を空けるよう足した prompt・有るか無いか）は `deck.py --script <台本> --out _ --slots`（関数は `deck.image_slots`）。
  推奨の大きさは、画像生成で出せる 1024×1024・1536×1024・1024×1536 のうち枠の縦横比に一番近いもの（16:9 の全面なら 1536×1024 を中央で切り抜く）
- 出力の `assets/` にコピーして HTML から引く。枠に合わせて切り抜く（`object-fit: cover`・`focus` の位置）
- **写真の上に文字を重ねる型**は、文字の下に ink の暗幕を敷く。暗幕を一番明るい（白い）写真に重ねた色を地として、白の文字も強調の色も 4.5 を下回れば rc=2（DADS は大きな文字の緩和を使わない。暗幕は ink の 86%）

### 画像だけのページ（扉絵。ADR 0026）

文字を載せず、画像だけを全面に敷くページは `photo` の `layout: full` に `hide_text: true` を書く（幕も文字も出ない）。**`headline` は書く**（ページの名前として一覧・版の履歴に残る。画面には出ない。元の題があれば変えない）。出典が要るときは `caption` を書く（右下に小さく出る）。表紙を画像だけにしたいときも、1 枚目をこの `photo` にする（`cover` は題が要る）。ピッチ（image-slide）でも同じ意味で、文字の無い絵を描く（出典だけ右下に小さく）。

### 絵の出どころ（ADR 0011）

| 出どころ | 中身 | 台本 |
|---|---|---|
| 持ち込み | 営業資料の写真・製品・現場・人（「あの資料のあの写真」） | `path` |
| 生成 | 空気・場面・気持ちを伝える写真風・挿絵 | `prompt` だけ（Codex が描く） |
| 図解 | 構造・手順・数字・仕組み | 型（flow・bar-chart・matrix など）か `figure` に SVG |
| キャラ | 差し絵のキャラ（Hana・Pochi など） | ページの `_characters`（下） |

- **画像にするかは台本を書く側が決める。** 構造・手順・数字なら図解（型）、空気・場面・気持ちなら生成、実在のもの（自社の製品・特定の人や場所）は持ち込みだけ（生成で作らない）
- visual は表紙・章扉・締めに画像を置くのが基本（無ければ警告。`expects_images`）。standard も、効く所には置いてよい（どちらでも生成の枠は描いて埋める）
- **差し替え**: 持ち込みの写真に替えるなら、その枠に `path` を書く（以後 Codex は触らない）。縦横比は `focus`・`fit: contain` で合わせる。
  生成の絵を直すなら `revise --page N [--slot 枠] --note "…"`（既定は微修正＝今の絵を土台に・`--remake` で作り直し）
- **画風は資料全体で1つ**: `--image-look photo|illustration|handdrawn`（省けばスタイル。standard は illustration・visual は photo）。
  変えると生成の枠だけ描き直す（持ち込みは触らない・前の画風の絵は残るので戻すときは描かない）。生成した絵に「イメージ」などの文字は入れない

**キャラ（`_characters`）**: ページに `"_characters": [{"name": "Hana", "image": "<パス>", "pose": "考える"}]` と書く（image-slide と同じ書き方。絵は呼ぶ側が
用意して渡す）。**そのページの最初の生成の枠**に参照画像として添え、造形はそのまま、ポーズと表情を合わせて描かせる。
生成の枠が無いページ（持ち込みの絵だけ・画像の無い型）には描かない（警告）。参照画像は合わせて5枚まで（Codex の上限）。

### 画像を頼むとき（prompt の書き方）

`prompt` は画像生成にそのまま渡せる文で書く（`image_slots` の `prompt_full` は、これに「文字・ロゴ・透かしを描かない」「文字を重ねる側を空ける」を足したもの）。

1. **場面と主題**: 何を・どこで（「朝の光が入る明るい会議室。木の机と椅子」）
2. **空ける側（copy space）**: 文字を重ねる型は、その側を空けさせる。全面の表紙なら「左の45%は無地の壁で何も置かない」、全面の引用なら「全体に暗めで、中央は空ける」。左右の写真・写真の並び・カードは空けなくてよい
3. **焦点**: 2 と同じ位置を `focus` にも書く（主題が右なら `"right"`、右上なら `"right top"`）。**生成の寸法は当てにしない**（Codex 経由の gpt-image-2 は 1774×887 などで返る）。枠に合わせて切り抜くので、大事なものは focus の側に置く
4. **画風は書かない**: 画風は `--image-look`（省けばスタイル）が全部の生成の枠に足す。prompt には「何を描くか」だけを書く（写真なら写真、イラストならイラストにそろう）
5. **描かせないもの**: 文字・数字・ロゴ・看板や画面の読める文字・実在の人物や会社や商品の名前・写真の縁や枠

推奨の大きさ（`size_exact`）は枠と同じ縦横比で 16 の倍数（全面 2048×1152・半分 1024×1152・`cover` の右 944×1152 など）。定型（`size`）で頼むなら 1536×1024・1024×1536・1024×1024 のうち比が近いもの。
**枠の 0.8 倍より小さい画像は警告**する（引き伸ばしてぼけるため）。

## 取り込んだページ（`html`・台本を書く側は使わない）

`html_slide.py import` が既存の PPTX から書くページ（[ADR 0025](../docs/adr/0025-import-pptx.md)）。型の一覧には載せない。

| キー | 中身 |
|---|---|
| `html` | section の中身。[`contract.md`](contract.md) に沿った絶対配置の HTML。画像は `<img src="media/…">`（台本のフォルダからのパス） |
| `background` | 地の色（`#rrggbb`・`rgba(…)`）か `linear-gradient(…)`。画像の地は `html` の一番奥の `img` |
| `notes` | ノート（元の PPTX のもの）。`talk` の代わりで、［クリック］の数は照合しない |
| `title` | 一覧・サムネイルに出す見出し |

- 段は HTML の `data-step`・`data-fx`・`data-delay` のまま。`mode: handout` は段を外す
- 書けないもの（rc=2）: `script`・`style`・`iframe`・`a` などの要素・`on…` の属性・`href`・style の `url()`・外の URL の画像
- 文字のはみ出し・スライドの外へのはみ出しは警告（字体の違いで起きる）。回すだけの `transform: rotate()` は文字の箱にだけ使える
- ページの `_hidden: true`（元の PPTX の非表示のスライド）は、呼ぶ側が抜く印。エンジンは読み飛ばす

## 画面をまたいで残すもの（data-keep）

中身の型には、上の細い帯（`band`）・左下の文字（`footer`）・ページ番号（`pageno`・`page_numbers: true` のときだけ・中央下）を毎枚同じ位置に置く。
`data-keep` で同じ名前を付ける（画面をまたいで同じ部品だと分かる名前。モーフは退役したので動きは付かない）。表紙・章扉・写真には付けない。

### ロゴ（`deck --logo <PNG か JPEG> [--logo-position bottom-right|bottom-left|top-right]`）

台本には書かない（image-slide と同じく引数で渡す）。既定は右下。ページ番号（出すときは中央下）とは重ならない。

- 大きさ: 透明な余白を切ってから、**329×44 の枠に縦横比を保って収める**（quick-slide の枠と同じ）。縮めるだけで引き伸ばさない。短辺が 30px 未満なら警告
- 位置: 右は本文の余白（120px）、縦は下の文字の行の中心にそろえる。右上なら上の余白の中（24〜68px。見出しは 80px から）
- ロゴの後ろに**ロゴと同じ大きさの地**（`!!logo-plate`）を敷く。色は、組んだ HTML を Chrome で開き、ロゴの周りで一番多い色（そのページのその場所の地の色。主張の全面なら塗りの色）
- **写真・絵（`<img>`）か文字がかかる角には置かず、次の角へ逃がす**。どこもふさがっていれば置かない（全面の写真）

| `--logo-position` | 試す順 |
|---|---|
| `bottom-right`（既定） | 右下 → 左下 → 置かない |
| `bottom-left` | 左下 → 右下 → 置かない |
| `top-right` | 右上 → 右下 → 置かない（上下をまたぐのは右の列だけ・左上は選べない） |

- 左下では、そのページの左半分の文字の左端にそろえ、下の文字（footer）をロゴの右へ寄せる。右下・右上では何も動かさない
- 角ごとに別の名前（`!!logo`・`!!logo-left`・`!!logo-top`、地は `!!logo-plate…`）にする。同じ名前だと、別の角の部品と区別できないため
- どのページにどう置いたかは `deck-info.json` の `logo.pages`（`corner`（bottom-right・bottom-left・top-right）・`x`・`plate` の色か `off`）

### 透かし（`deck --watermark <文字>`）

台本には書かない（ロゴと同じく引数で渡す。同じ台本を、配る先ごとに違う透かしで出せる）。例: `社外秘`・`配布禁止`・`○○様限定`。

- **全ページ**（表紙・章扉・写真も）の**中央下の端**に1行で置く。16px（行高 170%）・Gray-536 `#767676`（白の地で 4.54:1。地が白でなければ濃くする）・ベースラインが下端の約 12px 上
- 色は地（白）の上で 4.5:1。写真の上は測らない（明るい写真の上では薄くなる。透かしなので許す）
- 下の文字の行（1008〜1041px。中央下のページ番号）の下（1048px〜）に来るので重ならない。行は動かさない。下の文字が 1 行に入らないと透かしの帯にかかるので、透かしを付けるときは rc=2（付けないときは警告）
- PPTX では全スライドに `!!watermark` の文字の部品を置き、**クリックで選べない・動かせない・書き換えられない**ようにする（`a:spLocks`。消すには［選択］ウィンドウから）。画面をまたいでも同じ部品として残る
- PowerPoint の「ヘッダーとフッター」やスライドマスターは使わない（マスターの部品は全面の写真の下に隠れるため）
- 決めた経緯は [ADR 0009](../docs/adr/0009-watermark.md)

## テーマ

`themes/<名前>.yaml`。キーは `background`・`ink`・`primary`（必須）と `accent`・`muted`・`surface`・`font`・`font_files`（任意）。

```yaml
background: "#ffffff"
ink: "#1a1a1a"
primary: "#f5a623"   # 主役の色。骨格（上の帯・小見出し・番号・章扉・矢羽根・印・連絡先・主張の全面）
accent: "#399dd1"    # 差し色（任意）。*語*・accent: true の項目・強調する棒と線・表の強調する列。書かなければ primary と同じ
```

古い書き方（主役の色を `accent` に書く）は rc=2（「primary に名前が変わった」）。
ほかの色は `deck.py` が2色それぞれから、DADS のキーカラーの作り方（`skills/dads/scripts/keycolors.py`）で作る（[ADR 0020](../docs/adr/0020-dads-foundations.md)）。
DADS のプリミティブの段そのまま（`#006f83` = cyan-900 など）ならその色相の段、そうでなければ**同じ色相のまま明度だけを動かす**:
文字にも使う主役の色 `primary`（地・surface・淡い地の全部の上で 4.5:1）・意味のある図形と線の `primary-mark`（3:1）・飾りの面だけの `primary-decor`・
文字を載せる面 `primary-strong` と、その上の文字 `on-primary`（白）・淡い地 `tint`・写真の上の色 `primary-on-dark`。`accent` も同じ。
文字の `ink` は Gray-800・`muted` は Gray-600・罫・枠・軸の `rule` は Gray-536・非強調の棒の `bar` は 3:1。意味の色（成功・エラー・警告）は DADS の値で、白い面の上だけ。
**組み合わせのコントラストを検査する**（文字は大きさによらず 4.5、罫・枠・棒・図形は 3.0 を下回れば rc=2）。

字体は、テーマの `font`（既定 `Noto Sans JP`）の名前で設定（`config.example.yaml` を見る）の `fonts` を引く。
Noto Sans JP のファイルが分からなければ、游ゴシック（設定の `fonts`、無ければ PowerPoint 同梱のパス）で組み、`deck-info.json` の `font_note` に理由を残す。
テーマの `font_files: {regular, bold}` で直に差し替えもできる。文字の大きさは DADS の段（64・57・48・45・36・32・28・26・24・22・20・16 と、題・数字・主張だけの拡張 80・96・120・160・200）だけ。

## 確かめ方

```bash
python3 scripts/deck.py --script <台本.json> --out /tmp/deck-try           # HTML だけ組む（rc=2 なら台本を直す）
node scripts/snapshot.mjs /tmp/deck-try/deck.html /tmp/deck-try/png       # 1枚ずつ PNG に撮って目で見る
```

`snapshot.mjs` は撮るついでに、文字のはみ出しと画面の外に出る要素を「警告」で出す。

## スタイル（資料全体の見せ方の既定）

`deck`・`check`・`images` の `--style` で選ぶ（[ADR 0005](../docs/adr/0005-styles-standard-visual.md)）。台本に書いた変化形が勝ち、スタイルは書いていないところだけを埋める。

| スタイル | 中身 |
|---|---|
| `standard`（既定） | 説明・報告・社内資料向け。今までの見た目そのもの（何も足さない） |
| `visual` | ビジュアル寄り。プレゼン・ピッチ向け。表紙・章扉は全面写真、写真は全面、画像つきの箇条書きは写真1/2、3枚のギャラリーはコラージュ、出方はフェード。見た目は地に強調色をうっすら・上端に全幅の帯・白い箱に枠線・角を丸く・強調する箱と主張の1枚は強調色のベタ塗り |
| カスタム（YAML） | `defaults`（型ごとの変化形の既定）・`motion`（fx・transition）・`look`（見た目の調子: background・surface・radius・band・emphasis・statement）。雛形は `templates/custom-style.example.yaml`。知らないキー・値は rc=2 |

- 画像が要る変化形（hero・full・portrait など）は、そのページに `image` があるときだけ効く
- 何を足したかは `inspect.md` の各ページの「スタイルで足した既定」に出る

## 出典

型の名前と「いつ使うか」の考え方だけを参考にした。コード・CSS・説明文は写していない。

| 名前 | URL | ライセンス |
|---|---|---|
| Slidev 標準レイアウト | https://github.com/slidevjs/slidev | MIT |
| Marp core | https://github.com/marp-team/marp-core | MIT |
| reveal.js | https://github.com/hakimel/reveal.js | MIT |
| presenton | https://github.com/presenton/presenton | Apache-2.0 |
| presentation-ai（ALLWEONE） | https://github.com/allweonedev/presentation-ai | MIT |
| slide-deck-ai | https://github.com/barun-saha/slide-deck-ai | MIT |
| md2pptx | https://github.com/MartinPacker/md2pptx | MIT |
| PPTAgent | https://github.com/icip-cas/PPTAgent | MIT |
| PowerPoint・Google スライドの既定レイアウト | https://python-pptx.readthedocs.io/en/latest/user/slides.html ／ https://developers.google.com/apps-script/reference/slides/predefined-layout | 製品のレイアウト名（考え方の参考のみ） |
| Assertion-Evidence（Michael Alley） | https://www.assertion-evidence.com/ | 論文・サイト（考え方の参考のみ） |
| Marpit の画像の書き方 | https://github.com/marp-team/marpit/blob/main/docs/image-syntax.md | MIT |
| Keynote の既定のマスタースライド | https://iworkautomation.com/keynote/slide-masters.html | 製品のもの（考え方の参考のみ） |
| Material Design（Imagery） | https://m1.material.io/style/imagery.html | 考え方の参考のみ |
| NN/g「Ensure High Contrast for Text Over Images」 | https://www.nngroup.com/articles/text-over-images/ | 著作物（考え方の参考のみ） |
| Smashing Magazine「Designing Accessible Text Over Images」 | https://www.smashingmagazine.com/2023/08/designing-accessible-text-over-images-part1/ | 著作物（考え方の参考のみ） |
| OpenAI「GPT Image Generation Models Prompting Guide」 | https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide | 著作物（考え方の参考のみ） |
