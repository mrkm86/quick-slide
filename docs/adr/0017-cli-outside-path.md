# 0017 CLI は PATH の外のよくある置き場も探す。Codex の中の診断は、入れる前にターミナルで確かめてもらう

- 日付: 2026-10-14
- 状態: 採用
- 関係: Issue #25・`docs/adr/0005-pitch-needs-codex.md`・`src/quick_slide/clidirs.py`・`skills/quick-slide/scripts/quick_slide_setup.py`

## 起きたこと

Windows の機体で Codex にプラグインを入れて使うと、Codex の砂場（`codex exec -s workspace-write`）の PATH に npm のグローバルの
置き場が無く、`codex`・`claude` が見えなかった。診断（`--doctor`）は「見つかりません」と言い、Codex は入っているものを
`npm install -g @openai/codex`・`codex login` で入れ直すよう案内した。サーバを砂場から初めて起こしていれば、子（台本係・描き係）も
同じ PATH を受け継いで止まっていたはず。

## 決めたこと

1. **PATH で見つからなければ、よくある置き場も探す**（`clidirs.py`）。在るものだけを PATH の**後ろ**に足す（利用者の PATH が先に効く）
   - mac・Linux: npm の prefix（`.npmrc`・`NPM_CONFIG_PREFIX`）・`~/.local/bin`・`~/.claude/local`・`~/.npm-global/bin`・`~/.volta/bin`・
     `NVM_BIN`・`~/.nvm/versions/node/*/bin`（新しい版から）・`/opt/homebrew/bin`・`/usr/local/bin`
   - Windows: npm の prefix・`%APPDATA%\npm`・`%NVM_SYMLINK%`・`%ProgramFiles%\nodejs`・`%LOCALAPPDATA%\Volta\bin`・`C:\nvm4w\nodejs`・
     `%USERPROFILE%\.local\bin`
   - 使う場所: サーバが台本係の CLI を探すとき（`worker/tools.py`）・子に渡す環境（`childenv.child_env`）・診断（`node`・`npm`・
     `codex`・`claude`・`npx`。診断が子を起こすときの PATH も同じ）
   - 環境変数 `QUICK_SLIDE_CLI_DIRS` があれば、よくある置き場の代わりにそれを見る（空なら足さない）。試験はこれを空にして、
     機体にある本物の CLI を見ない
   - 同じ中身のファイルを `src/quick_slide/clidirs.py` と `skills/quick-slide/scripts/clidirs.py` に置く（診断はサーバを import しない）。
     試験がバイト単位で見比べる
2. **診断は Codex の中で走ったと分かれば、入れる案内をしない。** 印は `CODEX_SANDBOX`・`CODEX_SANDBOX_NETWORK_DISABLED`・
   `CODEX_THREAD_ID`・`CODEX_SESSION_ID` のどれか（利用者の選択、2026-10-14。Windows は砂場の印が付かないことがあるので、会話の印も見る）。
   Node・`codex`・台本を書く CLI が見つからないときの `fix` を「利用者のターミナルで `--doctor` を走らせて確かめて。そこでも
   見つからなければ〜」にする。JSON に `inside_codex` を足す。mac の Codex の砂場では 4 つとも付くのを確かめた
3. **SKILL.md に「Codex から使うとき」を足す。** `inside_codex` が真なら、入れる案内をすぐ会話に書かない

## 却下した案

- **診断が利用者のターミナルで見つけたパスを設定（`claude_bin`・`codex_bin`）に書いておく**: 設定の口が増え、CLI を入れ直したり
  動かしたりすると古いパスが残る。診断を一度ターミナルで流してもらう手間も要る
- **砂場の中からはサーバを起こさず、利用者に起こしてもらう**: 「人に起動してくださいと言わない」入口の決まりとぶつかる
- **エンジンに codex の探し方を足す**: エンジンは PATH で探すので、子の PATH に置き場を足せば足りる（探し方の正本はエンジンのまま。ADR 0005）

## 残したこと

- 砂場の中から起こしたサーバの子は、砂場の制限も受け継ぐ（mac の seatbelt は子にも効く）。CLI が見つかっても、置き場や
  `~/.codex`・`~/.claude` に書けずに止まることがありうる（未確認）。手元への通信が塞がれる件は `server_unreachable` が言う
- Windows の Codex の砂場で、どの印が付くかは未確認（Issue #25 の確かめ方で見る）
