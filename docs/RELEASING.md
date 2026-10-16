# リリースの手順

バージョンの付け方・お知らせの仕組みは [`docs/adr/0020-release-and-update-notice.md`](adr/0020-release-and-update-notice.md)。
利用者の画面にお知らせが出るのは、**正式バージョンの GitHub Release を作ったとき**（下書き・プレリリースでは出ない）。

## ふだん

開発・修正は **main から切って main へ向ける**（リリースの PR が開いている間も同じ。[`docs/adr/0028-develop-on-main-short-release-branch.md`](adr/0028-develop-on-main-short-release-branch.md)）。
`git clone` もマーケットプレイスも main の先頭を取るので、main に入れる PR は、それだけで壊れない状態にする。

変更を main に入れる PR では、利用者に見える変化があれば `CHANGELOG.md` の「未リリース」に 1 行足す
（書き方は CHANGELOG.md の頭）。

## バージョンを出す

例は `2.1.0`。

1. main を最新にし、テストを全部通す（`AGENTS.md` §3）
   `python3 tools/public_check.py tree` で、出してはいけないものが無いことも見る（`AGENTS.md` §0）
2. リリースのブランチでバージョンを上げる

   ```bash
   git switch -c release/2.1.0
   python3 tools/release.py prepare 2.1.0   # バージョンを 3 か所で上げ、「未リリース」を 2.1.0 の節にする
   python3 tools/release.py check
   ```

   `CHANGELOG.md` の 2.1.0 の節を読み直す。**これがそのまま利用者の画面とリリースの頁に出る。**
   人名・社内の名前・顧客や他製品の名前・内部のパスを書かない（`AGENTS.md` §0）

3. コミット（`chore(release): 2.1.0`）して PR を出し、main に入れる
   この PR では Windows の試験は回らない（変わるのが CHANGELOG と版の置き場だけなので。`docs/adr/0023-ci-windows-first.md`）。
   回るのは公開前の検査と版の形の検査で、1 分ほどで終わる。試験は 1. で手元で通したものを頼りにする

   **この PR の上で直しを積まない。** 直しが見つかったら main へ入れ、このブランチに main を取り込む
   （または main から切り直して `prepare` をやり直す）。開いている時間を短くし、入れたらすぐ 4. へ進む
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

## 急ぎの直しだけを先に出す

main に、まだ出していない変更（次の版に入れるもの）があって、それより先に直しだけを出したいとき。例は 2.1.0 の次。

1. タグから切る: `git switch -c release/2.1.1 v2.1.0`
2. 直しだけを入れる（main に先に入れて `git cherry-pick <コミット>`）
3. 上の「バージョンを出す」の 2. から進める（番号は出した順に付ける。main に溜まっている変更は、その次の版になる）
4. CHANGELOG の 2.1.1 の節を main にも入れる（入れ忘れると、main の次の版で直しが無かったように見える）

`2.1.0a` のような番号は使わない（SemVer の形でない。`release.py check` が通らない）。

## 試しに出す（プレリリース）

バージョンを `2.1.0-rc.1` のようにし、`gh release create … --prerelease` で作る。`releases/latest` に入らないので、
利用者にお知らせは出ない。

## 間違えたとき

- 本文を直す: GitHub Release を編集する（`gh release edit v2.1.0 --notes-file -`）。画面は本文を写さずリリースの頁へリンクするので、直せばそのまま見える
- 出したバージョンを取り下げる: Release を削除するか下書きに戻す。前の Release が「最新」に戻り、お知らせもそれに従う。
  タグは消さず、直したバージョンを次の番号で出す
