# リリースの手順

バージョンの付け方・お知らせの仕組みは [`docs/adr/0020-release-and-update-notice.md`](adr/0020-release-and-update-notice.md)。
利用者の画面にお知らせが出るのは、**正式バージョンの GitHub Release を作ったとき**（下書き・プレリリースでは出ない）。

## ふだん

変更を main に入れる PR では、利用者に見える変化があれば `CHANGELOG.md` の「未リリース」に 1 行足す
（書き方は CHANGELOG.md の頭）。

## バージョンを出す

例は `2.1.0`。

1. main を最新にし、テストを全部通す（`AGENTS.md` §3）
2. リリースのブランチでバージョンを上げる

   ```bash
   git switch -c release/2.1.0
   python3 tools/release.py prepare 2.1.0   # バージョンを 3 か所で上げ、「未リリース」を 2.1.0 の節にする
   python3 tools/release.py check
   ```

   `CHANGELOG.md` の 2.1.0 の節を読み直す。**これがそのまま利用者の画面とリリースの頁に出る。**
   人名・社内の名前・顧客や他製品の名前・内部のパスを書かない（`AGENTS.md` §0）

3. コミット（`chore(release): 2.1.0`）して PR を出し、main に入れる
4. main の、そのコミットにタグを打って push する

   ```bash
   git switch main && git pull
   git tag -a v2.1.0 -m "Quick Slide 2.1.0"
   git push origin v2.1.0
   ```

5. GitHub Release を作る（本文は CHANGELOG の節）

   ```bash
   python3 tools/release.py notes 2.1.0 | gh release create v2.1.0 --title "Quick Slide 2.1.0" --notes-file -
   ```

6. 確かめる: 前のバージョンの Quick Slide で 設定 › 更新 の「今すぐ確かめる」を押し、2.1.0 のお知らせと変更点のリンクが出ること

## 試しに出す（プレリリース）

バージョンを `2.1.0-rc.1` のようにし、`gh release create … --prerelease` で作る。`releases/latest` に入らないので、
利用者にお知らせは出ない。

## 間違えたとき

- 本文を直す: GitHub Release を編集する（`gh release edit v2.1.0 --notes-file -`）。画面は本文を写さずリリースの頁へリンクするので、直せばそのまま見える
- 出したバージョンを取り下げる: Release を削除するか下書きに戻す。前の Release が「最新」に戻り、お知らせもそれに従う。
  タグは消さず、直したバージョンを次の番号で出す
