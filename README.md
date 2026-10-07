# Quick Slide v2

台本（資料用の JSON）から16:9の日本語スライドを作り、PPTX・PDF・ページごとの画像・サムネイル一覧まで書き出すプラグイン。
Claude Code と Codex のどちらにも載る。

- **画像は ChatGPT のサブスク枠で描く。** Codex 組み込みの画像生成（gpt-image-2）を使い、API の従量課金を前提にしない
- **エンジンは CLI から単体で呼べる。** GUI（作り直し中）を通さなくても、スキルとして使える

## エンジン

| スキル | 作るもの |
|---|---|
| [`skills/html-slide/`](skills/html-slide/SKILL.md) | 文字も図形も PowerPoint の部品として組んだ、あとから直せて動く PPTX（プレゼンモード） |
| [`skills/image-slide/`](skills/image-slide/SKILL.md) | 同じ台本を画像生成で1枚ずつ丸ごと描いた資料（ピッチモード） |

2つは同じ台本を受ける。設計判断はそれぞれの `docs/adr/`。

## 置き場

| 何 | 既定 | 変え方 |
|---|---|---|
| データ（設定・単体で使ったときの出力） | `~/.quick-slide/` | 環境変数 `QUICK_SLIDE_HOME` |
| エンジンの設定 | `~/.quick-slide/engines/<スキル>/config.yaml` | `HTML_SLIDE_CONFIG`・`IMAGE_SLIDE_CONFIG` |
| 描いた絵のキャッシュ（上限つき） | `~/Library/Caches/quick-slide/<スキル>/` | `HTML_SLIDE_CACHE`・`IMAGE_SLIDE_CACHE`・設定の `cache_dir` |

置き場はエンジンの `scripts/places.py` の1か所だけで決める。

## 依存

- macOS（Windows・Linux はまだ）
- Python 3 ＋ python-pptx・lxml・Pillow・PyYAML
- `codex` CLI（ChatGPT でログイン済み）
- html-slide: Node ＋ puppeteer-core と Chrome（headless shell）、PowerPoint for Mac 同梱の游ゴシック。手順は [`skills/html-slide/references/deploy.md`](skills/html-slide/references/deploy.md)

## 状態

- エンジン2つを載せた（このリポの最初の形）
- GUI（ブラウザで動く常駐ダッシュボード）は v1 から写して、エンジンにつなぎ直す途中

作業に入る前に [AGENTS.md](AGENTS.md) を読むこと。

## ライセンス

未定。
