# 0001. 途中に HTML を挟み、描き出し係は「HTML の決まり」だけを知る

- **日付**: 2026-10-03
- **状態**: 採用

## 決めたこと

- 資料は **台本 → 型 → HTML → PPTX** の順に作る。型が書くのは HTML だけで、PPTX は描き出し係（このスキルの `scripts/extract.mjs`・`scripts/pptx_writer.py`）が作る
- 描き出し係は、型も題材も知らない。知っているのは [`references/contract.md`](../../references/contract.md)（見た目の許可リストと、動きの属性 `data-step` / `data-fx` / `data-delay` / `data-keep`）だけ
- 位置はブラウザ（Chrome）が計算した結果を読む。並べ方（flex・grid・絶対配置）は型の自由で、制限するのは見た目だけ
- アニメーションは PowerPoint の `<p:timing>` を直接書く。画面の切り替えはモーフを `mc:AlternateContent` で書き、未対応のアプリ用にフェードを併記する
- html-video が焼いた動画の HTML は、互換の経路（`from-video`：GSAP の時刻から段を決める）で同じ描き出し係に通す。html-video 側が決まりの属性を出すようになったら、この経路は消す

## 採らなかった案と、その理由

| 案 | 却下した理由 |
|---|---|
| 台本から python-pptx で直接組む | 動画（HyperFrames・HTML）と資料で型を二重に実装することになり、見た目がずれていく。HTML ならブラウザのプレビューがそのまま完成品になる（quick-slide の GUI に載せられる） |
| どんな HTML でも PPTX にする | グラデーション・変形・フィルタの多い Web ページは近似しかできず、黙って崩れる。許可リストで止める方が、崩れたことに気づける |
| 画像生成で1枚丸ごと描く | 編集できない・動かせない・文字の誤りを止められない。インパクト勝負の用途には残す（image-slide・quick-slide のピッチモード） |
| 文字を画像にして貼る | 編集できなくなる。このスキルの存在理由が消える |

## 効いてくる制約

- 型に許可リスト外の CSS（`transform`・`filter`・`clip-path`・`::before` の `content`・背景画像）を使わない。使うと `check` が rc=2 で止める
- 段の数とノートの［クリック］の数を一致させる。ずれると `verify` が rc=2
- PowerPoint の GUI を自動操作しない（AppleScript の書き出しがダイアログで止まり、使う人の画面に残る）

## 見直す条件

- html-video が決まりの属性を出すようになった → `from-video` の経路を消す
- `--import`（既存の PPTX を正本にして直す）を作るとき → 向きが逆（PPTX → HTML）なので、別の ADR で決める
