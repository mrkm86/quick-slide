"""本物のブラウザで画面を動かすための最小の道具（CDP を素の socket で話す）。

**なぜ要るか。** 画面の JavaScript を 1 行も動かさない試験は、「画面が読む値を答えが持っていない」を
捕まえられない（v1 で、試験が全部緑のまま画面が凍ったことがある）。だから本物で動かす。

依存は足さない（Python 標準ライブラリだけ）。node も puppeteer も要らない。
Chrome が無い環境では `available()` が偽を返し、呼び側が skip する。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455


def chrome_path() -> str | None:
    for p in CHROME_PATHS:
        if Path(p).is_file():
            return p
    return shutil.which("google-chrome") or shutil.which("chromium")


def available() -> bool:
    return chrome_path() is not None


class _Socket:
    """WebSocket のクライアント側。テキストフレームだけ扱えればよい。"""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)$", url)
        if m is None:
            raise ValueError(f"ws の URL として読めない: {url!r}")
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode("ascii")
        )
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            self._fill()
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0] + b" ":
            raise RuntimeError(f"Upgrade されなかった: {head[:200]!r}")
        want = base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")
        if want.encode("ascii") not in head:
            raise RuntimeError("Sec-WebSocket-Accept が合わない")

    def _fill(self) -> None:
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionError("ブラウザとの接続が切れた")
        self.buf += chunk

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            self._fill()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self, opcode: int, data: bytes) -> None:
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, text: str) -> None:
        self._frame(0x1, text.encode("utf-8"))

    def recv(self) -> str:
        payload = b""
        while True:
            b0, b1 = self._read(2)
            fin, op, ln = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            body = self._read(ln) if ln else b""
            if op == 0x8:
                raise ConnectionError("ブラウザが接続を閉じた")
            if op == 0x9:
                self._frame(0xA, body)   # ping には pong
                continue
            if op == 0xA:
                continue
            payload += body
            if fin:
                return payload.decode("utf-8")

    def close(self) -> None:
        try:
            self._frame(0x8, b"")
        except OSError:
            pass
        self.sock.close()


class JsError(AssertionError):
    """画面の JavaScript が投げた例外。**握りつぶさない**（握りつぶすと、画面が凍っても試験は緑のまま）。"""


class Page:
    """開いた 1 枚の画面。`eval` で読み、`errors` に投げられた例外が溜まる。"""

    def __init__(self, ws_url: str) -> None:
        self.ws = _Socket(ws_url)
        self._id = 0
        self.errors: list[str] = []
        self.call("Runtime.enable")
        self.call("Page.enable")

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        mine = self._id
        self.ws.send(json.dumps({"id": mine, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") == "Runtime.exceptionThrown":
                self.errors.append(_describe(msg["params"].get("exceptionDetails") or {}))
            if msg.get("id") == mine:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result") or {}

    def eval(self, expression: str) -> Any:
        r = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise JsError(_describe(r["exceptionDetails"]))
        return r.get("result", {}).get("value")

    def until(self, expression: str, *, seconds: float = 20.0, what: str = "") -> Any:
        """真になるまで待つ。**待っている間も例外は拾い続ける**ので、凍った理由が結果に出る。"""
        deadline = time.monotonic() + seconds
        last: Any = None
        while time.monotonic() < deadline:
            last = self.eval(expression)
            if last:
                return last
            self.raise_if_broken()
            time.sleep(0.25)
        raise AssertionError(f"{seconds:.0f} 秒待っても真にならなかった: {what or expression}（最後の値 {last!r}）"
                             + (f"\n画面が投げた例外:\n" + "\n".join(self.errors) if self.errors else ""))

    def goto(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})
        self.until("document.readyState === 'complete'", seconds=30, what=f"{url} が読み込み終わること")

    def raise_if_broken(self) -> None:
        if self.errors:
            raise JsError("画面の JavaScript が例外を投げた:\n" + "\n".join(self.errors))

    def close(self) -> None:
        self.ws.close()


def _describe(details: dict[str, Any]) -> str:
    exc = details.get("exception") or {}
    text = exc.get("description") or exc.get("value") or details.get("text") or "（内容不明）"
    where = details.get("url") or ""
    line = details.get("lineNumber")
    return f"{text}".strip() + (f"  [{where}:{line}]" if where else "")


class Browser:
    """ヘッドレス Chrome を 1 つ起こす。使い終わったら必ず `close()`。"""

    def __init__(self, *, width: int = 1280, height: int = 1600) -> None:
        exe = chrome_path()
        if exe is None:
            raise RuntimeError("Chrome が無い")
        self.profile = Path(tempfile.mkdtemp(prefix="quick-slide-chrome-"))
        self.proc = subprocess.Popen(
            [exe, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
             "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars",
             "--disable-extensions", "--disable-background-timer-throttling",
             "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
             f"--window-size={width},{height}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.port = self._wait_for_port()
        self.pages: list[Page] = []

    def _wait_for_port(self, seconds: float = 30.0) -> int:
        f = self.profile / "DevToolsActivePort"
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if f.is_file():
                text = f.read_text(encoding="utf-8").splitlines()
                if text and text[0].strip().isdigit():
                    return int(text[0].strip())
            if self.proc.poll() is not None:
                raise RuntimeError(f"Chrome が起動直後に終了した（exit {self.proc.returncode}）")
            time.sleep(0.05)
        raise RuntimeError("Chrome の DevTools ポートを確認できなかった")

    def open(self, url: str) -> Page:
        """空の頁を作ってから `Page.navigate` で行く。URL を Chrome の問い合わせ文字列に載せない
        （載せると、どう符号化されるかという別の話に振り回される）。"""
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req, timeout=30) as resp:
            tab = json.loads(resp.read().decode("utf-8"))
        page = Page(tab["webSocketDebuggerUrl"])
        self.pages.append(page)
        page.goto(url)
        return page

    def close(self) -> None:
        for p in self.pages:
            try:
                p.close()
            except OSError:
                pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)
