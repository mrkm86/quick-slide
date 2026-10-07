# 0003 名前を html-pptx から html-slide にする

- 日付: 2026-10-03
- 状態: 採用

## 決めたこと

1. スキル名を **`html-slide`** にする（旧名 `html-pptx`）。入口は `scripts/html_slide.py`、設定と出力の置き場のフォルダ名も `html-slide`（置き場の根っこは [ADR 0015](0015-places-under-quick-slide.md)）、環境変数は `HTML_SLIDE_*`
2. 名前は「作り方-媒体」で揃える: **html-slide**（HTML で組む資料）／ **image-slide**（画像生成で1枚丸ごと描く資料）／ **html-video**（HTML で組む動画）。quick-slide のプレゼンモードが html-slide、ピッチモードが image-slide
3. description の発動語に「html-pptx（旧名）」を残す

## 理由

- 資料のエンジンを2つ（HTML で組む／画像生成で描く）並べるので、名前を揃える
- 出口は PPTX だけではない（確認用の PNG・HTML。quick-slide は PDF も出す）。ファイル形式を名前にすると、出口が増えたときにずれる
- 画像生成の側を `image-pptx` と呼ぶと「編集できる PPTX」に読めるが、中身は絵を並べたもの。媒体の名前なら誤解が無い
- 作ったばかりで、ほかのスキルからは呼ばれていない。直すなら今が一番安い

## 却下した案

| 案 | 却下の理由 |
|---|---|
| `html-pptx` のまま | 上の理由。image 側と揃えると `image-pptx` になり、誤解を生む |
| `html-deck` | quick-slide・html-video と語が揃わない |

「html-slide」は reveal.js のような HTML のスライドを作る道具にも読める。description の頭で「編集できて動く PPTX を作る」と書いて補う。
