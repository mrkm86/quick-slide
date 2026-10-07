# 機体に置く手順（image-slide）

macOS の機体に置く前提（字体のパス・キャッシュの置き場が macOS のもの）。html-slide と同じ扱い。
Quick Slide から使うなら、`python3 <プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py --doctor` が足りないもの（Python のパッケージ・`codex` CLI・ChatGPT のログイン）を言う。`codex` CLI とログインは利用者が入れる・する（全体に入れるため）。

## 依存

| 何 | どこ | 備考 |
|---|---|---|
| Python 3 ＋ python-pptx・Pillow・PyYAML | システムの python3 | `python3 -c "import pptx, PIL, yaml"` |
| Codex CLI | `npm install -g @openai/codex` | ChatGPT のアカウントで1度だけ `codex login`。[codex.md](codex.md) |
| 字体（游ゴシック） | `/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/YuGoth*.ttc` | 確認用の PNG に帯の文字を焼くときだけ使う。無ければヒラギノ。PPTX にはテーマの字体の名前で入る |

## 設定

データの置き場（既定 `~/.quick-slide/`・環境変数 `QUICK_SLIDE_HOME` で変わる）の `engines/image-slide/config.yaml`（雛形はスキルの `config.example.yaml`）。無くても既定値で動く。環境変数 `IMAGE_SLIDE_CONFIG` で別の場所を直に指せる

## 置いたら確かめる

```bash
cd <このスキルのフォルダ>
bash tests/run.sh                                                     # 本物の Codex は呼ばない
python3 scripts/image_slide.py auth                                    # ログインの確認だけ
python3 scripts/image_slide.py deck --script examples/sample-pitch.json --out /tmp/image-slide-smoke   # 5枚描く（サブスク枠を使う）
```
