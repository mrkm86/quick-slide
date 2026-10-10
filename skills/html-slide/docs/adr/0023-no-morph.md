# 0023 モーフ（変形）の切り替えをやめる

- 日付: 2026-10-11
- 状態: 採用

## 決めたこと

利用者の方針: **html-slide は、HTML で表現できない変形などのアニメーションを、最初からスライドにも表現しない。**

1. 切り替え（`transition`）は **`fade`（既定）と `none`** だけ。1枚目も中身のページも fade。`mode: handout` は今までどおり none（動きも切り替えも付けない）
2. **`morph` は退役**。台本・スタイル（`motion.transition`）・決まりの HTML（`data-transition`）に書いてあっても止めず、fade として描き、警告する（rc は変えない）
   - 台本: `deck.RETIRED_TRANSITIONS`・警告は「N枚目: transition: morph は退役した…」
   - スタイル: `deck_style.RETIRED_TRANSITIONS`（ADR 0021 の `RETIRED_LOOKS` と同じ作法。読んだとき注意を出す）。組み込みの `visual.yaml` は fade に替えた
   - 決まりの HTML: `extract.mjs` の `retired-transition`（warn）
3. PPTX は**モーフを書き出さない**（`pptx_writer.MORPH` を消した。`morph` と渡されたら fade）。`verify` は PPTX にモーフが出ていれば NG
4. 動画から作る PPTX（`from_video`）も、切り替えは全部 fade
5. 台本係の一覧・`deck-format.md`・`contract.md`・`parts-format.md` から morph を外した。`data-keep`（図形名 `!!名前`）は、画面をまたいで同じ部品だと分かる名前として残す（ロゴ・透かしの扱いは変えない）
6. **効果（`data-fx`）は残す**: `rise`・`fade`・`pop`・`wipe-right`・`wipe-left`・`wipe-up`・`wipe-down`・`wheel`。ブラウザで CSS で再現できる。`contract.md` に「この効果と切り替えは、ブラウザの再生（Quick Slide の発表の画面）でも同じに出せるものだけ」を足した

## 理由

- Quick Slide の発表の画面は、ブラウザで `data-step`・`data-fx` を再生する。PowerPoint のモーフ（同じ名前の図形どうしを対応させて動かす）は、ブラウザで同じに再現できない
- 発表の画面と PPTX で見た目をそろえる。再現できない動きを最初から作らないほうが、台本の書き方も検査も単純になる

## 見送ったこと

- 古い台本の morph を rc=2 にすること: 過去のデッキを組み直せなくなる。fade で描いて警告にとどめる
- `!!名前` の廃止: ロゴ・透かし・上の帯などの部品名として使っており、動きと関係なく役に立つ
