"""常駐 HTTP サーバ本体。標準ライブラリだけ。

構成:
- `QuickSlideServer` — bind・Store・`server.json`・起動時掃除・停止を持つ。`start()` → `serve_forever()` → `close()`
- `_Handler` — `BaseHTTPRequestHandler`。全 verb を `QuickSlideServer.dispatch` に渡すだけ
- ルート表 `ROUTES` と各ハンドラ（`_route_*`）。まだ無い口は `later.py` が「まだ使えない」と答える
- ワーカー（`worker/pool.py`）はサーバの中のスレッド。`start()` の最後に起こし、`close()` の最初に止める
- 設定（P4）: 待ち受け（`server`）のほかは仕事のたびに読み直す（`refresh_settings`）。待ち受けを変えたら画面の
  「再起動」（`POST /api/server/restart`・`server/restart.py`）。ファイルを手で直したときも `restart_required` が立つ

守っている性質（tests/ が確認する）:
- I1/I8 静的配信は `static.py` の許可リスト経由のみ。データ置き場は静的ルートの外
- I2 会話キーの生値はリクエスト本文の dict からアクセス層へ渡すだけで、ログ・応答・ファイルに書かない。
  アクセスログはクエリ文字列を落とす（launch トークンが `?token=` に載るため）
- I4 attach は「path の launch を読む → ハッシュ一致を確認 → アクセス層の条件付き UPDATE で 1 回だけ消費」。
  拒否コードは消費後の実際の行状態から導く（推測しない）
- I7 応答は日本語本文 + 機械可読 `code`。予期しない例外は 500 + 一般文 + 相関 ID、トレースバックはログだけ
- I9 「ぜんぶの口」（0.0.0.0 / ::）と名前には開かない（ループバックか、設定に書かれたアドレス 1 つ）。Host は全要求、Origin はセッショントークンを返す `GET /api/session` と
  状態を変える要求の両方で検査する（`Origin` があればループバックの `http://` オリジンのいずれかであること。
  無ければ許可 — CLI が使うため）。状態を変える要求はさらにセッショントークンも必須
"""

from __future__ import annotations

import dataclasses
import errno
import hmac
import http.server
import ipaddress
import json
import logging
import os
import secrets
import signal
import socket
import threading
import time
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .. import __version__
from ..clock import Clock, elapsed_clock, utcnow
from ..engines import ENGINES
from ..errors import NotFoundError, QuickSlideError, StartupError, StateError
from ..ids import is_uuid, new_id
from ..paths import WEB_APP_DIR, DataRoot
from ..render.runner import engine_unavailable
from ..security.tokens import hash_token
from ..settings import Settings, bind_refusal, bind_scope, file_fingerprint, is_loopback_host, listen_of
from ..store import Store
from ..worker.pool import WorkerPool
from ..worker.tools import Tools
from .errors import GENERIC_500_MESSAGE, ApiError, bad_json, from_store_error, method_not_allowed, not_found
from .later import NOT_READY_CODE
from . import restart as restart_mod
from .runtime import InstanceLock, ServerRecord, http_host, remove_record, status, write_record
from .static import AssetFiles, PresentFiles, ResolvedFile, StaticRoot

log = logging.getLogger("quick_slide.server")
access_log = logging.getLogger("quick_slide.server.access")

SESSION_HEADER = "X-Quick-Slide-Session"
MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_CLIENT_JSON_BYTES = 8 * 1024
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# 形式ごとの「いま使えるか」を覚えておく秒数（`kind_refusals`）
KIND_CHECK_SECONDS = 10.0
SECURITY_HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)
HTML_CSP = "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'"

# 画面に返す文。**API の引数名・列名・内部の状態名は書かない**（利用者に見せる語ではない）。
# 断った理由の区別は `code` が持ち、文は「利用者にできること」だけを言う。
BROWSER_INFO_MESSAGE = "ブラウザの情報を受け取れませんでした。画面を読み込み直してください。"
COPY_RECORD_MESSAGE = "操作の記録を受け取れませんでした。操作そのものは続けられます。"

# http.server が内部で send_error に使う英語文面の代わり（I7）
_STATUS_MESSAGES_JA = {
    400: "要求の形式が正しくありません。",
    404: "指定されたものは見つかりませんでした。",
    405: "この URL ではその操作はできません。",
    408: "要求の受信がタイムアウトしました。",
    411: "本文の長さ（Content-Length）が必要です。",
    413: "本文が大きすぎます。",
    414: "URL が長すぎます。",
    431: "ヘッダが大きすぎます。",
    500: GENERIC_500_MESSAGE,
    501: "その操作には対応していません。",
    505: "その HTTP バージョンには対応していません。",
}


@dataclass
class Request:
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: bytes
    params: dict[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def json_object(self) -> dict[str, Any]:
        if not self.body:
            raise bad_json()
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise bad_json() from None
        if not isinstance(data, dict):
            raise bad_json()
        return data


@dataclass
class Response:
    status: int
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = ()
    close: bool = False

    @classmethod
    def json(cls, status: int, obj: Any, **kw: Any) -> "Response":
        return cls(status, json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8"), **kw)

    @classmethod
    def file(cls, resolved: ResolvedFile, data: bytes) -> "Response":
        extra: tuple[tuple[str, str], ...] = ()
        if resolved.content_type.startswith("text/html"):
            extra = (("Content-Security-Policy", HTML_CSP),)
        return cls(200, data, content_type=resolved.content_type, headers=extra)


Handler = Callable[["QuickSlideServer", Request], Response]


class _HTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, address: tuple[str, int], handler: type, app: "QuickSlideServer") -> None:
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.app = app
        super().__init__(address, handler)

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001 - 標準の署名
        # 既定はトレースバックを stderr に印字する。ログにだけ残す
        log.error("接続処理中の例外 (%s)\n%s", client_address, traceback.format_exc())


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = f"QuickSlide/{__version__}"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30
    server: _HTTPServer

    def _dispatch(self) -> None:
        self.server.app.dispatch(self)

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _dispatch

    def version_string(self) -> str:
        return self.server_version  # 既定は sys_version（空）を足して末尾に空白が付く

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 標準の署名
        # 既定は stderr に requestline（クエリ込み）を書く。何も書かない。アクセスログは dispatch が出す
        return

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """http.server 内部（不正な要求行、長すぎるヘッダ等）からの呼び出し。英語の HTML ではなく日本語 JSON を返す。"""
        body = json.dumps({"error": {"code": f"http_{code}", "message": _STATUS_MESSAGES_JA.get(code, "要求を処理できませんでした。")}},
                          ensure_ascii=False).encode("utf-8")
        if getattr(self, "request_version", "HTTP/0.9") == "HTTP/0.9":
            # 要求行が読めなかったときの既定は HTTP/0.9 扱いで、状態行もヘッダも出ない。状態行を必ず返す
            self.request_version = "HTTP/1.0"
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            for k, v in SECURITY_HEADERS:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD" and code >= 200 and code not in (204, 304):
                self.wfile.write(body)
        except OSError:
            pass
        self.close_connection = True


def allowed_hosts(host: str, port: int, names: Sequence[str] = ()) -> set[str]:
    """`Host:` ヘッダで名乗ってよい宛先（I9）。ここを通らない要求は 421 で断る。

    ループバックは常に許す。bind したアドレスそのもの（書かれた形と正規の形）も許す（アドレスは DNS で付け替えられない）。
    設定に書かれた名前を足すのは、**ループバック以外に** bind したときだけ。ループバックかどうかは
    `is_loopback_host` がアドレスの意味で決める（`0:0:0:0:0:0:0:1` のような別の書き方で名前を足させない）。
    名前を足せるのは設定が明示したものだけで、勝手に DNS を引いたりはしない（設定と実態を食い違わせない）。
    """
    hosts = ["127.0.0.1", "localhost", "[::1]"]
    if host.lower().rstrip(".") != "localhost":
        hosts.append(http_host(host))
        try:
            hosts.append(http_host(str(ipaddress.ip_address(host))))
        except ValueError:
            pass
    if not is_loopback_host(host):
        hosts.extend(names)
    return {f"{h.lower()}:{port}" for h in hosts}


class QuickSlideServer:
    """1 プロセス 1 インスタンス。`start()` で bind と Store を用意し、`serve_forever()` で回し、`close()` で片付ける。"""

    def __init__(
        self,
        root: DataRoot | str | Path | None = None,
        *,
        settings: Settings | None = None,
        host: str | None = None,
        port: int | None = None,
        static_root: Path | str | None = None,
        clock: Clock | None = None,
        tools: Tools | None = None,
        start_workers: bool = True,
        samples_dir: Path | str | None = None,
    ) -> None:
        self.root = root if isinstance(root, DataRoot) else DataRoot.resolve(root)
        # 起動したときに読んだ設定の印。仕事のたびに見比べ、変わっていれば読み直す（待ち受けの段は起動時のまま）
        self._settings_fp = file_fingerprint(self.root.settings_path)
        self.settings = settings or Settings.load(self.root.settings_path)
        self.boot_settings = self.settings
        self.settings_error: str | None = None
        self._settings_lock = threading.Lock()
        self.host = host if host is not None else self.settings.server.host
        self.port = port if port is not None else self.settings.server.port
        refusal = bind_refusal(self.host)
        if refusal is not None:
            # 黙って 127.0.0.1 に落とさない（設定と実態を食い違わせない。I9）
            raise StartupError(refusal)
        self.static = StaticRoot(static_root or WEB_APP_DIR)
        self.static_root_arg = static_root   # 再起動の後継にも同じものを渡す
        self.restartable = False             # 常駐の `serve` だけが立てる（試験のスレッドの中では再起動しない）
        self.restarting: dict[str, Any] | None = None
        self._abandon = False
        self.assets = AssetFiles(self.root.root, self.root.assets_dir, self.root.trash_dir)
        self.present_files = PresentFiles(self.root.root)
        self.clock = clock or Clock()
        self.instance_id = new_id()
        self.session_token = secrets.token_urlsafe(32)
        self.store_lock = threading.RLock()
        self.store: Store | None = None
        self.started_at: str | None = None
        self._t0: float | None = None
        # 稼働時間を読む時計。`time.monotonic()` は macOS では眠っている間止まるので使わない
        # （`clock.elapsed_clock` に実測と選び方）
        self._uptime_clock_name, self._uptime_read = elapsed_clock()
        self.sweep_summary: dict[str, Any] | None = None
        self._httpd: _HTTPServer | None = None
        self._lock = InstanceLock(self.root.server_lock_path)
        self._stopping = threading.Event()
        self.tools = tools or Tools()
        self.start_workers = start_workers   # 試験で取り込みの形だけを見るときは起こさない
        # 同梱の見本の置き場（docs/adr/0006-bundled-samples.md）。常駐の serve・start は `paths.SAMPLES_DIR` を渡す。
        # None なら起動のときに見本を入れない（試験の既定。空のデータの置き場から始めたい）。設定 › 見本 の一覧と
        # 「入れ直す」はこの置き場を読む
        self.samples_dir = Path(samples_dir) if samples_dir is not None else None
        self.samples_summary: dict[str, Any] | None = None
        self.workers: WorkerPool | None = None
        self._kind_refusals: tuple[float, dict[str, str]] | None = None
        self._kind_refusals_lock = threading.Lock()

    # --- ライフサイクル ---
    @property
    def bound_port(self) -> int:
        if self._httpd is None:
            raise StateError("サーバは未起動です")
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{http_host(self._display_host)}:{self.bound_port}"

    @property
    def _display_host(self) -> str:
        """人に見せる URL の名前。`--host` で設定を上書きしたときは、設定の名前を使わない（食い違わせない）。"""
        cfg = self.settings.server
        return cfg.display_host if self.host == cfg.host else self.host

    def start(self) -> "QuickSlideServer":
        self.root.ensure()
        if not self._lock.acquire():
            st = status(self.root, timeout=1.0)
            hint = f"（pid {st['pid']}、{st['url']}）" if st.get("running") else "（別プロセスがロックを保持）"
            raise StartupError(f"Quick Slide は既に起動しています{hint}。二重起動はしません")
        try:
            st = status(self.root, timeout=1.0)
            if st.get("running"):
                raise StartupError(f"Quick Slide は既に起動しています（pid {st['pid']}、{st['url']}）。二重起動はしません")
            try:
                self._httpd = _HTTPServer((self.host, self.port), _Handler, self)
            except OSError as exc:
                if exc.errno == errno.EADDRNOTAVAIL:
                    raise StartupError(
                        f"IP アドレス {self.host} はこのパソコンに割り当てられていないため、使えません。"
                        "このパソコンの IP アドレスを書いてください") from exc
                raise StartupError(
                    f"ポート {self.port} に bind できません（{exc.__class__.__name__}）。"
                    "別のアプリがこのポートを使っている可能性があります。そのアプリを止めるか、設定 server.port を変えてください。"
                    "別のポートへ勝手に切り替えることはしません"
                ) from exc
            bound_ip = self._httpd.server_address[0]
            is_loopback = is_loopback_host(bound_ip.split("%")[0])
            # ループバックを頼んだのに外へ開いていたら止める。書かれたアドレスへ開くとき（設定の「この機械」）は
            # 外へ開くのが頼まれたことなので止めない（v1 はここで必ず止め、画面で保存した口が起動できなかった）
            if is_loopback_host(self.host) and not is_loopback:
                raise StartupError(f"bind 結果がループバックではありません（{bound_ip}）。起動を中止します")
            self.store = Store.open(self.root, settings=self.settings, clock=self.clock, startup_sweep=False)
            self.sweep_summary = run_startup_sweep(self.store)
            # 同梱の見本を入れる（一度も入れていない見本・触っていない古い版）。ワーカーを起こす前に。見本のせいで起動を止めない
            if self.samples_dir is not None:
                try:
                    from .. import samples as _samples

                    self.samples_summary = _samples.sync(self.store, self.root, directory=self.samples_dir,
                                                         extra_env=self.tools.extra_env)
                except Exception:  # noqa: BLE001
                    log.exception("見本を入れられませんでした（起動は続けます）")
            # 起動時掃除のあとでワーカーを起こす（前のセッションの「走っている」仕事は掃除が失敗にしてある）
            if self.start_workers:
                self.workers = WorkerPool(self, self.root, self.settings, self.tools)
                self.workers.start()
            # 起動できた待ち受けを控える（再起動して新しい設定で起動できなければ、これに戻す）
            try:
                restart_mod.save_last_good(self.root, self.running_listen())
            except OSError:
                log.warning("起動できた待ち受けを控えられませんでした（再起動のやり直しに使う）")
            self.started_at = utcnow()
            self._uptime_clock_name, self._uptime_read = elapsed_clock()
            self._t0 = self._uptime_read()
            write_record(self.root, ServerRecord(
                instance_id=self.instance_id, pid=os.getpid(), started_at=self.started_at, host=self.host,
                port=self.bound_port, url=self.url, session_token=self.session_token, version=__version__,
            ))
            if not is_loopback_host(self.host):
                log.warning("この機械の外に開いています host=%s。Quick Slide に鍵は無いので、"
                            "この網から届く端末は誰でもスライドを読み・消し・画像の枠を使えます", self.host)
            log.info("起動 instance=%s pid=%d url=%s sweep=%s", self.instance_id, os.getpid(), self.url,
                     json.dumps(self.sweep_summary, ensure_ascii=False, sort_keys=True))
            return self
        except BaseException:
            self.close()
            raise

    def serve_forever(self) -> None:
        if self._httpd is None:
            raise StateError("start() を先に呼ぶ")
        self._httpd.serve_forever(poll_interval=0.25)

    def shutdown(self) -> None:
        """serve_forever を止める（serve_forever と別スレッドから呼ぶ）。"""
        if self._httpd is not None:
            self._httpd.shutdown()

    def request_stop(self, *, abandon: bool = False) -> None:
        """停止を頼む。**202 を返すために絶対に詰まらせない**（`shutdown()` は別スレッドで呼ぶ）。

        `abandon`（今すぐ再起動）は、走っている仕事の終わりを台帳に書かずに止める（次のサーバが続きから戻す）。
        """
        if abandon:
            self._abandon = True
        if self._stopping.is_set():
            return
        self._stopping.set()
        threading.Thread(target=self.shutdown, name="quick-slide-stop", daemon=True).start()

    def close(self) -> None:
        # ワーカー → httpd → Store → server.json → server.lock の順。ワーカーは台帳に終わりを書いてから止まる
        if self.workers is not None:
            try:
                self.workers.stop(abandon=self._abandon)
            finally:
                self.workers = None
        if self._httpd is not None:
            try:
                self._httpd.server_close()
            finally:
                self._httpd = None
        if self.store is not None:
            try:
                self.store.close()
            finally:
                self.store = None
        if self.started_at is not None:
            remove_record(self.root, self.instance_id)
        self._lock.release()
        log.info("停止 instance=%s", self.instance_id)

    def run(self) -> None:
        """前面で動かす: start → シグナルで止まるまで serve → close。"""
        self.start()
        self.run_after_start()

    def run_after_start(self) -> None:
        """`start()` 済みのサーバを、SIGTERM / SIGINT か `POST /api/server/stop` で止まるまで回し、必ず close する。"""
        previous = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, lambda *_: self.request_stop())
        try:
            self.serve_forever()
        finally:
            for sig, prev in previous.items():
                signal.signal(sig, prev)
            self.close()

    # --- テスト・組み込み用: 別スレッドで回す ---
    def start_in_thread(self) -> threading.Thread:
        self.start()
        t = threading.Thread(target=self.serve_forever, name="quick-slide-http", daemon=True)
        t.start()
        return t

    # --- 設定（P4）---
    def running_listen(self) -> dict[str, Any]:
        """いま動いている待ち受け（頼まれたポート。0 は自動割当のまま）。"""
        cfg = self.boot_settings.server
        names = cfg.host_names if self.host == cfg.host else ()
        return {"host": self.host, "port": self.port, "host_names": list(names)}

    def refresh_settings(self) -> Settings:
        """settings.json が起動時（か前に読んだとき）から変わっていれば読み直す。**待ち受けの段は起動時のまま**
        （変えるには再起動）。読めなければ前の設定のまま続け、理由を `settings_error` に残す。"""
        fp = file_fingerprint(self.root.settings_path)
        if fp == self._settings_fp:
            return self.settings
        with self._settings_lock:
            if fp == self._settings_fp:
                return self.settings
            try:
                loaded = Settings.load(self.root.settings_path)
            except QuickSlideError as exc:
                self._settings_fp = fp
                self.settings_error = str(exc)
                log.warning("設定を読み直せませんでした（前の設定で続けます）: %s", exc)
                return self.settings
            fresh = dataclasses.replace(loaded, server=self.boot_settings.server)
            self._settings_fp = fp
            self.settings_error = None
            changed = fresh != self.settings
            self.settings = fresh
            store = self.store
            if store is not None:
                # 台帳の錠は取らない（画面の口は台帳の錠を持ったままここへ来る。錠の順を逆にすると詰まる）。
                # 属性の差し替えは 1 回の代入なので、読む側は前か後かのどちらかを見る
                store.settings = fresh
            if self.workers is not None and fresh.worker.parallel != self.workers.target:
                self.workers.adjust(fresh.worker.parallel)
            if changed:
                log.info("設定を読み直しました writer=%s parallel=%d", fresh.writer, fresh.worker.parallel)
        return self.settings

    def settings_state(self) -> dict[str, Any]:
        """ファイルに書いてある待ち受けと、いま動いている待ち受けの食い違い（画面の「再起動が必要」の帯）。"""
        self.refresh_settings()
        running = self.running_listen()
        try:
            disk = Settings.load(self.root.settings_path)
        except QuickSlideError as exc:
            return {"restart_required": False, "error": str(exc), "disk": None, "running": running,
                    "expected_url": None}
        listen = listen_of(disk.server)
        from ..settings import expected_url

        # ポート 0（自動割当。試験で使う）で動いているときは、ポートは見比べない
        compare = dict(listen, port=running["port"]) if running["port"] == 0 else listen
        return {"restart_required": compare != running, "error": None, "disk": listen, "running": running,
                "expected_url": expected_url(listen)}

    # --- 要求処理 ---
    def dispatch(self, h: _Handler) -> None:
        cid = secrets.token_hex(6)
        started = time.monotonic()
        path, query = split_target(h.path)
        method = h.command
        headers = {k.lower(): v for k, v in h.headers.items()}
        resp: Response
        try:
            body = self._read_body(h, headers, method)
            req = Request(method=method, path=path, query=query, headers=headers, body=body)
            self._check_host(req)
            resp = self._route(req)
        except ApiError as exc:
            # 「まだ使えない口」（later.py）は想定どおりの断りなので、相関 ID もエラーログも付けない
            unexpected = exc.status >= 500 and exc.code != NOT_READY_CODE
            resp = Response.json(exc.status, exc.body(cid if unexpected else None), close=exc.status in (413, 411))
            if unexpected:
                log.error("[%s] %s %s -> %d %s", cid, method, path, exc.status, exc.code)
        except QuickSlideError as exc:
            api = from_store_error(exc)
            resp = Response.json(api.status, api.body(cid if api.status >= 500 else None))
        except Exception:  # noqa: BLE001 - 最外殻。トレースバックはログにだけ
            log.error("[%s] 予期しない例外 %s %s\n%s", cid, method, path, traceback.format_exc())
            resp = Response.json(500, ApiError(500, "internal_error", GENERIC_500_MESSAGE).body(cid))
        self._send(h, method, resp)
        access_log.info("%s %s %d %d %.1fms cid=%s", method, path, resp.status, len(resp.body), (time.monotonic() - started) * 1000, cid)

    @staticmethod
    def _read_body(h: _Handler, headers: dict[str, str], method: str) -> bytes:
        if "transfer-encoding" in headers:
            raise ApiError(411, "length_required", "本文は Content-Length 付きで送ってください（chunked には対応していません）。")
        raw = headers.get("content-length")
        if raw is None:
            if method in MUTATING_METHODS:
                raise ApiError(411, "length_required", "本文の長さ（Content-Length）が必要です。")
            return b""
        try:
            n = int(raw)
        except ValueError:
            raise ApiError(400, "bad_content_length", "Content-Length が整数ではありません。") from None
        if n < 0:
            raise ApiError(400, "bad_content_length", "Content-Length が負です。")
        if n > MAX_BODY_BYTES:
            raise ApiError(413, "body_too_large", f"本文が大きすぎます（上限 {MAX_BODY_BYTES // (1024 * 1024)} MiB）。")
        data = h.rfile.read(n) if n else b""
        if len(data) != n:
            raise ApiError(400, "short_body", "本文が Content-Length より短く終わりました。")
        return data

    def _send(self, h: _Handler, method: str, resp: Response) -> None:
        try:
            h.send_response(resp.status)
            h.send_header("Content-Type", resp.content_type)
            h.send_header("Content-Length", str(len(resp.body)))
            for k, v in SECURITY_HEADERS:
                h.send_header(k, v)
            for k, v in resp.headers:
                h.send_header(k, v)
            if resp.close:
                h.send_header("Connection", "close")
                h.close_connection = True
            h.end_headers()
            if method != "HEAD" and resp.body:
                h.wfile.write(resp.body)
        except (BrokenPipeError, ConnectionResetError):
            h.close_connection = True

    # --- 検査（I9） ---
    def _allowed_hosts(self) -> set[str]:
        cfg = self.settings.server
        names = cfg.host_names if self.host == cfg.host else ()
        return allowed_hosts(self.host, self.bound_port, names)

    def _allowed_origins(self) -> set[str]:
        return {f"http://{h}" for h in self._allowed_hosts()}

    def _check_host(self, req: Request) -> None:
        host = (req.header("host") or "").strip().lower()
        if host not in self._allowed_hosts():
            raise ApiError(421, "bad_host", "この要求の宛先（Host）が Quick Slide のものではないため受け付けません。")

    def _check_origin(self, req: Request) -> None:
        """`Origin` があればループバックの `http://` オリジンのいずれかであること（`null` も拒否）。
        無ければ許可する（CLI・同一プロセス内の直接呼び出しには `Origin` が付かないため）。"""
        origin = req.header("origin")
        if origin is not None and origin.strip().lower() not in self._allowed_origins():
            raise ApiError(403, "bad_origin", "別のサイトからの要求は受け付けません。")

    def _check_mutation(self, req: Request) -> None:
        self._check_origin(req)
        token = req.header(SESSION_HEADER)
        if token is None:
            raise ApiError(403, "session_required", f"状態を変える要求には {SESSION_HEADER} ヘッダ（セッショントークン）が必要です。")
        if not hmac.compare_digest(token.encode("utf-8"), self.session_token.encode("utf-8")):
            raise ApiError(403, "session_invalid", "セッショントークンが一致しません。画面を開き直すか、サーバの状態を確認してください。")

    # --- ルーティング ---
    def _route(self, req: Request) -> Response:
        method = req.method
        path = req.path
        path_methods: set[str] = set()
        for methods, matcher, handler in ROUTES:
            params = matcher(path)
            if params is None:
                continue
            path_methods |= methods
            if method in methods or (method == "HEAD" and "GET" in methods):
                req.params = params
                if method in MUTATING_METHODS:
                    self._check_mutation(req)
                    if self.restarting is not None and not path.startswith("/api/server/"):
                        raise ApiError(503, "restarting", "サーバを再起動しています。少し待ってから、もう一度操作してください。")
                return handler(self, req)
        if path_methods:
            raise method_not_allowed()
        raise not_found()

    # --- ハンドラ ---
    def _health_body(self) -> tuple[bool, dict[str, Any]]:
        self.refresh_settings()   # 手で直した設定を、ワーカーの数も含めてこの答えに映す
        db_ok = False
        db_error: str | None = None
        try:
            with self.store_lock:
                assert self.store is not None
                db_ok = is_uuid(self.store.preset_brand_revision_id())  # 実際に 1 行読めた証拠
        except Exception as exc:  # noqa: BLE001 - 健康診断は例外を「不健康」として報告する
            db_error = exc.__class__.__name__
        body = {
            "ok": db_ok,
            "service": "quick-slide",
            "version": __version__,
            "instance_id": self.instance_id,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "uptime_seconds": None if self._t0 is None else round(self._uptime_read() - self._t0, 3),
            # どの時計で測ったかも言う。数だけ出すと、別の機械の記録を読んだときに
            # 「眠っていた分が入っているか」が分からない（この差は実測で 3.25 倍あった）
            "uptime_clock": self._uptime_clock_name,
            "host": self.host,
            # この機械の外から届くのか。開いたなら開いたと言う（黙って開かない）
            "bind_scope": bind_scope(self.host),
            "port": self.bound_port,
            "db": {"ok": db_ok, "error_class": db_error},
            "startup_sweep": self.sweep_summary,
            "browser": {"mode": self.settings.browser.mode, "attach_wait_seconds": self.settings.browser.attach_wait_seconds},
            "static_available": self.static.available,
            "worker": self.workers.status() if self.workers is not None else None,
            "settings": self._settings_health(),
            "restarting": None if self.restarting is None else {
                k: self.restarting[k] for k in ("id", "mode", "running_jobs")},
            "restart": restart_mod.public_result(self.root),
        }
        return db_ok, body

    def _settings_health(self) -> dict[str, Any]:
        st = self.settings_state()
        return {"restart_required": st["restart_required"], "error": st["error"] or self.settings_error,
                "writer": self.settings.writer, "parallel": self.settings.worker.parallel}

    def _route_health(self, req: Request) -> Response:
        ok, body = self._health_body()
        return Response.json(200 if ok else 503, body)

    def _route_session(self, req: Request) -> Response:
        # 秘密（セッショントークン）を返す唯一の GET。Sec-Fetch-Site だけでなく Origin も重ねて検査する
        # （3 つの検査を重ねるのは、1 つが環境で欠けても残りが効くようにするため）。
        self._check_origin(req)
        site = (req.header("sec-fetch-site") or "").lower()
        if site and site not in ("same-origin", "none"):
            raise ApiError(403, "bad_origin", "別のサイトからはセッションを取得できません。")
        return Response.json(200, {"session_token": self.session_token, "instance_id": self.instance_id})

    def _route_stop(self, req: Request) -> Response:
        self.request_stop()
        return Response.json(202, {"stopping": True, "instance_id": self.instance_id})

    def _route_restart(self, req: Request) -> Response:
        body = req.json_object()
        return Response.json(202, restart_mod.begin(self, body.get("mode")))

    def wake_worker(self) -> None:
        """**仕事を作った直後に**ワーカーを起こす。取りこぼしても、ワーカーは数秒ごとに見に来る。"""
        if self.workers is not None:
            self.workers.wake()

    def kind_refusals(self) -> dict[str, str]:
        """形式（モード）→ いまその形式で作り始められない理由（使える形式は入らない）。ピッチは Codex が要る。
        画面を開くたび（`/api/vocabulary`）と作成の依頼のたびに聞くので、答えは少しの間だけ覚える
        （Codex を入れた・ログインしたら、サーバを起こし直さなくても画面を読み直せば使える）。"""
        with self._kind_refusals_lock:
            now = time.time()
            if self._kind_refusals is not None and 0 <= now - self._kind_refusals[0] < KIND_CHECK_SECONDS:
                return dict(self._kind_refusals[1])
            refusals = {}
            for mode, engine in ENGINES.items():
                reason = engine_unavailable(engine, tools=self.tools, home=self.root.root)
                if reason:
                    refusals[mode] = reason
            self._kind_refusals = (now, refusals)
            return dict(refusals)

    def launch_url(self, deck_id: str, launch_id: str | None, token: str | None) -> str:
        base = f"{self.url}/slides/{deck_id}"
        if launch_id is None or token is None:
            return base
        return f"{base}?launch={launch_id}&token={token}"

    @staticmethod
    def _launch_public(row: dict[str, Any]) -> dict[str, Any]:
        keys = ("id", "deck_id", "state", "requested_via", "requested_at", "expires_at", "attached_at", "attached_via", "client")
        return {k: row.get(k) for k in keys}

    def _route_launch_get(self, req: Request) -> Response:
        with self.store_lock:
            assert self.store is not None
            try:
                row = self.store.launch(req.params["launch_id"])
            except NotFoundError:
                raise ApiError(404, "launch_not_found", "そのリンクは見つかりません。会話に残された URL をそのまま開いてください。") from None
        return Response.json(200, self._launch_public(row))

    def _route_attach(self, req: Request) -> Response:
        launch_id = req.params["launch_id"]
        body = req.json_object()
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise ApiError(400, "token_required", "このリンクでは画面を開けません。会話に残された URL をそのまま開いてください。")
        client = body.get("client")
        if client is None:
            client = {}
        if not isinstance(client, dict):
            raise ApiError(400, "validation_error", BROWSER_INFO_MESSAGE)
        client_text = json.dumps(client, ensure_ascii=False)
        if len(client_text) > MAX_CLIENT_JSON_BYTES:
            raise ApiError(400, "client_too_large", BROWSER_INFO_MESSAGE)
        if token in client_text:
            # client_json は永続化される。トークン平文をそこに紛れ込ませる要求は受けない（I4）
            raise ApiError(400, "token_in_client", BROWSER_INFO_MESSAGE)
        del client_text
        with self.store_lock:
            assert self.store is not None
            store = self.store
            try:
                row = store.launch(launch_id)
            except NotFoundError:
                with store.transaction():
                    store.append_event("launch_attach_rejected", {"launch_id": None, "reason": "unknown_launch"})
                raise ApiError(404, "launch_not_found", "そのリンクは見つかりません。会話に残された URL をそのまま開いてください。") from None
            if not hmac.compare_digest(hash_token(token), row["token_hash"]):
                # この launch のトークンではない。何も消費せず、記録だけ残す
                with store.transaction():
                    store.append_event("launch_attach_rejected", {"launch_id": launch_id, "reason": "token_mismatch"}, deck_id=row["deck_id"])
                raise ApiError(403, "token_mismatch", "この URL のトークンは一致しません。会話に残された URL をそのまま開いてください。")
            try:
                attached = store.attach_launch(token, attached_via="unknown", client=client)
            except StateError:
                # 拒否の理由は「消費を試みた後の実際の行の状態」から導く（アクセス層は同じ理由を events に残している）
                after = store.launch(launch_id)
                state = after["state"]
                if state == "attached":
                    raise ApiError(409, "already_consumed", "このリンクは既に使われています。画面は開いているはずなので、そちらをご覧ください。") from None
                if state == "expired":
                    raise ApiError(410, "expired", "このリンクの有効期限（15 分）が切れました。会話からスライドを開き直してください。") from None
                if state == "cancelled":
                    raise ApiError(409, "cancelled", "この起動は取り消されています。") from None
                raise ApiError(409, "rejected", "このリンクでは画面を開けませんでした。会話からスライドを開き直してください。") from None
        del token
        out = self._launch_public(attached)
        out["launch_id"] = attached["id"]
        return Response.json(200, out)

    def _route_clipboard_copy(self, req: Request) -> Response:
        body = req.json_object()
        method = body.get("method")
        ok = body.get("ok")
        error_name = body.get("error_name")
        launch_id = body.get("launch_id")
        if not isinstance(ok, bool):
            raise ApiError(400, "validation_error", COPY_RECORD_MESSAGE)
        if error_name is not None and (not isinstance(error_name, str) or len(error_name) > 200):
            raise ApiError(400, "validation_error", COPY_RECORD_MESSAGE)
        if launch_id is not None and not isinstance(launch_id, str):
            raise ApiError(400, "validation_error", COPY_RECORD_MESSAGE)
        with self.store_lock:
            assert self.store is not None
            deck_id = None
            if launch_id is not None:
                try:
                    deck_id = self.store.launch(launch_id)["deck_id"]  # deck は行から導く（クライアントに言わせない）
                except NotFoundError:
                    raise ApiError(404, "launch_not_found", "そのリンクは見つかりません。会話に残された URL をそのまま開いてください。") from None
            self.store.record_clipboard_copy(launch_id=launch_id, method=method, ok=ok, error_name=error_name, deck_id=deck_id)
        return Response.json(201, {"recorded": True, "launch_id": launch_id, "deck_id": deck_id})

    def _route_asset_file(self, req: Request) -> Response:
        with self.store_lock:
            assert self.store is not None
            try:
                row = self.store.asset(req.params["asset_id"])
            except NotFoundError:
                raise not_found() from None
        resolved = self.assets.resolve(row)
        data = resolved.read() if resolved is not None else None
        if resolved is None or data is None:
            raise not_found()
        return Response.file(resolved, data)

    def _route_trashed_asset_file(self, req: Request) -> Response:
        """ごみ箱へ退避した画像を配る。**`deletion_task_id` が合わなければ 404。**

        退避先は `.trash/<deletion_task_id>/<relative_path>` で、そこに無ければ降りられない。
        追加の照合をここで書かないのは、**判断点を増やさない**ため（置き場所そのものが照合になる）。
        """
        with self.store_lock:
            assert self.store is not None
            try:
                row = self.store.asset(req.params["asset_id"])
            except NotFoundError:
                raise not_found() from None
        resolved = self.assets.resolve_trashed(row, req.params["deletion_task_id"])
        data = resolved.read() if resolved is not None else None
        if resolved is None or data is None:
            raise not_found()
        return Response.file(resolved, data)

    def _route_moved(self, req: Request) -> Response:
        """古い画面の URL を新しい URL へ 301 で送る。

        **送る先を決めるのはここ 1 か所だけにする。** 2 か所に書くと、`?launch=<id>&token=<token>` を
        片方だけ落とすような差が生まれ、attach が記録されない経路ができる。
        `/decks/<id>` は会話の中に残っていることがあるので、404 にはしない。
        """
        deck_id = req.params.get("deck_id")
        target = f"/slides/{deck_id}" if deck_id else "/slides"
        if req.query:
            target += "?" + req.query
        return Response(301, b"", content_type="text/plain; charset=utf-8",
                        headers=(("Location", target),))

    def _route_index(self, req: Request) -> Response:
        resolved = self.static.index()
        data = resolved.read() if resolved is not None else None
        if resolved is None or data is None:
            raise ApiError(404, "ui_unavailable", "画面のファイルが見つかりません。インストールを確認してください。")
        return Response.file(resolved, data)

    def _route_static(self, req: Request) -> Response:
        resolved = self.static.resolve(req.path)
        data = resolved.read() if resolved is not None else None
        if resolved is None or data is None:
            raise not_found()
        return Response.file(resolved, data)


def split_target(target: str) -> tuple[str, str]:
    """要求行の request-target → (path, query)。`//host/x` を netloc と誤読しないよう、origin-form は自前で分ける。
    absolute-form（`http://host/path`）は path だけを取り出す（Host 検査は別途ヘッダで行う）。"""
    if target.startswith("/"):
        path, _, query = target.partition("?")
        return path.partition("#")[0], query
    if target.lower().startswith(("http://", "https://")):
        parts = urlsplit(target)
        return parts.path or "/", parts.query
    return target, ""


# --- ルート表 ---
def _exact(target: str) -> Callable[[str], dict[str, str] | None]:
    return lambda p: {} if p == target else None


def _uuid_route(prefix: str, name: str, suffix: str = "") -> Callable[[str], dict[str, str] | None]:
    def match(p: str) -> dict[str, str] | None:
        if not p.startswith(prefix) or not p.endswith(suffix):
            return None
        middle = p[len(prefix):len(p) - len(suffix)] if suffix else p[len(prefix):]
        if not is_uuid(middle) or middle != middle.lower():
            return None  # ID は小文字 UUID に限る（macOS の大文字小文字非区別に引きずられない）
        return {name: middle}
    return match


def _static_route(p: str) -> dict[str, str] | None:
    return {} if p.startswith("/static/") else None


def _two_uuid_route(prefix: str, name1: str, middle: str, name2: str, suffix: str = "") -> Callable[[str], dict[str, str] | None]:
    """`/api/decks/<deck_id>/pages/<slide_id>/duplicate` のような 2 つの ID を持つ経路。ID は小文字 UUID に限る。"""
    def match(p: str) -> dict[str, str] | None:
        if not p.startswith(prefix) or not p.endswith(suffix):
            return None
        rest = p[len(prefix):len(p) - len(suffix)] if suffix else p[len(prefix):]
        head, sep, tail = rest.partition(middle)
        if not sep or not is_uuid(head) or head != head.lower() or not is_uuid(tail) or tail != tail.lower():
            return None
        return {name1: head, name2: tail}
    return match


# デッキ・設定の API（別モジュール）。Request / Response を参照するのでここで遅延 import する（循環回避）
from . import deck_routes as _deck_routes  # noqa: E402
from . import edit_routes as _edit_routes  # noqa: E402
from . import export_routes as _export_routes  # noqa: E402
from . import intake_routes as _intake_routes  # noqa: E402
from . import later as _later  # noqa: E402
from . import present_routes as _present_routes  # noqa: E402
from . import samples_routes as _samples_routes  # noqa: E402
from . import settings_routes as _settings_routes  # noqa: E402

_not_ready = _later.not_ready


ROUTES: tuple[tuple[frozenset[str], Callable[[str], dict[str, str] | None], Handler], ...] = (
    (frozenset({"GET"}), _exact("/api/styles"), _settings_routes.list_styles),
    (frozenset({"GET"}), _exact("/api/settings"), _settings_routes.catalog),
    (frozenset({"POST"}), _exact("/api/settings/images"), _settings_routes.upload),
    (frozenset({"POST"}), _exact("/api/settings/server"), _settings_routes.save_server),
    (frozenset({"POST"}), _exact("/api/settings/brands/preview"), _settings_routes.preview_brand),
    (frozenset({"POST"}), _exact("/api/settings/brands/import/check"), _settings_routes.check_import),
    (frozenset({"POST"}), _exact("/api/settings/brands/import"), _settings_routes.import_brand),
    (frozenset({"GET"}), _uuid_route("/api/settings/brands/", "setting_id", "/export"), _settings_routes.export_brand),
    (frozenset({"POST"}), _exact("/api/settings/brands"), _settings_routes.save_brand),
    (frozenset({"POST"}), _uuid_route("/api/settings/brands/", "setting_id"), _settings_routes.save_brand),
    (frozenset({"POST"}), _uuid_route("/api/settings/brands/", "setting_id", "/delete"), _settings_routes.delete_setting),
    (frozenset({"POST"}), _exact("/api/settings/styles/preview"), _settings_routes.preview_style),
    (frozenset({"POST"}), _exact("/api/settings/styles"), _settings_routes.save_style),
    (frozenset({"POST"}), _uuid_route("/api/settings/styles/", "setting_id"), _settings_routes.save_style),
    (frozenset({"POST"}), _uuid_route("/api/settings/styles/", "setting_id", "/delete"), _settings_routes.delete_setting),
    (frozenset({"GET"}), _exact("/settings"), QuickSlideServer._route_index),
    (frozenset({"GET"}), _exact("/settings/brands"), QuickSlideServer._route_index),
    (frozenset({"GET"}), _exact("/settings/styles"), QuickSlideServer._route_index),
    (frozenset({"GET"}), _exact("/settings/server"), QuickSlideServer._route_index),
    (frozenset({"GET"}), _exact("/settings/samples"), QuickSlideServer._route_index),
    (frozenset({"GET"}), _exact("/api/samples"), _samples_routes.route_list),
    (frozenset({"POST"}), _exact("/api/samples/restore"), _samples_routes.route_restore),
    (frozenset({"GET"}), _uuid_route("/settings/brands/", "setting_id"), QuickSlideServer._route_index),
    (frozenset({"GET"}), _exact("/api/health"), QuickSlideServer._route_health),
    (frozenset({"GET"}), _exact("/api/session"), QuickSlideServer._route_session),
    (frozenset({"POST"}), _exact("/api/server/stop"), QuickSlideServer._route_stop),
    (frozenset({"POST"}), _exact("/api/server/restart"), QuickSlideServer._route_restart),
    # 会話からの投入
    (frozenset({"POST"}), _exact("/api/intakes"), _intake_routes.route_intake),
    (frozenset({"GET"}), _uuid_route("/api/launches/", "launch_id"), QuickSlideServer._route_launch_get),
    (frozenset({"POST"}), _uuid_route("/api/launches/", "launch_id", "/attach"), QuickSlideServer._route_attach),
    (frozenset({"POST"}), _exact("/api/events/clipboard-copy"), QuickSlideServer._route_clipboard_copy),
    (frozenset({"GET"}), _uuid_route("/api/assets/", "asset_id", "/file"), QuickSlideServer._route_asset_file),
    # 書き出しの配布（P5。server/export_routes.py）
    (frozenset({"GET"}), _uuid_route("/api/exports/", "export_id", "/file"), _export_routes.route_export_file),
    # --- デッキ管理（server/deck_routes.py）。POST は _route が 3 重検査を掛ける ---
    (frozenset({"GET"}), _exact("/api/vocabulary"), _deck_routes.route_vocabulary),
    (frozenset({"GET"}), _exact("/api/tags"), _deck_routes.route_tags),
    (frozenset({"GET"}), _exact("/api/trash"), _deck_routes.route_trash),
    (frozenset({"GET"}), _two_uuid_route("/api/trash/", "deletion_task_id", "/assets/", "asset_id", "/file"),
     QuickSlideServer._route_trashed_asset_file),
    (frozenset({"POST"}), _exact("/api/trash/purge"), _deck_routes.route_trash_purge),
    # 会話に結びついたスライドを引く（P3。読み取りだが POST: 会話識別子を URL に載せない）
    (frozenset({"POST"}), _exact("/api/conversations/deck"), _edit_routes.route_conversation_deck),
    (frozenset({"GET"}), _exact("/api/decks/home"), _deck_routes.route_home),
    (frozenset({"GET"}), _exact("/api/decks"), _deck_routes.route_list),
    (frozenset({"GET"}), _uuid_route("/api/decks/", "deck_id"), _deck_routes.route_deck),
    (frozenset({"GET"}), _uuid_route("/api/decks/", "deck_id", "/progress"), _deck_routes.route_progress),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/title"), _deck_routes.route_title),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/pin"), _deck_routes.route_pin),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/archive"), _deck_routes.route_archive),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/duplicate"), _deck_routes.route_duplicate),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/delete"), _deck_routes.route_delete),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/restore"), _deck_routes.route_restore),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/purge"), _deck_routes.route_purge),
    # --- 発表の画面（server/present_routes.py）。HTML・API・仕切りの中で開く回の HTML とその画像・字体 ---
    (frozenset({"GET"}), _uuid_route("/api/decks/", "deck_id", "/present"), _present_routes.route_present_data),
    (frozenset({"GET"}), _uuid_route("/slides/", "deck_id", "/present"), _present_routes.route_present_screen),
    (frozenset({"GET"}), _present_routes.present_route, _present_routes.route_present_file),
    # --- 直す・版から戻す・ページの操作（P3。server/edit_routes.py） ---
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/pages/reorder"), _edit_routes.route_reorder),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/pages/insert"), _edit_routes.route_insert),
    (frozenset({"POST"}), _two_uuid_route("/api/decks/", "deck_id", "/pages/", "slide_id", "/duplicate"),
     _edit_routes.route_duplicate),
    (frozenset({"POST"}), _two_uuid_route("/api/decks/", "deck_id", "/pages/", "slide_id", "/visibility"),
     _edit_routes.route_visibility),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/adopt"), _edit_routes.route_adopt),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/instructions"), _edit_routes.route_instructions),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/resume"), _edit_routes.route_resume),
    (frozenset({"GET"}), _two_uuid_route("/api/decks/", "deck_id", "/jobs/", "job_id"), _edit_routes.route_job),
    # --- 書き出し・設定を替えて組み直す（P5） ---
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/exports"), _export_routes.route_create_export),
    (frozenset({"GET"}), _uuid_route("/api/decks/", "deck_id", "/look"), _edit_routes.route_look),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/rebuild"), _edit_routes.route_rebuild),
    # --- まだ無い口（ページの削除・v1 の企画の口）。口は残し、「まだ使えない」と答える ---
    (frozenset({"POST"}), _two_uuid_route("/api/decks/", "deck_id", "/pages/", "slide_id", "/remove"), _not_ready),
    (frozenset({"POST"}), _two_uuid_route("/api/decks/", "deck_id", "/instructions/", "instruction_id", "/generate"),
     _not_ready),
    (frozenset({"GET"}), _uuid_route("/api/decks/", "deck_id", "/plan"), _not_ready),
    (frozenset({"POST"}), _two_uuid_route("/api/decks/", "deck_id", "/plans/", "plan_id", "/approve"), _not_ready),
    (frozenset({"POST"}), _uuid_route("/api/decks/", "deck_id", "/generate"), _not_ready),
    # --- 画面（同じ index.html。画面側が pathname で振り分ける） ---
    (frozenset({"GET"}), _exact("/"), QuickSlideServer._route_index),
    (frozenset({"GET"}), _exact("/slides"), QuickSlideServer._route_index),            # 一覧画面
    (frozenset({"GET"}), _uuid_route("/slides/", "deck_id"), QuickSlideServer._route_index),
    # 古い URL。**会話に残っていることがあるので 404 にしない。** クエリはそのまま連れていく
    (frozenset({"GET"}), _exact("/library"), QuickSlideServer._route_moved),
    (frozenset({"GET"}), _uuid_route("/decks/", "deck_id"), QuickSlideServer._route_moved),
    (frozenset({"GET"}), _static_route, QuickSlideServer._route_static),
)


# --- 起動時掃除（I6） ---
def run_startup_sweep(store: Store) -> dict[str, Any]:
    """アクセス層の掃除を 1 つずつ実行する。1 つが失敗しても他は続け、失敗とスキップを件数で報告する。

    期限切れの起動（launch）と、前のサーバで「走っている」まま残った仕事（失敗にする。勝手に作り直さない）と、
    ごみ箱へ移す途中で止まった削除（ごみ箱から戻せる形にする）。
    """
    summary: dict[str, Any] = {"launches_expired": None, "jobs_interrupted": None, "deletions_recovered": None,
                               "skipped_items": 0, "problems": [], "failed_sweeps": []}
    problems: list[dict[str, Any]] = []
    sweeps: tuple[tuple[str, Callable[[], list[str]]], ...] = (
        ("launches_expired", store.expire_launches),
        ("jobs_interrupted", store.fail_interrupted_jobs),
        ("deletions_recovered", store.recover_interrupted_deletions),
    )
    for name, fn in sweeps:
        try:
            summary[name] = len(fn())
        except Exception as exc:  # noqa: BLE001 - 1 つの掃除の失敗で起動を止めない。失敗は失敗として報告する
            log.error("起動時掃除 %s が失敗（続行）\n%s", name, traceback.format_exc())
            summary["failed_sweeps"].append({"sweep": name, "error_class": exc.__class__.__name__})
    summary["problems"] = problems
    summary["skipped_items"] = len(problems)
    summary["ok"] = not summary["failed_sweeps"]
    return summary
