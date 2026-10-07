# 機体に置く手順（html-slide）

字体は PowerPoint for Mac に同梱のファイルを読む（無い機体では設定でパスを渡す）。下の依存を揃えれば、どの機体にも置ける。
Quick Slide から使うなら、`python3 <プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py --doctor` が足りないものを言い、
`--setup` が管理者の権限なしで入れられるものを入れる（下の「Quick Slide の置き場」へ）。

## 依存

| 何 | どこ | 備考 |
|---|---|---|
| Python 3 ＋ python-pptx・lxml・Pillow・PyYAML | システムの python3 | `python3 -c "import pptx, lxml, PIL, yaml"` |
| Node | PATH の `node`（設定の `node_bin`・環境変数 `HTML_SLIDE_NODE` で替えられる） | 18 以上 |
| puppeteer-core | 下の「在りかの探し方」 | スキルの中に node_modules を置かない |
| Chrome（headless shell） | 下の「在りかの探し方」 | Chrome そのもの（GUI）ではなく、`chrome-headless-shell` |
| 字体（游ゴシック） | `/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/YuGoth*.ttc` | PowerPoint for Mac に同梱。無い機体では設定の `fonts:` でパスを渡す（ADR 0002） |
| LibreOffice（任意） | `/opt/homebrew/bin/soffice` | `verify --odp` と確認用の PDF に使う。無ければその照合だけ飛ばす |

## 在りかの探し方（ADR 0016・`scripts/browser.py`）

上から順に、最初に当たったものを使う。`<根っこ>` はデータの置き場（既定 `~/.quick-slide/`・環境変数 `QUICK_SLIDE_HOME`）。

| 何 | 1. 環境変数 | 2. 設定 | 3. Quick Slide の置き場 | 4. 前からの置き場 |
|---|---|---|---|---|
| Node のモジュール | `HTML_SLIDE_NODE_MODULES` | `node_modules` | `<根っこ>/engines/html-slide/node/node_modules` | `~/.cache/html-video/node_modules` |
| Chrome | `HTML_SLIDE_CHROME`（実行ファイル） | `chrome`（実行ファイルか入れたフォルダ） | `<根っこ>/engines/html-slide/chrome/` | `~/.cache/puppeteer/` |

手で入れるなら（管理者の権限は要らない）:

```bash
npm install --prefix <根っこ>/engines/html-slide/node puppeteer-core
npx @puppeteer/browsers install chrome-headless-shell@stable --path <根っこ>/engines/html-slide/chrome
python3 <このスキルのフォルダ>/scripts/browser.py      # 見つけたものを JSON 1 行で出す
```

足りないまま `deck`・`check`・`revise` を流すと、絵を描く前に rc=4 で止まり、何が無いかを言う。

## 設定

データの置き場の `engines/html-slide/config.yaml`（雛形はスキルの `config.example.yaml`）。無くても既定値で動く。環境変数 `HTML_SLIDE_CONFIG` で別の場所を直に指せる

## 置いたら確かめる

```bash
cd <このスキルのフォルダ>
bash tests/run.sh
python3 scripts/html_slide.py deck --script examples/sample-deck.json --out /tmp/html-slide-smoke
```
