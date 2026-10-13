---
name: illust
description: 資料やスライドに置く挿絵（中身を表す動かない絵）を SVG で描き、確かめて、描いてよい形に絞り、PNG にするスキル。画像生成（Codex）は要らない。アイコンを組み合わせる描き方と、丸・角丸・太い線だけで人や物や場面を描く描き方の 2 つを持ち、ブランドの色から挿絵の配色を作る。`<icon name="users" …/>` と書けば共有のアイコン（Material Symbols）の形に展開する。スクリプト・外への参照・画像の埋め込みは落とす。「挿絵を描いて」「イラストを SVG で」「この場面の絵を描いて」「挿絵を PNG にして」「illust」等で発動。※写真風・水彩などの質感のある絵は画像生成（html-slide・image-slide の Codex）の領分。構造・手順・数字は図解（html-slide の型・motion-slide の場面）の領分。
---

# illust — 挿絵を SVG で描く

挿絵の SVG を書く → `illust.py` が確かめて絞る → PNG にする（HTML ならそのまま置く）。
描き方・色・書かないものは [`references/authoring.md`](references/authoring.md)。設計判断は [`docs/adr/`](docs/adr/)。

> **テスト**: `bash tests/run.sh`

## 使い方

```bash
python3 scripts/illust.py palette --primary "#006f83"          # ブランドの色から挿絵の配色（JSON）
python3 scripts/illust.py check --svg 挿絵.svg                 # 確かめるだけ（NG: の行。rc=2）
python3 scripts/illust.py clean --svg 挿絵.svg --out 絞った.svg  # アイコンを展開して絞った SVG（HTML に置く）
python3 scripts/illust.py png --svg 挿絵.svg --out 挿絵.png     # PNG（長い辺 1920。Chrome で描く）
python3 scripts/illust.py icons                                # 使えるアイコンの名前
```

- 終了コード: 0 = できた / 2 = SVG を直す（`NG:` の行） / 4 = Chrome が無い / 1 = そのほか
- Chrome は `--chrome`・環境変数 `ILLUST_CHROME`・PATH・よくある置き場（puppeteer の置き場など）の順に探す
- 状態を持たない。ほかのスキルを呼ばない（アイコンの形は共有の置き場 `skills/dads/icons` を読むだけ）

## 使われ方

- Quick Slide は、台本の画像の枠に書かれた挿絵（`image: {"svg": "<svg …>"}`）をこのスキルで PNG にしてから、html-slide に
  画像のファイルとして渡す（エンジンは変えない・素材はファイルで受ける）
- motion-slide の場面に置くときは、`clean` の出した SVG をそのまま場面の HTML に置ける
