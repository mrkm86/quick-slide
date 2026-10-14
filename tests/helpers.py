"""テスト共通部品。一時データ置き場にサーバをポート 0（自動割当）で別スレッド起動し、生の HTTP で叩く。

本物の `~/.quick-slide/` は触らない（DataRoot を一時ディレクトリに向ける。環境変数にも依存しない）。
"""

from __future__ import annotations

import functools
import http.client
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quick_slide.clock import Clock, FixedClock  # noqa: E402
from quick_slide.paths import SKILLS_DIR, WEB_APP_DIR, DataRoot  # noqa: E402
from quick_slide.server.app import SESSION_HEADER, QuickSlideServer  # noqa: E402
from quick_slide.settings import Settings  # noqa: E402
from quick_slide.store import Store  # noqa: E402
from quick_slide.worker.tools import Tools  # noqa: E402

FAKES = REPO / "tests" / "fakes"

T0 = "2026-09-07T00:00:00.000000Z"

# 試験の出力に警告の行を混ぜない（ログを見る試験は `capture_logs` で捕まえる）
logging.getLogger("quick_slide").addHandler(logging.NullHandler())


# 環境を一から作る試験でも引き継ぐ、Windows が子を起こすのに要る変数（無い機械では何も足さない）。
# SYSTEMROOT が無いと socket も codex の名前解決も失敗する（B6）。COMSPEC・PATHEXT は .cmd の包みを起こすのに要る
WINDOWS_BASE_VARS = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")


def windows_base_env() -> dict[str, str]:
    return {name: os.environ[name] for name in WINDOWS_BASE_VARS if name in os.environ} if os.name == "nt" else {}


def runnable(script: Path) -> str:
    """偽物（`.py`）を、コマンドとして起こせる形で返す。

    Windows は `.py` を直接起こせない（`shutil.which` も `PATHEXT` に無いので見つけない）。同じ Python で走らせる
    `.cmd` の包みを一時フォルダに作って返す。cmd.exe は包みを OEM のコードページで読むので、その字で書く。
    偽物は `-X utf8` で起こす（本物の codex・claude と同じく、stdin・stdout を UTF-8 で読み書きする）。
    """
    if os.name != "nt":
        return str(script)
    cmd = Path(tempfile.mkdtemp(prefix="quick-slide-launch-")) / f"{script.stem}.cmd"
    cmd.write_text(f'@"{sys.executable}" -X utf8 "{script}" %*\r\n', encoding="oem", newline="")
    return str(cmd)


def temp_dir(testcase, prefix: str = "quick-slide-test-") -> Path:
    tmp = Path(tempfile.mkdtemp(prefix=prefix)).resolve()  # /var → /private/var（置き場は実パスで扱う）
    testcase.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    return tmp


def open_store(testcase, *, clock: Clock | None = None, settings: Settings | None = None) -> Store:
    store = Store.open(DataRoot(temp_dir(testcase) / "home"), clock=clock, settings=settings or Settings())
    testcase.addCleanup(store.close)
    return store


def make_deck(store: Store, title: str = "テストのスライド", *, mode: str = "presentation", **kw: Any) -> dict[str, Any]:
    return store.create_deck(title, mode=mode, brand_revision_id=store.preset_brand_revision_id(),
                             style_revision_id=store.preset_style_revision_id(), **kw)


class CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))

    def text(self) -> str:
        return "\n".join(self.records)


def capture_logs(testcase) -> CapturingHandler:
    """`quick_slide` 配下（store・server・access）の全ログを捕まえる。"""
    handler = CapturingHandler()
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    logger = logging.getLogger("quick_slide")
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    testcase.addCleanup(logger.removeHandler, handler)
    testcase.addCleanup(logger.setLevel, previous)
    return handler


class HttpResult:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    @property
    def error_code(self) -> str | None:
        try:
            return self.json()["error"]["code"]
        except (KeyError, TypeError, ValueError):
            return None

    def __repr__(self) -> str:
        return f"HttpResult({self.status}, {self.body[:200]!r})"


class ServerFixture:
    def __init__(self, tmp: Path, *, settings: Settings | None = None, static_root: Path | None = None,
                 clock: Clock | None = None, host: str = "127.0.0.1", tools: Tools | None = None,
                 start_workers: bool = True, samples_dir: Path | None = None, update_fetch: Any = None) -> None:
        self.tmp = tmp
        self.root = DataRoot(tmp / "home")
        self.clock = clock or FixedClock(T0)
        self.server = QuickSlideServer(self.root, settings=settings or Settings(), host=host, port=0,
                                       static_root=static_root or WEB_APP_DIR, clock=self.clock,
                                       tools=tools or Tools(claude=runnable(FAKES / "refuse.py"), codex=runnable(FAKES / "refuse.py")),
                                       start_workers=start_workers, samples_dir=samples_dir, update_fetch=update_fetch)
        self.thread = self.server.start_in_thread()
        self.port = self.server.bound_port
        self.host = host
        self.responses: list[bytes] = []  # 全応答本文（秘匿走査用）
        self._session: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # --- HTTP ---
    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None, *,
                host: str | None = None, raw_body: bytes | None = None, session: bool = False) -> HttpResult:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h: dict[str, str] = {"Host": host if host is not None else f"127.0.0.1:{self.port}"}
        data: bytes | None = raw_body
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            h["Content-Type"] = "application/json"
        if data is not None:
            h["Content-Length"] = str(len(data))
        if session:
            h[SESSION_HEADER] = self.session()
        h.update(headers or {})
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                conn.putheader(k, v)
            conn.endheaders(data)
            resp = conn.getresponse()
            out = HttpResult(resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read())
        finally:
            conn.close()
        self.responses.append(out.body)
        return out

    def raw(self, payload: bytes) -> bytes:
        """生のバイト列を送って応答全体を返す（http.client が拒む不正な要求行の検査用）。"""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            s.sendall(payload)
            s.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                c = s.recv(65536)
                if not c:
                    break
                chunks.append(c)
        finally:
            s.close()
        out = b"".join(chunks)
        self.responses.append(out)
        return out

    def get(self, path: str, **kw: Any) -> HttpResult:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> HttpResult:
        kw.setdefault("session", True)
        if body is None:
            body = {}
        return self.request("POST", path, body, **kw)

    def session(self) -> str:
        if self._session is None:
            self._session = self.request("GET", "/api/session").json()["session_token"]
        return self._session

    # --- 便利 ---
    @property
    def store(self) -> Store:
        assert self.server.store is not None
        return self.server.store

    def make_deck(self, title: str = "テストのスライド", **kw: Any) -> dict[str, Any]:
        with self.server.store_lock:
            return make_deck(self.store, title, **kw)

    def make_launch(self, deck_id: str | None = None) -> tuple[str, str]:
        """起動を 1 つ作る（P2 では会話からの投入が作る）。戻り値 (launch_id, token)。"""
        with self.server.store_lock:
            row = self.store.create_launch(requested_via="start", deck_id=deck_id)
        return row["id"], row["token"]

    def all_files(self) -> dict[Path, bytes]:
        """データ置き場配下の全ファイル（WAL を本体へ反映後）。"""
        try:
            self.store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:  # noqa: BLE001 - 閉じた後でも走査できるように
            pass
        out: dict[Path, bytes] = {}
        for p in sorted(self.root.root.rglob("*")):
            if p.is_file() or p.is_symlink():
                try:
                    out[p] = p.read_bytes()
                except OSError:
                    out[p] = b""
        return out

    def close(self) -> None:
        try:
            self.server.shutdown()
        finally:
            self.server.close()
        self.thread.join(timeout=5)


def start_server(testcase, **kw: Any) -> ServerFixture:
    fx = ServerFixture(temp_dir(testcase, "quick-slide-srv-"), **kw)
    testcase.addCleanup(fx.close)
    return fx


def png_bytes(width: int = 400, height: int = 120, color: tuple[int, int, int, int] = (20, 40, 200, 255)) -> bytes:
    """テスト用の PNG（Pillow）。"""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


SYMLINK_REASON = "symlink を作れない（Windows は開発者モードか管理者の特権が要る）"


@functools.lru_cache(maxsize=1)
def can_symlink() -> bool:
    """この機械で symlink を作れるか（Windows は特権が無いと WinError 1314。N3）。symlink を断る守りの試験は、作れる機械でだけ走る。"""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.symlink(Path(tmp) / "target", Path(tmp) / "link")
        except OSError:
            return False
    return True


def make_static_root(base: Path) -> tuple[Path, Path]:
    """静的配信の検査用ルート。外側に秘密らしいファイルを置き、内側から symlink で指す。戻り値 (root, outside)。"""
    root = base / "web-app"
    outside = base / "outside"
    (root / "sub").mkdir(parents=True)
    outside.mkdir()
    (root / "index.html").write_text("<!doctype html><title>t</title>INSIDE-INDEX", encoding="utf-8")
    (root / "app.js").write_text("// INSIDE-JS", encoding="utf-8")
    (root / "sub" / "inner.css").write_text("/* INSIDE-CSS */", encoding="utf-8")
    (root / "notes.txt").write_text("INSIDE-TXT-NOT-ALLOWED", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("INSIDE-DOTGIT", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "hmac.key").write_bytes(b"INSIDE-FAKE-KEY-NOT-ALLOWED-EXT")
    (root / "db").mkdir()
    (root / "db" / "quick-slide.db").write_bytes(b"INSIDE-FAKE-DB")
    (root / "trailing-space.html ").write_text("INSIDE-TRAILING-SPACE", encoding="utf-8")
    (root / "trailing-dot.html.").write_text("INSIDE-TRAILING-DOT", encoding="utf-8")
    (root / "café.html").write_text("INSIDE-NFC", encoding="utf-8")  # NFC。NFD で要求しても出ない
    (outside / "secret.html").write_text("OUTSIDE-SECRET-HTML", encoding="utf-8")
    (outside / "hmac.key").write_bytes(b"OUTSIDE-KEY-BYTES")
    if can_symlink():
        os.symlink(outside / "secret.html", root / "link.html")
        os.symlink(outside, root / "linkdir")
        os.symlink(root / "index.html", root / "inside-link.html")  # 内側を指す symlink も拒否
    return root, outside


def allowed_static_bodies(root: Path) -> set[bytes]:
    """性質検査用: 配信されてよいバイト列の全体集合（許可拡張子・通常ファイル・symlink でない・ドット始まりでない）。"""
    from quick_slide.server.static import STATIC_TYPES

    ok: set[bytes] = set()
    for p in root.rglob("*"):
        if p.is_symlink() or not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        if p.suffix in STATIC_TYPES and p.name == p.name.strip() and not p.name.endswith("."):
            ok.add(p.read_bytes())
    return ok


# --- P2: 偽物の台本係・エンジンの偽物の codex ---------------------------------------------------------------

def fake_jwt(exp: float) -> str:
    import base64
    import time

    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc({'exp': int(exp), 'iat': int(time.time())})}.sig"


def fake_codex_home(base: Path) -> Path:
    """エンジンが読む「ChatGPT でログイン済み」の偽の CODEX_HOME（本物のホームは読まない）。"""
    import time

    home = base / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    last = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400))
    (home / "auth.json").write_text(json.dumps({
        "auth_mode": "chatgpt", "OPENAI_API_KEY": None, "last_refresh": last,
        "tokens": {"id_token": "x", "access_token": fake_jwt(time.time() + 5 * 86400), "refresh_token": "r",
                   "account_id": "a"}}), encoding="utf-8")
    return home


def fake_tools(testcase, *, mode: str = "ok", images: bool = True, skills_dir: Path | None = None,
               extra: dict[str, str] | None = None) -> tuple[Tools, Path]:
    """偽物の台本係（claude 風・codex 風）と、エンジンの偽物の codex を差した Tools。戻り値 (tools, 記録の置き場)。

    エンジンは本物（`skills/`）。描いた絵のキャッシュ・エンジンの設定は使い捨てのフォルダへ向ける。
    """
    tmp = temp_dir(testcase, "quick-slide-fakes-")
    env = {
        "FAKE_WRITER_LOG": str(tmp / "writer-calls"), "FAKE_WRITER_MODE": mode, "FAKE_WRITER_IMAGES": "1" if images else "0",
        "FAKE_CODEX_LOG": str(tmp / "codex-calls"), "CODEX_HOME": str(fake_codex_home(tmp)),
        "HTML_SLIDE_CODEX": runnable(SKILLS_DIR / "html-slide" / "tests" / "fake_codex.py"),
        "IMAGE_SLIDE_CODEX": runnable(SKILLS_DIR / "image-slide" / "tests" / "fake_codex.py"),
        "HTML_SLIDE_CACHE": str(tmp / "html-slide-cache"), "IMAGE_SLIDE_CACHE": str(tmp / "image-slide-cache"),
        "MOTION_SLIDE_CACHE": str(tmp / "motion-slide-cache"),
        "HTML_SLIDE_CONFIG": str(tmp / "no-config.yaml"), "IMAGE_SLIDE_CONFIG": str(tmp / "no-config.yaml"),
        **(extra or {}),
    }
    # モーションが組むのに要るもの（Playwright の Chromium）の確かめは、本物を探さず「揃っている」と答える偽物にする
    return Tools(claude=runnable(FAKES / "fake_claude.py"), codex=runnable(FAKES / "fake_codex_text.py"),
                 skills_dir=skills_dir or SKILLS_DIR, extra_env=env,
                 motion_probe=[sys.executable, "-c", "print('[]')"]), tmp


def writer_calls(log_root: Path) -> list[dict[str, Any]]:
    d = log_root / "writer-calls"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


MANUSCRIPT = """# 会議を半分にする（架空の題材）

- 会議は 1 人あたり週 11.5 時間まで増えた（架空）
- 3 つのルール: 25 分を基本に・議題は前日までに・決めたらその場で閉じる
- 来月から 2 チームで試す
"""


def intake_payload(*, runtime: str = "claude_code", title: str | None = "会議を半分にする", **attrs: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"schema_version": 1, "origin": {"kind": "conversation", "runtime": runtime},
                               "manuscript": MANUSCRIPT, "deck_attributes": attrs}
    if title is not None:
        payload["title_hint"] = title
    return payload


def wait_until_idle(fx: "ServerFixture", deck_id: str, seconds: float = 180.0) -> dict[str, Any]:
    """そのデッキの仕事が終わるまで待ち、一番新しい描いた回を返す。"""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with fx.server.store_lock:
            if not fx.store.deck_working(deck_id):
                return fx.store.latest_run(deck_id)
        time.sleep(0.2)
    raise AssertionError(f"{seconds:.0f} 秒待っても仕事が終わらなかった")
