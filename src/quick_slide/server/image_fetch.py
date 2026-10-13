"""画像の URL から画像を取り込む（`POST /api/images/fetch` {url}。docs/adr/0008-edit-kinds.md）。

修正の欄の「画像 → URL を指定」が使う。取ったバイト列は、画像を選んで送ったときと同じ検査と登録を通す
（`settings_routes.store_image`）。URL は覚えない。

行き先の決まり（サーバが自分自身や社内の機械を叩かされないため）:
- `http`・`https` だけ。名前を引いたアドレスが**すべて公開のアドレス**（`ipaddress` の `is_global`）でなければ断る
- つなぐのは、確かめたそのアドレス（名前を引き直さない。引き直しの間に行き先を替えられないため）。https は名前で証明書を確かめる
- 転送（3xx）は 3 回まで追い、転送先も同じ検査をする
- 大きさは画像の上限まで読み、それを超えたら止める。時間は 1 回の接続ごとに上限を持つ
- 経由するプロキシは使わない（社内のプロキシの内側へ出ないため）。プロキシが要る機械では、手元に落としてから添える
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.parse
from typing import Any, Callable

from .errors import ApiError

MAX_URL_CHARS = 2048
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 15
USER_AGENT = "QuickSlide-image-fetch"

URL_INVALID = "画像の URL を読み取れません。http:// か https:// で始まる URL を入れてください。"
URL_NOT_PUBLIC = "この URL の画像は取り込めません（インターネットに公開された場所ではありません）。画像を保存してから添えてください。"
URL_UNREACHABLE = "画像を取りに行けませんでした。URL を確かめるか、画像を保存してから添えてください。"
URL_STATUS = "画像を取りに行けませんでした（{status}）。URL を確かめるか、画像を保存してから添えてください。"
URL_TOO_MANY_REDIRECTS = "転送が多すぎて、画像にたどり着けませんでした。画像そのものの URL を入れてください。"
URL_TOO_LARGE = "画像が大きすぎます。10 MiB 以下の画像を選んでください。"

Resolver = Callable[[str, int], list[str]]


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def _check_url(raw: Any) -> urllib.parse.SplitResult:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_URL_CHARS:
        raise ApiError(400, "image_url_invalid", URL_INVALID)
    parts = urllib.parse.urlsplit(raw.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ApiError(400, "image_url_invalid", URL_INVALID)
    try:
        parts.port  # 数字でないポートは ValueError
    except ValueError:
        raise ApiError(400, "image_url_invalid", URL_INVALID) from None
    return parts


def public_address(host: str, port: int, resolve: Resolver = _resolve) -> str:
    """名前を引き、すべてが公開のアドレスならその 1 つ目を返す。1 つでも公開でなければ断る。"""
    try:
        addresses = resolve(host, port)
    except (OSError, UnicodeError):
        raise ApiError(400, "image_url_unreachable", URL_UNREACHABLE) from None
    if not addresses:
        raise ApiError(400, "image_url_unreachable", URL_UNREACHABLE)
    for a in addresses:
        try:
            ip = ipaddress.ip_address(a.split("%", 1)[0])
        except ValueError:
            raise ApiError(400, "image_url_not_public", URL_NOT_PUBLIC) from None
        if not ip.is_global or ip.is_multicast:
            raise ApiError(400, "image_url_not_public", URL_NOT_PUBLIC)
    return addresses[0]


class _PinnedHTTPS(http.client.HTTPSConnection):
    """確かめたアドレスへつなぎ、証明書は名前で確かめる。"""

    def __init__(self, host: str, address: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, address: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


def download(raw_url: Any, *, max_bytes: int, resolve: Resolver = _resolve) -> bytes:
    """URL の中身を `max_bytes` まで読む。行き先の決まりは冒頭のとおり。"""
    parts = _check_url(raw_url)
    for _ in range(MAX_REDIRECTS + 1):
        port = parts.port or (443 if parts.scheme == "https" else 80)
        address = public_address(parts.hostname or "", port, resolve)
        cls = _PinnedHTTPS if parts.scheme == "https" else _PinnedHTTP
        conn = cls(parts.hostname or "", address, port, TIMEOUT_SECONDS)
        path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        try:
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
            res = conn.getresponse()
            if res.status in (301, 302, 303, 307, 308):
                location = res.getheader("Location")
                if not location:
                    raise ApiError(400, "image_url_unreachable", URL_UNREACHABLE)
                parts = _check_url(urllib.parse.urljoin(urllib.parse.urlunsplit(parts), location))
                continue
            if res.status != 200:
                raise ApiError(400, "image_url_status", URL_STATUS.format(status=res.status))
            data = res.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ApiError(413, "image_size_limit", URL_TOO_LARGE)
            return data
        except (OSError, http.client.HTTPException, ssl.SSLError):
            raise ApiError(400, "image_url_unreachable", URL_UNREACHABLE) from None
        finally:
            conn.close()
    raise ApiError(400, "image_url_redirects", URL_TOO_MANY_REDIRECTS)
