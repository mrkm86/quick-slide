"""時刻。DB に入れる時刻は UTC RFC3339（マイクロ秒、`Z` 終端）で固定し、文字列比較で順序が出る形にする。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

RFC3339_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utcnow() -> str:
    return format_rfc3339(datetime.now(timezone.utc))


def format_rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime は受け付けない（UTC aware を渡す）")
    return dt.astimezone(timezone.utc).strftime(RFC3339_FORMAT)


def parse_rfc3339(text: str) -> datetime:
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def add_seconds(text: str, seconds: float) -> str:
    return format_rfc3339(parse_rfc3339(text) + timedelta(seconds=seconds))


class Clock:
    """差し替え可能な時計。テストでは `FixedClock` を使う。"""

    def now(self) -> str:
        return utcnow()


class FixedClock(Clock):
    def __init__(self, start: str) -> None:
        self._now = start

    def now(self) -> str:
        return self._now

    def set(self, text: str) -> None:
        self._now = text

    def advance(self, seconds: float) -> str:
        self._now = add_seconds(self._now, seconds)
        return self._now


def elapsed_clock() -> tuple[str, Callable[[], float]]:
    """**止まらない経過時間**を読む時計を選び、名前と読み手を返す（稼働時間の計測用）。

    `time.monotonic()` は名前に反して、**この機械では眠っている間止まる**。macOS の Python は
    これを `CLOCK_UPTIME_RAW`（起きている時間だけ）へ割り当てるためで、2026-09-24 に実測した:
    機械の起動から 10日23:01（946,860 秒）経っているのに `time.monotonic()` は 291,416 秒
    （3日8:56）しか返さなかった。3.25 倍の過少報告である。蓋を閉じて持ち歩く機械なので、
    「立ち上げてから何日動いているか」を知りたい用途では、この差はそのまま嘘になる。

    選ぶ順は 1 つの理由で決まる——**眠っている間も数えるか**:

    - `CLOCK_BOOTTIME`（Linux）: 起動から。サスペンドを含む
    - `CLOCK_MONOTONIC`（macOS）: 起動から。スリープを含む（Linux では逆に含まないので後ろ）
    - `time.monotonic()`: どちらも無いときの最後の手段

    壁時計（`CLOCK_REALTIME` / `utcnow`）は使わない。NTP の補正や手動の時刻変更で**戻る**ので、
    経過時間が負になり得る。ここで欲しいのは「時刻」ではなく「経ったこと」である。
    """
    import sys
    import time

    if sys.platform.startswith("linux") and hasattr(time, "CLOCK_BOOTTIME"):
        return "CLOCK_BOOTTIME", lambda: time.clock_gettime(time.CLOCK_BOOTTIME)
    if sys.platform == "darwin" and hasattr(time, "CLOCK_MONOTONIC"):
        return "CLOCK_MONOTONIC", lambda: time.clock_gettime(time.CLOCK_MONOTONIC)
    return "time.monotonic", time.monotonic
