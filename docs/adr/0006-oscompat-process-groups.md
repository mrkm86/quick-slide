# 0006 OS の差は `oscompat.py` の 1 か所に寄せ、子は孫ごと止める（Windows は Job Object）

- 日付: 2026-10-13
- 状態: 採用
- 関係: Issue #1 の W1b（A2・A3・B1・B2・B3・N6）

## 決めたこと

mac・Linux と Windows で書き方が変わるものを、`oscompat.py` に寄せる。ほかのファイルは OS を見分けない。

| やること | 関数 | mac・Linux | Windows |
|---|---|---|---|
| 二重起動のロック | `lock_nowait` / `unlock` | `fcntl.flock` | `msvcrt.locking`（先頭の 1 バイト） |
| PID が生きているか | `pid_alive` | `os.kill(pid, 0)` | `OpenProcess` ＋ `GetExitCodeProcess` |
| 子を自分の組で起こす | `group_kwargs` | `start_new_session=True` | `CREATE_NEW_PROCESS_GROUP`・`CREATE_NO_WINDOW` |
| 子を孫ごと止める | `popen_group` / `kill_group` / `end_group` | 組に SIGTERM → 待つ → SIGKILL | 起こした直後に Job Object へ入れ、止めるときは Job ごと止める |
| 待ち受けの口を自分だけにする | `REUSE_ADDRESS` / `bind_exclusively` | `SO_REUSEADDR`（TIME_WAIT の口を使い直すだけ） | `SO_REUSEADDR` を使わず `SO_EXCLUSIVEADDRUSE`（Windows の `SO_REUSEADDR` は、ほかが待ち受けている口に重ねて bind できる。Windows 機の試験で見つかった N15） |

### 置き方

- **同じ中身のファイルを 3 か所に置く**: `src/quick_slide/oscompat.py`（サーバ）、`skills/html-slide/scripts/oscompat.py`、
  `skills/image-slide/scripts/oscompat.py`。エンジンは単体でも使うので、サーバを import させない
  （`places.py` と同じ扱い）。試験が 3 つをバイト単位で見比べる
- 標準ライブラリだけで書く（Windows の呼び出しは `ctypes`）

### Windows で孫ごと止める

- 台本係・描き係が起こす子（`popen_group`）は、起こした直後に **閉じると中を全部止める Job Object**
  （`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`）に入れる。子がそのあとに起こす孫も、同じ Job に入る
- 止めるときは Job ごと止める（`kill_group`）。子が終わったら Job を閉じる（`end_group`）。閉じたときに残っていた孫も止まる
- Job に入れられなかったときは、`taskkill /T /F /PID` に頼る
- **常駐のサーバ（`start` と再起動の後継）は Job に入れない**（`group_kwargs` だけで起こす）。起こした CLI が
  終わっても生き残らせるため

## 採らなかった案

- **`taskkill /T` だけで止める**: 親子の記録をたどるので、`codex.cmd` の cmd.exe が先に終わると、その下の node と
  codex.exe にたどり着けない。旧版の実測（B2）で、取り残された孫がパイプを握り、`run()` がタイムアウトを
  過ぎても返らなかった
- **子を一時停止の状態で起こしてから Job に入れる**: 孫が Job の外へ逃げる隙は無くなるが、止めたままの子を
  動かすには、主スレッドの握りを取り直す手間が要る。子が孫を起こすまでには cmd.exe・node の起動がはさまり、
  起こした直後に入れれば間に合う。間に合わなかったときも `taskkill /T` が残る
- **`CREATE_BREAKAWAY_FROM_JOB` で常駐のサーバを親の Job から外す**: 親の Job が外れることを許していないと、
  起動そのものが失敗する。旧版の実測（B3）では、Claude Code から起こしたサーバは外さなくても生き残った
- **OS の差をそれぞれのファイルで `if os.name == "nt"` で書く**: 同じ判断が 6 ファイルに散り、直し漏れが出る

## 影響

- mac・Linux の動きは変わらない（`kill_group` は今までの SIGTERM → SIGKILL と同じ。待つ時間も 5 秒）
- Windows は、Codex・Claude Code の子が孫を残さずに止まる。止まらない失敗（N6）が、タイムアウトで止まるようになる
- 確かめるのは Windows 機の試験（Issue #1 のコメントの手順）
