"""Codex（ChatGPT のサブスク枠の gpt-image-2）で絵を描く。認証は1回の実行で1度だけ、描くのは並列（ADR 0002）。

## 認証（check_auth）— 実行の最初に1度だけ

1. 実ホーム（`$CODEX_HOME` か `~/.codex`）の `auth.json` があり、ChatGPT のログインであること
   （API キーのログインは従量課金になるので断る）。`codex login status` も1回だけ見る
2. トークンが新しいか（アクセストークンの期限まで MIN_LEFT_HOURS 時間以上。期限が読めないときだけ、最後の更新から REFRESH_DAYS 日以内）。
   古ければ、**実ホームで1回だけ** codex を走らせて更新させる（直列・1プロセス）。更新されなければ rc=4。
   Codex（0.159.2）は期限が読めるトークンを**期限の5分前まで更新しない**（`should_refresh_proactively`・
   openai/codex `codex-rs/login/src/auth/manager.rs:3004-3026`）。最後の更新からの日数（8日）は期限が読めないときの予備。
   だから日数では判定しない（日数で判定すると、7日目から期限まで更新されずに毎回 rc=4 になった・2026-10-06）
3. `auth.json` の中身を1度だけ読み、並列の各ワーカーに**コピー**を渡す

各ワーカーは使い捨ての隔離ホーム（コピーした `auth.json`・最小の設定・空の作業フォルダ・`-s read-only`）で
`codex exec` を1回だけ走らせ、`<隔離ホーム>/generated_images/` から絵を拾う。実 `~/.codex/auth.json` は書き換えない。

- 隔離ホームを共有しない・symlink にしない: 並列のワーカーが同じ refresh token で同時に更新すると、
  使い捨ての refresh token を奪い合って失効する（`refresh_token_reused` / `token_revoked` が実際に出た）
- 新しいトークンを配るので、ワーカーの中で更新は起きない見込み。起きたら（コピーが書き換わったら）数えて警告する
- 失効の文言が出たら、残りのページを止めて rc=4（同じ失効を並列で叩き続けない）

## 並列（run_parallel）

ThreadPoolExecutor で max_parallel 本まで同時に回す（中身は子プロセスなので、スレッドで足りる）。
枠切れ（429・usage limit）は全ワーカーを一緒に待たせる（pause_until）。待ちが長すぎるなら止めて rc=1。
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

REFRESH_DAYS = 7          # 期限が読めないトークンだけの予備。codex はそのとき最後の更新から8日で更新しに行く（manager.rs の TOKEN_REFRESH_INTERVAL）
MIN_LEFT_HOURS = 3        # 期限までこれより短ければ、先に更新する（ワーカーの中で期限が来ると、並列で同じ refresh token を奪い合う）
MAX_RATE_WAIT = 15 * 60   # 枠切れの待ちがこれより長ければ、待たずに止める（描けたページは覚えている）

AUTH_DEAD = re.compile(r"token_revoked|refresh_token_reused|refresh token.*(?:expired|invalid)|sign in again|"
                       r"Not logged in|401 Unauthorized", re.I)
# 裸の 429 は使わない（「429.png」を枠切れと読む）。HTTP の 429 は「429 Too Many Requests」や「status 429」の形で見る
RATE = re.compile(r"usage\s+limit|rate[\s_-]*limit|\b429\s+Too\s+Many\s+Requests|status(?:\s*code)?[\s:=]*429\b|"
                  r"too\s+many\s+requests|try again (?:at|in)", re.I)

WRAPPER = (
    "Use only the built-in image generation tool, through the ChatGPT login. Generate exactly ONE image, "
    "{shape}. Pass the design instruction below to the image tool in full, without summarizing or "
    "translating it. Do not run shell commands. Do not read, write or copy files. Do not use API keys or HTTP APIs, "
    "and do not draw programmatically. The calling program collects the generated image by itself. "
    "If generation fails, stop and report it."
)
WRAPPER_REFS = ("The attached image(s) are reference images. Pass them to the image generation tool as references, "
                "as the design instruction says.")


class CodexError(Exception):
    """kind: auth（止める・rc=4）/ rate（枠切れ）/ gen（描けなかった）/ timeout / band（帯が空いていない）/ stopped（止めた）。"""

    def __init__(self, kind: str, detail: str = "", retry_after: int | None = None):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind, self.detail, self.retry_after = kind, detail, retry_after


# --- 認証 ---------------------------------------------------------------------------

def real_home() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CODEX_HOME") or pathlib.Path.home() / ".codex").expanduser()


def find_cli(cfg: dict) -> str:
    cli = os.environ.get("HTML_SLIDE_CODEX") or cfg.get("codex_bin") or shutil.which("codex")
    if not cli or not shutil.which(cli):
        raise CodexError("auth", "codex が見つからない（npm install -g @openai/codex、設定の codex_bin でも指せる）")
    return shutil.which(cli)


def _env(home: pathlib.Path | None = None) -> dict:
    # OPENAI_API_KEY が残っていると、codex は API の従量課金に切り替わる。サブスク枠に固定するため渡さない
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "CODEX_API_KEY")}
    if home is not None:
        env["CODEX_HOME"] = str(home)
    return env


def _jwt_exp(token: str) -> datetime | None:
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return datetime.fromtimestamp(json.loads(base64.urlsafe_b64decode(p))["exp"], timezone.utc)
    except Exception:
        return None


def _parse_time(v) -> datetime | None:
    """ISO 8601 の時刻。タイムゾーンが無ければ UTC とみなす（codex は Z 付きで書く）。読めなければ None。"""
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def freshness(raw: bytes, now: datetime | None = None) -> dict:
    """auth.json の中身から、ChatGPT のログインか・新しいか（トークンそのものは返さない）。形が崩れていても例外にしない。"""
    now = now or datetime.now(timezone.utc)
    try:
        a = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        a = None
    if not isinstance(a, dict):
        return {"chatgpt": False, "fresh": False, "why": "auth.json が読めない。`codex login` で入り直す"}
    tokens = a.get("tokens")
    if not isinstance(tokens, dict) or not isinstance(tokens.get("refresh_token"), str) or not tokens["refresh_token"]:
        return {"chatgpt": False, "fresh": False, "why": "ChatGPT のログインではない（API キーのログインは従量課金になる）"}
    last = _parse_time(a.get("last_refresh"))
    age_days = (now - last).total_seconds() / 86400 if last else None
    access = tokens.get("access_token")
    exp = _jwt_exp(access) if isinstance(access, str) else None
    left_h = (exp - now).total_seconds() / 3600 if exp else None
    if left_h is not None:      # codex と同じく、期限が読めれば期限だけで決める
        fresh = left_h > MIN_LEFT_HOURS
    else:
        fresh = age_days is not None and age_days < REFRESH_DAYS
    return {"chatgpt": True, "fresh": fresh, "age_days": None if age_days is None else round(age_days, 2),
            "hours_left": None if left_h is None else round(left_h, 1)}


def _run_auth_cmd(cmd: list, timeout: int, what: str) -> subprocess.CompletedProcess:
    """認証の確認で走らせる codex。起動できない・時間切れも CodexError（rc=4）にする。"""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, env=_env(), timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise CodexError("auth", f"{what}が {timeout} 秒で終わらなかった")
    except OSError as e:
        raise CodexError("auth", f"{what}で codex を起動できなかった: {e}")


def _read_auth(path: pathlib.Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as e:
        raise CodexError("auth", f"{path} が読めない（{e.__class__.__name__}）。`codex login` で入り直す")


def check_auth(cli: str, log=print) -> dict:
    """実行の最初に1度だけ呼ぶ。{snapshot（auth.json の中身）, report（build.json に書く・秘密は含まない）}。"""
    home = real_home()
    path = home / "auth.json"
    if not path.is_file():
        raise CodexError("auth", f"{path} が無い。`codex login` で ChatGPT のアカウントに1度だけログインする")
    r = _run_auth_cmd([cli, "login", "status"], 30, "ログインの確認")
    if "Logged in using ChatGPT" not in (r.stdout + r.stderr):
        raise CodexError("auth", "codex が ChatGPT でログインしていない。`codex login` で1度だけログインする")
    raw = _read_auth(path)
    f = freshness(raw)
    if not f["chatgpt"]:
        raise CodexError("auth", f["why"])
    report = {"checked": 1, "refreshed_here": False, **{k: f[k] for k in ("age_days", "hours_left")}}
    if not f["fresh"]:
        log(f"Codex のトークンが古い（最後の更新から {f['age_days']} 日・残り {f['hours_left']} 時間）。1回だけ更新する")
        work = tempfile.mkdtemp(prefix="html-slide-auth.")
        try:
            _run_auth_cmd([cli, "exec", "--skip-git-repo-check", "-s", "read-only", "-C", work,
                           "-c", "model_reasoning_effort=low", "Reply with the single word: ok"], 180, "トークンの更新")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        raw = _read_auth(path)
        f = freshness(raw)
        if not f["fresh"]:
            raise CodexError("auth", "トークンを更新できなかった（codex は期限の5分前まで自分では更新しない）。`codex login` で入り直す")
        report.update(refreshed_here=True, age_days=f["age_days"], hours_left=f["hours_left"])
    return {"snapshot": raw, "report": report}


# --- 秘密を伏せる -----------------------------------------------------------------------

_SECRET_SHAPES = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]+|\bsk-[A-Za-z0-9_-]{16,}|\brt_[A-Za-z0-9_.-]{16,}")


def secrets_of(snapshot: bytes) -> list[str]:
    """auth.json に入っているトークンの値（ログに出たら伏せる）。"""
    try:
        a = json.loads(snapshot)
    except (ValueError, UnicodeDecodeError):
        return []
    out = []
    if isinstance(a, dict):
        for v in list((a.get("tokens") or {}).values() if isinstance(a.get("tokens"), dict) else []) + [a.get("OPENAI_API_KEY")]:
            if isinstance(v, str) and len(v) >= 12:
                out.append(v)
    return out


def redact(text: str, secrets: list[str]) -> str:
    for v in secrets:
        text = text.replace(v, "［伏せた］")
    return _SECRET_SHAPES.sub("［伏せた］", text)


# --- 1枚を描く ------------------------------------------------------------------------

def _flat_refs(refs: list[pathlib.Path], work: pathlib.Path) -> list[pathlib.Path]:
    """透過のある参照画像は白に合成して作業フォルダに写す（透明のまま渡すと、背景が黒く読まれるおそれがある。
    キャラの透過 PNG が主）。透過の無い絵はそのまま渡す。元のファイルは触らない。"""
    from PIL import Image
    out = []
    for i, r in enumerate(refs, 1):
        with Image.open(r) as im:
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                flat = Image.new("RGB", rgba.size, "white")
                flat.paste(rgba, mask=rgba.getchannel("A"))
                q = work / f"ref-{i}.png"
                flat.save(q)
                out.append(q)
                continue
        out.append(r)
    return out


def _private_home(snapshot: bytes) -> pathlib.Path:
    """使い捨ての隔離ホーム。作る途中で失敗したら、作りかけ（auth.json のコピー）を消してから投げ直す。"""
    home = pathlib.Path(tempfile.mkdtemp(prefix="html-slide-home."))
    try:
        home.chmod(0o700)
        fd = os.open(home / "auth.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(snapshot)
        # 実ホームの設定（プラグイン・フック・MCP）は持ち込まない。起動が軽く、ほかの作業に引きずられない
        (home / "config.toml").write_text('model_reasoning_effort = "low"\n', encoding="utf-8")
        (home / "generated_images").mkdir()
    except BaseException:
        shutil.rmtree(home, ignore_errors=True)
        raise
    return home


def _retry_after(text: str) -> int | None:
    m = re.search(r'retry[-_]after["\s]*[:=][\s"]*(\d+)', text, re.I) or re.search(r"try again in\s+(\d+)\s*s", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"try again in\s+(\d+)\s*(?:min|m\b)", text, re.I)
    return int(m.group(1)) * 60 if m else None


# 行の頭（時刻があればその後ろ）に ERROR の印がある行。「2026-10-03T00:00:00Z ERROR codex: …」も拾う
_ERROR_LINE = re.compile(r"^\s*(?:\d{4}-\d\d-\d\dT\S+\s+)?(?:ERROR|Error|error)\b[:\s]")


def failure_lines(jsonl: str, stderr: str) -> str:
    """失敗を表す行だけを拾う: --json の失敗のイベントと、stderr の ERROR で始まる行。

    警告や反響（「token_revoked という語を含む説明」・「429.png」）を、失効や枠切れと読み違えないため。
    """
    out = []
    for line in jsonl.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        t = ev.get("type", "") if isinstance(ev, dict) else ""
        item = ev.get("item") if isinstance(ev, dict) else None
        if t in ("error", "turn.failed") or str(t).endswith(".failed") or (isinstance(item, dict) and item.get("type") == "error"):
            out.append(line)
    out += [line for line in stderr.splitlines() if _ERROR_LINE.match(line)]
    return "\n".join(out)


def generate(cli: str, snapshot: bytes, text: str, refs: list[pathlib.Path], dest: pathlib.Path,
             log_dir: pathlib.Path, *, timeout: int, should_stop=lambda: False, shape: str = "landscape 16:9") -> dict:
    """1枚描いて dest に置く。{seconds, worker_refreshed, extra_images}。失敗は CodexError。

    shape は絵の形（既定は 16:9 の横長。html-slide の画像の枠は縦長・正方形もある）。

    指示はファイルに書いてから stdin につなぐ（パイプに書き込む途中で詰まると、停止も時間切れも効かなくなるため）。
    隔離ホームと作業フォルダは、作った直後から後始末の対象にする。ログは書き終えたあとに秘密を伏せる。
    """
    home = work = None
    started = time.monotonic()
    secrets = secrets_of(snapshot)
    page = dest.name.split(".")[0]          # slide-01.try.png → slide-01（候補の印はログの名前に持ち込まない）
    out_log, err_log = log_dir / (page + ".jsonl"), log_dir / (page + ".stderr.txt")
    try:
        home = _private_home(snapshot)
        work = pathlib.Path(tempfile.mkdtemp(prefix="html-slide-work."))
        # 子プロセスの出力は、まず隔離ホーム（0700）の中に受ける。公開のログへは、どの終わり方でも伏せてから写す（下の finally）
        priv_out, priv_err = home / "codex.jsonl", home / "codex.stderr.txt"
        prompt_file = home / "instruction.txt"
        prompt_file.write_text(WRAPPER.format(shape=shape) + ("\n" + WRAPPER_REFS if refs else "") + "\n\nDesign instruction:\n" + text,
                               encoding="utf-8")
        cmd = [cli, "exec", "--skip-git-repo-check", "-s", "read-only", "-C", str(work), "--json",
               "-c", "model_reasoning_effort=low"]
        for r in _flat_refs(refs, work):
            cmd += ["-i", str(r)]
        cmd.append("-")   # -i は値を複数取るので、指示は stdin で渡す（位置引数だと画像のパスとして読まれる）
        timed_out = False
        with open(prompt_file, "rb") as inp, open(priv_out, "w") as out, open(priv_err, "w") as err:
            p = subprocess.Popen(cmd, stdin=inp, stdout=out, stderr=err, env=_env(home), cwd=work,
                                 start_new_session=True)
            try:
                while p.poll() is None:
                    if should_stop():
                        raise CodexError("stopped", "ほかのページで止める理由が出た")
                    if time.monotonic() - started > timeout:
                        timed_out = True
                        break
                    time.sleep(0.5)
            finally:
                if p.poll() is None:
                    _kill(p)
        stdout, stderr = (redact(x.read_text(errors="replace"), secrets) for x in (priv_out, priv_err))
        failures = failure_lines(stdout, stderr)
        # 失効は絵の有無より先に見る（絵が1枚返っていても、失効が出たら残りを止める）
        if AUTH_DEAD.search(failures):
            raise CodexError("auth", "Codex の認証が失効している。`codex login` で入り直す")
        if timed_out:   # 期限を過ぎたら、絵が残っていても採らない（描きかけ・描き直しの途中かもしれない）
            raise CodexError("timeout", f"{timeout} 秒で終わらなかった")
        pngs = sorted((x for x in (home / "generated_images").rglob("*") if x.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
                       and x.is_file() and not x.is_symlink()), key=lambda x: x.stat().st_mtime)
        if not pngs:
            if RATE.search(failures):
                raise CodexError("rate", failures.strip()[-300:], _retry_after(failures))
            raise CodexError("gen", f"絵が返らなかった（rc={p.returncode}）: {(failures or stderr).strip()[-300:]}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        shutil.copyfile(pngs[-1], tmp)
        _verify_image(tmp)
        os.replace(tmp, dest)
        refreshed = (home / "auth.json").read_bytes() != snapshot
        return {"seconds": round(time.monotonic() - started, 1), "worker_refreshed": refreshed,
                "extra_images": len(pngs) - 1}
    except OSError as e:
        raise CodexError("gen", f"準備か後始末で失敗した: {e}")
    finally:
        if home is not None:
            _publish_logs(home, out_log, err_log, secrets)
        for d in (home, work):
            if d is not None:
                shutil.rmtree(d, ignore_errors=True)


def _publish_logs(home: pathlib.Path, out_log: pathlib.Path, err_log: pathlib.Path, secrets: list[str]) -> None:
    """隔離ホームの中の出力を、秘密を伏せて公開のログに写す。止めた・失敗した・成功したのどれでも通る。"""
    try:
        out_log.parent.mkdir(parents=True, exist_ok=True)
        for src, dst in ((home / "codex.jsonl", out_log), (home / "codex.stderr.txt", err_log)):
            if src.is_file():
                dst.write_text(redact(src.read_text(errors="replace"), secrets))
    except OSError:
        pass   # ログが書けなくても、隔離ホームの後始末（秘密の削除）は続ける


def _verify_image(path: pathlib.Path) -> None:
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.verify()
    except Exception as e:
        path.unlink(missing_ok=True)
        raise CodexError("gen", f"返った絵が壊れている: {e}")


def _kill(p) -> None:
    try:
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()


# --- 並列 ------------------------------------------------------------------------------

class Runner:
    """ページを並列に描く。work(item, attempt, last_error) が1回ぶんをやり、失敗は CodexError を投げる。"""

    RETRY_KINDS = ("gen", "timeout", "band", "rate")

    def __init__(self, max_parallel: int, attempts: int, log=print, sleep=time.sleep):
        self.max_parallel, self.attempts, self.log, self.sleep = max(1, max_parallel), max(1, attempts), log, sleep
        self.stop = threading.Event()
        self.stop_reason: CodexError | None = None
        self.pause_until = 0.0
        self.lock = threading.Lock()

    def _wait_pause(self):
        while not self.stop.is_set():
            with self.lock:
                left = self.pause_until - time.monotonic()
            if left <= 0:
                return
            self.sleep(min(left, 5))

    def _one(self, item, work):
        last = None
        for attempt in range(1, self.attempts + 1):
            self._wait_pause()
            if self.stop.is_set():
                return item, None, CodexError("stopped", str(self.stop_reason or ""))
            try:
                return item, work(item, attempt, last), None
            except CodexError as e:
                last = e
                if e.kind == "auth":
                    self.stop_reason = e
                    self.stop.set()
                    return item, None, e
                if e.kind == "stopped":
                    return item, None, e
                if e.kind == "rate":
                    wait = e.retry_after if e.retry_after is not None else min(300, 30 * 2 ** (attempt - 1))
                    if wait > MAX_RATE_WAIT:
                        self.stop_reason = e
                        self.stop.set()
                        return item, None, e
                    with self.lock:
                        self.pause_until = max(self.pause_until, time.monotonic() + wait)
                    self.log(f"  枠切れ。{wait} 秒待って続ける")
                if e.kind not in self.RETRY_KINDS or attempt == self.attempts:
                    return item, None, e
                self.log(f"  {item['no']}枚目: {e.kind}（{attempt}回目）。描き直す")
        return item, None, last

    def run(self, items: list, work) -> list:
        """[(item, 結果 or None, CodexError or None)] を items の順で返す。"""
        done = {}
        with ThreadPoolExecutor(max_workers=self.max_parallel) as ex:
            futs = [ex.submit(self._one, it, work) for it in items]
            for f in as_completed(futs):
                it, res, err = f.result()
                done[it["no"]] = (it, res, err)
                self.log(f"  {'✓' if err is None else '✗'} {it['no']}枚目"
                         + ("" if err is None else f"（{err.kind}）") + f"  [{len(done)}/{len(items)}]")
        return [done[it["no"]] for it in items]
