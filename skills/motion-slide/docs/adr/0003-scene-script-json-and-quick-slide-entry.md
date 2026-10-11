# 0003. 場面ごとの台本（JSON）と Quick Slide からの入口を足す。型は持たない

- **日付**: 2026-10-11
- **状態**: 採用
- **経緯**: motion-slide を Quick Slide のエンジンにつなぐ（編集はページ＝場面ごと・品質は今のまま・出力は HTML 1枚）

## 決めたこと

- 台本の入口に **場面ごとの JSON** を足す（`references/deck-format.md`）。デッキ全体の設定（`config`・共通の `style`・`stage`・`script`）と、場面ごとの部品（`html`・`style`・`script`・`hook`・`talk`）を持ち、`scripts/motion_slide.py` が `templates/deck.html` の形に**組み立てて**今の `build.py` → `check.py` にかける。今の台本 HTML から作る使い方もそのまま残す
- JSON は**部品の入れ物**で、画面の型は持たない。場面の中身は HTML・CSS・JS を自由に書く（0001 で型を持たないとした理由は変わらない）。0001 の「機械が自動で組み立てる経路は無い」は、この入口で置き換わる
- `hook` は **`(b, x) => { … }` の本体だけ**を持つ。`MS.hook("<id>", …)` は組み立てが付けるので、別の場面の id を引けない形になる。場面だけで使う定数と `MS.ready` は場面の `script`
- **場面の hook・script は HTML の `<script>` に直に埋めず、`ms-scenes`（JSON）に入れてエンジンが `new Function` で場面ごとに作る**（2026-10-11 のレビューを受けて）。本体だけで文法を通してから包むので、`}` で本体の外へ出る書き方・`</script>` を含む文字列でも、その場面だけが落ちて他の場面・共通部に届かない。JSON は `<` `>` `&` を `\u` で逃がす。共通の `script` は `</script`・`<!--` を逃がし、`style` の `</style` は NG。これは作りで防ぐ層で、下の検査は補助
- 場面の HTML は html.parser でタグ名を小文字にそろえて検査し、`script`・`style`・`iframe`・`object`・`embed`・`base`・`meta`・`link`・`title`・`textarea`・`template` などを禁止、閉じ忘れ・閉じすぎ・閉じていない `<!--` を NG にする。場面の CSS は `#<id>` の直後の `~` `+` `:has()` と `@import` も NG
- **場面の中で閉じる**ことを検査で守らせる: 別の場面の `#id`・要素の id・`MS.go(別の番号)`、`#<id>` で始まらない場面の CSS、場面をまたぐ要素の id の重なりは NG。場面をまたぐもの（額縁・主役・共通の定数・場面を束ねる動き）は一番外の `stage`・`script`・`style` に置く
- 入口は html-slide の `check --script` / `deck --script --out` と同じ口・終了コード（0 / 2 / 4）・`NG: N枚目（id …）: …` の行・出力の名前（`slides/slide-NN.png`・`thumbs.png`・`build.json`・`deck-info.json`・`inspect.md`）にそろえる。ページの絵は check.py が撮った拍の絵（場面の `thumb`、無ければ最後の拍）
- `scripts/split.py` で今の台本 HTML を場面ごとの JSON に分ける。見本2本は分けて組み直して NG 0・場面と拍が同じになる（`tests/run.sh`。見本は固有名詞を含むので `MOTION_SLIDE_SAMPLES` で渡したときだけ走る）
- motion-slide は PPTX を考えない。HTML をフルに使う（docs/PRESENT-PLAN.md の「エンジンごとの動きの決まり」）

## 採らなかった案と、その理由

| 案 | 却下した理由 |
|---|---|
| 画面の型（cards・flow など）を JSON に持つ | 見本2本とも場面ごとに違う図と動きで、型に収まらない（0001 と同じ） |
| `hook` を `MS.hook("id", …)` 全体で持つ | 別の場面の id を書ける。本体だけにすれば組み立てが id を決める |
| 場面ごとの JS を共通の `script` に1本で持つ | 場面を単位に差し替えられない。`script` は場面ごとに包んで、名前の衝突も防ぐ |
| 全部を場面に分け、共通の部品を認めない | 額縁・場面をまたぐ主役・場面を束ねる動きがある見本は、場面に分けられない。共通の部品は残す |

## 効いてくる制約

- 場面を束ねる動きの多い台本（見本の一方）は、ほとんどが共通の `script`・`style` に残る。ページ単位で直せるのは `html`・`talk`・`thumb` などで、その台本の編集の細かさは台本の作りで決まる
- **場面をまたぐ参照の検査は「うっかり」を止める補助。** 空白・コメントをはさんだ `getElementById ("…")` なども見るが、文字列をつないで id を作るなど、わざと抜ける書き方は止めない。場面のコードは発表の画面の sandbox の iframe の中で動き、Quick Slide の API・セッションには届かない前提
- 発表の画面の CSP は、場面の hook・script を動かすために `script-src` に `'unsafe-eval'`（`new Function`）が要る
- JS の文法の検査は `node` があるときだけ（無ければ全拍の検査の JS エラーで分かる）
- 全拍の検査は拍の数に比例して時間がかかる（1拍あたり約 2 秒。`--wait` で縮められるが飛ぶ動きが終わる長さは要る）

## 見直す条件

- Quick Slide が場面1つだけを差し替えるようになったら、場面の `script` と共通の `script` の境目（名前の共有）を見直す
