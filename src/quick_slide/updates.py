"""新しいリリースのお知らせ（docs/adr/0020-release-and-update-notice.md）。

- 版は SemVer（`MAJOR.MINOR.PATCH`。`-` の後ろはプレリリース）。正本は `quick_slide.__version__` で、
  プラグインのマニフェスト 2 つと揃える（tests/test_version.py）
- 確かめる先は GitHub の「最新のリリース」（`releases/latest`。下書きとプレリリースは入らない）だけ。
  画面は外へ出られない（CSP）ので、画面 → このサーバの `GET /api/update` → GitHub の順に問い合わせる
- 答えはメモリに覚える（`CACHE_SECONDS`）。確かめられなかったときは前の答えを `stale` で返し、
  前の答えも無ければ「新しい版は無い」として扱う（お知らせは出さない。画面は黙る）
- 送るのは決まった URL への GET 1 つだけ（User-Agent に版を載せる）。データの置き場の中身は何も送らない
- 止める: 設定 › 更新 の切り替え（`settings.json` の `updates.check`）か、環境変数 `QUICK_SLIDE_NO_UPDATE_CHECK=1`
- `python -m quick_slide.updates` は、いま確かめた答えを JSON 1 行で出す（会話の更新 `quick_slide_update.py` が使う。
  頼まれた確かめなので、上の 2 つは見ない）
"""

from __future__ import annotations

import http.client
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from . import __version__
from .clock import format_rfc3339

# 公開リポジトリ（確かめる先とリンクの元。ここ 1 か所に書く）
REPOSITORY = "mrkm86/quick-slide-v2"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"

ENV_NO_CHECK = "QUICK_SLIDE_NO_UPDATE_CHECK"
CACHE_SECONDS = 6 * 60 * 60        # 確かめられた答えを覚えておく長さ
RETRY_SECONDS = 30 * 60            # 確かめられなかったあと、次に問い合わせるまで（画面を開くたびに叩かない）
REFRESH_MIN_SECONDS = 30           # 「今すぐ確かめる」を続けて押しても、この間は前の答えを返す
TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_NOTES_CHARS = 20_000

# 確かめられなかった理由（画面は code で文を選ぶ）
UNREACHABLE = "unreachable"        # つながらない・GitHub が 404 以外のエラーを返した
NO_RELEASE = "no_release"          # リリースがまだ無い（公開前のリポジトリも 404 になる）
BAD_RESPONSE = "bad_response"      # 答えの形が読めない・版の形でない

_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
# タグの頭（`v2.1.0`。`claude plugin tag` の `quick-slide--v2.1.0` も読む）
_TAG_PREFIX = re.compile(r"^(?:quick-slide--)?v")


class FetchFailed(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


Fetch = Callable[[], bytes]


def parse_version(text: Any) -> tuple | None:
    """SemVer の比べ方の鍵。版の形でなければ None。正式版はプレリリースより新しい（2.1.0-rc.1 < 2.1.0）。"""
    if not isinstance(text, str):
        return None
    m = _SEMVER.match(text.strip())
    if not m:
        return None
    core = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if m.group(4) is None:
        return (*core, 1, ())
    ids = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in m.group(4).split("."))
    return (*core, 0, ids)


def version_from_tag(tag: Any) -> str | None:
    if not isinstance(tag, str):
        return None
    version = _TAG_PREFIX.sub("", tag.strip(), count=1)
    return version if parse_version(version) is not None else None


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def env_disables(environ: Mapping[str, str]) -> bool:
    return (environ.get(ENV_NO_CHECK) or "").strip().lower() in ("1", "true", "yes", "on")


def fetch_latest() -> bytes:
    """GitHub の最新のリリースを 1 回取る（常駐の serve が使う。試験は差し替える）。"""
    req = urllib.request.Request(LATEST_RELEASE_API, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"QuickSlide/{__version__}",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise FetchFailed(NO_RELEASE if exc.code == 404 else UNREACHABLE) from None
    except (OSError, ValueError, http.client.HTTPException):
        raise FetchFailed(UNREACHABLE) from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise FetchFailed(BAD_RESPONSE)
    return data


def read_release(data: bytes) -> dict[str, Any]:
    """GitHub の答えから、画面に出すものだけを抜く。リンクは公開リポジトリのリリースの頁に限る。"""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FetchFailed(BAD_RESPONSE) from None
    if not isinstance(obj, dict):
        raise FetchFailed(BAD_RESPONSE)
    tag = obj.get("tag_name")
    version = version_from_tag(tag)
    if version is None:
        raise FetchFailed(BAD_RESPONSE)
    url = obj.get("html_url")
    if not (isinstance(url, str) and url.startswith(RELEASES_PAGE + "/tag/")):
        url = f"{RELEASES_PAGE}/tag/{urllib.parse.quote(str(tag), safe='')}"
    notes = obj.get("body") if isinstance(obj.get("body"), str) else ""
    if len(notes) > MAX_NOTES_CHARS:
        notes = notes[:MAX_NOTES_CHARS].rstrip() + "\n…（続きはリリースの頁で）"
    name = obj.get("name") if isinstance(obj.get("name"), str) and obj.get("name").strip() else None
    published = obj.get("published_at") if isinstance(obj.get("published_at"), str) else None
    return {"version": version, "url": url, "name": name, "published_at": published, "notes": notes.strip()}


class UpdateChecker:
    """最新のリリースを確かめて覚える。`fetch` が None のサーバ（試験の既定）は外へ出ない。"""

    def __init__(self, fetch: Fetch | None, *, current: str = __version__, now: Callable[[], float] = time.time) -> None:
        self.fetch = fetch
        self.current = current
        self._now = now
        self._lock = threading.Lock()
        self._release: dict[str, Any] | None = None   # 最後に確かめられた答え
        self._checked_at: float | None = None          # 最後に確かめられた時刻
        self._error: str | None = None                 # 最後の問い合わせが失敗した理由（成功したら None）
        self._next_at = 0.0                            # これより前は問い合わせない
        self._last_try = float("-inf")

    def status(self, *, disabled_by: str | None, refresh: bool = False) -> dict[str, Any]:
        """画面に返す形。`disabled_by` は settings・env（止めている理由）か None。"""
        if self.fetch is None and disabled_by is None:
            disabled_by = "server"
        if disabled_by is None:
            self._maybe_check(refresh)
        with self._lock:
            release, checked_at, error = self._release, self._checked_at, self._error
        newer = disabled_by is None and release is not None and is_newer(release["version"], self.current)
        return {
            "current_version": self.current,
            "enabled": disabled_by is None,
            "disabled_by": disabled_by,
            "latest_version": release["version"] if release else None,
            "update_available": newer,
            "release_url": release["url"] if release else None,
            "release_name": release["name"] if release else None,
            "published_at": release["published_at"] if release else None,
            "notes": release["notes"] if release else None,
            "checked_at": _stamp(checked_at),
            "stale": bool(error and release),
            "error": error if disabled_by is None else None,
            "releases_url": RELEASES_PAGE,
        }

    def _maybe_check(self, refresh: bool) -> None:
        # 問い合わせの間は鍵を持ったまま待つ（同時に開いた画面が GitHub を 2 回叩かない。上限は TIMEOUT_SECONDS）
        with self._lock:
            now = self._now()
            if refresh:
                if now - self._last_try < REFRESH_MIN_SECONDS:
                    return
            elif now < self._next_at:
                return
            self._last_try = now
            try:
                release = read_release(self.fetch())   # type: ignore[misc]
            except Exception as exc:  # noqa: BLE001 - お知らせのために画面を壊さない（理由は code だけ返す）
                self._error = exc.code if isinstance(exc, FetchFailed) else UNREACHABLE
                self._next_at = now + RETRY_SECONDS
                return
            self._release, self._checked_at, self._error = release, now, None
            self._next_at = now + CACHE_SECONDS


def _stamp(t: float | None) -> str | None:
    return format_rfc3339(datetime.fromtimestamp(t, timezone.utc)) if t is not None else None


def main() -> int:
    """`python -m quick_slide.updates`: いま GitHub に確かめた答えを JSON 1 行で出す（会話の更新が使う。
    利用者が頼んだ確かめなので、設定の切り替えと環境変数は見ない）。"""
    import sys

    status = UpdateChecker(fetch_latest).status(disabled_by=None, refresh=True)
    sys.stdout.write(json.dumps(status, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
