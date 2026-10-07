# 機体に置く手順（html-slide）

字体は PowerPoint for Mac に同梱のファイルを読む（無い機体では設定でパスを渡す）。下の依存を揃えれば、どの機体にも置ける。

## 依存

| 何 | どこ | 備考 |
|---|---|---|
| Python 3 ＋ python-pptx・lxml・Pillow・PyYAML | システムの python3 | `python3 -c "import pptx, lxml, PIL, yaml"` |
| Node ＋ puppeteer-core | `~/.cache/html-video/node_modules/` | html-video の `setup` が入れたものを借りる。スキルの中に node_modules を置かない |
| Chrome（headless shell） | `~/.cache/puppeteer/chrome-headless-shell/` | 同上 |
| 字体（游ゴシック） | `/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/YuGoth*.ttc` | PowerPoint for Mac に同梱。無い機体では設定の `fonts:` でパスを渡す（ADR 0002） |
| LibreOffice（任意） | `/opt/homebrew/bin/soffice` | `verify --odp` と確認用の PDF に使う。無ければその照合だけ飛ばす |

## 設定

データの置き場（既定 `~/.quick-slide/`・環境変数 `QUICK_SLIDE_HOME` で変わる）の `engines/html-slide/config.yaml`（雛形はスキルの `config.example.yaml`）。無くても既定値で動く。環境変数 `HTML_SLIDE_CONFIG` で別の場所を直に指せる

## 置いたら確かめる

```bash
cd <このスキルのフォルダ>
bash tests/run.sh
python3 scripts/html_slide.py deck --script examples/sample-deck.json --out /tmp/html-slide-smoke
```
