# 0017 Codex から使うとき: サーバはターミナルで先に起こす。CLI は PATH の外も探し、診断は入れる前にターミナルで確かめてもらう

- 日付: 2026-10-14
- 状態: 採用
- 関係: Issue #25・`docs/adr/0005-pitch-needs-codex.md`・`src/quick_slide/clidirs.py`・`skills/quick-slide/scripts/quick_slide_setup.py`・
  `skills/quick-slide/scripts/quick_slide_start.py`

## 起きたこと

Windows の機体で Codex にプラグインを入れて使うと、Codex の砂場（`codex exec -s workspace-write`）から `codex`・`claude` が見えず、
診断（`--doctor`）は「見つかりません」と言い、Codex は入っているものを `npm install -g @openai/codex`・`codex login` で入れ直すよう案内した。

このブランチの 1 回目の版を Windows 機（Codex の `[windows] sandbox = "elevated"`）で確かめると、さらに分かった:

- 砂場の PATH には `C:\nvm4w\nodejs` が入っていた。見えないのは、そこが**別の利用者のフォルダを指す symlink で、砂場から辿れない**ため。
  1 回目の版の `clidirs` は、その置き場で `Path.is_dir()` が `PermissionError: [WinError 5]` を投げ、診断もサーバの子も落ちた
- 砂場の中では `py -3` が `No installed Python found!` と言う（利用者ごとに入れた Python の登録が見えない）。代わりの `python` は
  PATH の先にある古い 3.11 で、サーバは `sqlite3.connect(autocommit=)` が無くて起きない。診断は 3.10 以上を「OK」と言っていた
- **砂場は作業場と `%TEMP%` にしか書けないので、既定の置き場（`~/.quick-slide`）ではサーバが起きない。** Codex は回り道を 6 段試して 7 分かかった
- 砂場に付く印は `CODEX_SANDBOX_NETWORK_DISABLED`・`CODEX_THREAD_ID`・`CODEX_SESSION_ID`（**`CODEX_SANDBOX` は付かない**）。
  mac の砂場では 4 つとも付く。手元（127.0.0.1）への通信は Windows の砂場でも通った

## 決めたこと

1. **Codex から使うときは、サーバを利用者のターミナルで先に起こしておく**（利用者の選択、2026-10-14）
   - `quick_slide_start.py --start-server` を足した（サーバを起こすだけ。投入しない。動いていればそのまま）
   - 砂場の中（下の印がある）で起動に失敗したら、`code: start_in_terminal` と、ターミナルで流すコマンド（`command`。
     Windows は `py -3`、ほかは `python3` で書く。砂場の中の Python は古い別物のことがある）を返す。SKILL.md に、回り道をせずに
     そのコマンドを利用者に渡すと書く
   - 入口の「人に起動してくださいと言わない」は、砂場の外ではそのまま。砂場の中では起こせないので、この 1 つだけ例外にする
2. **CLI は PATH で見つからなければ、よくある置き場も探す**（`clidirs.py`）。在るものだけを PATH の**後ろ**に足す（利用者の PATH が先に効く）。
   読めない置き場は無いものとみなす（`PermissionError` で全体を落とさない）
   - mac・Linux: npm の prefix（`.npmrc`・`NPM_CONFIG_PREFIX`）・`~/.local/bin`・`~/.claude/local`・`~/.npm-global/bin`・`~/.volta/bin`・
     `NVM_BIN`・`~/.nvm/versions/node/*/bin`（新しい版から）・`/opt/homebrew/bin`・`/usr/local/bin`
   - Windows: npm の prefix・`%APPDATA%\npm`・`%NVM_SYMLINK%`・`%ProgramFiles%\nodejs`・`%LOCALAPPDATA%\Volta\bin`・`C:\nvm4w\nodejs`・
     `%USERPROFILE%\.local\bin`
   - 使う場所: サーバが台本係の CLI を探すとき（`worker/tools.py`）・子に渡す環境（`childenv.child_env`）・診断（`node`・`npm`・
     `codex`・`claude`・`npx`。診断が子を起こすときの PATH も同じ）
   - 環境変数 `QUICK_SLIDE_CLI_DIRS` があれば、よくある置き場の代わりにそれ（の在るもの）を見る。空なら足さない。試験はこれを
     空にして、機体にある本物の CLI を見ない
   - 同じ中身のファイルを `src/quick_slide/clidirs.py` と `skills/quick-slide/scripts/clidirs.py` に置く（診断はサーバを import しない）。
     試験がバイト単位で見比べる
3. **診断は Codex の中で走ったと分かれば、入れる案内をしない。** 印は `CODEX_SANDBOX`・`CODEX_SANDBOX_NETWORK_DISABLED`・
   `CODEX_THREAD_ID`・`CODEX_SESSION_ID` のどれか（利用者の選択、2026-10-14。Windows は `CODEX_SANDBOX` が付かないので、会話の印も見る）。
   Node・`codex`・台本を書く CLI が見つからないときの `fix` を「利用者のターミナルで `--doctor` を走らせて確かめて。そこでも
   見つからなければ〜」にする。JSON に `inside_codex` を足す
4. **Python は 3.12 以上**（診断の下限を 3.10 から上げた。サーバが 3.12 の `autocommit=` を使う）。サーバが起きず Python が古ければ、
   砂場の外では `code: python_too_old` で言う（`server_unavailable` だけでは `server.out` を見ないと分からなかった）。SKILL.md に、
   `py -3` が使えないときは `python` の前に `%LOCALAPPDATA%\Programs\Python\Python3*\python.exe` を試す、を足した

## 却下した案

- **CLI を探すだけで、砂場の中からもサーバを起こす**（1 回目の版）: 置き場に書けないので起きない（Windows で確かめた）
- **Codex の設定で書ける場所（`writable_roots`）に置き場・`~/.codex`・`~/.claude` を足す案内**: 設定が重く、Windows の elevated の
  砂場で効くかも分からない
- **砂場を外して使う案内（`-s danger-full-access`）**: Codex の保護を外すことになる
- **診断が利用者のターミナルで見つけたパスを設定（`claude_bin`・`codex_bin`）に書いておく**: 設定の口が増え、CLI を入れ直したり
  動かしたりすると古いパスが残る。サーバをターミナルで起こすなら、そのサーバは利用者の PATH を受け継ぐので要らない
- **エンジンに codex の探し方を足す**: エンジンは PATH で探すので、子の PATH に置き場を足せば足りる（探し方の正本はエンジンのまま。ADR 0005）

## 残したこと

- 砂場の中で走る `quick_slide_start.py` 自体は、サーバに投げるだけなので、砂場の中の Python が古くても動く（標準ライブラリだけ・3.10 で読める書き方）
- 利用者のターミナルを閉じたあとも、サーバが動き続けるか（Windows）は、この判断とは別に確かめる
