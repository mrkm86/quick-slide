"""設定（`config/settings.json`。秘密は入れない）。

`intake`（原稿の上限・抜粋の保存期間）・`launch`（起動トークンの期限）・`browser`（画面の開き方）・
`server`（bind 先とポート）・`writer`（台本係の LLM: `auto` / `claude` / `codex`）・`worker`（同時に動かす仕事の数・
時間の上限・検査の回数）・既定のブランド。値は暫定運用値。未知のキーは拒否せず無視する。

`browser.mode` は環境変数 `QUICK_SLIDE_BROWSER` で上書きできる。

**効くとき**（P4）: `server`（待ち受けのアドレス・名前・ポート）だけが再起動を要る。ほかの段（台本係・ワーカー・
取り込みの上限・起動トークン・画面の開き方・既定のブランド）は、サーバが仕事を始めるたびにファイルの印
（`file_fingerprint`）を見て、変わっていれば読み直す（`server/app.QuickSlideServer.current_settings`）。
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ValidationError

ENV_BROWSER = "QUICK_SLIDE_BROWSER"
# auto = 統合ブラウザ → 外部ブラウザ → URL 提示の 3 段 / external = 経路 1 を飛ばす / manual = 開かず URL だけ
BROWSER_MODES = ("auto", "external", "manual")

# 既定の bind 先の代表的な書き方（画面に見せる一覧）。**ループバックかどうかの判定には使わない** —
# 判定は `is_loopback_host` がアドレスの意味で行う（`0:0:0:0:0:0:0:1`・`127.0.0.2`・`::ffff:127.0.0.1` もループバック）。
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
# 「ぜんぶの口」を意味するアドレスの代表的な書き方。判定は `is_wildcard_host`（`0:0::0` なども同じ扱い）。
WILDCARD_HOSTS = ("0.0.0.0", "::")
DEFAULT_PORT = 7801  # v1 と並べて動かさない（データの置き場も分ける）
# 台本係の LLM。auto = 会話の出所（origin.runtime）に合わせ、分からなければ手元にある CLI（claude → codex の順）
WRITER_CHOICES = ("auto", "claude", "codex")



def save_server_section(path: Path, *, host: str, host_names: Sequence[str], port: int | None = None) -> ServerSettings:
    """`settings.json` の `server` だけを書き換える（画面から直せるように）。

    **他のキーには触らない。** このファイル は手でも直すので、知らないキーを落とすと人の設定が消える。
    値は書く前に `ServerSettings` と `bind_refusal` に通す。通らないものはファイルに触れずに断る。
    `port` を省けば、いま書いてある値を保つ。
    """
    names = tuple(host_names)
    settings = ServerSettings(host=host, port=_existing_port(path) if port is None else port, host_names=names)
    refusal = bind_refusal(settings.host)
    if refusal is not None:
        raise ValidationError(refusal)
    raw = _read_raw(path)
    server = dict(raw.get("server") or {})
    server["host"] = settings.host
    if names:
        server["host_names"] = list(names)
    else:
        server.pop("host_names", None)
    if port is not None:
        server["port"] = settings.port
    raw["server"] = server
    _write_raw(path, raw)
    return settings


def restore_server_section(path: Path, listen: dict) -> None:
    """`server` の段だけを `listen`（{host, port, host_names}）に戻す（再起動のやり直し。ほかのキーは触らない）。

    読めない settings.json（JSON が壊れている）は、`server` の段だけを持つ新しい内容に置き換える
    （壊れた中身は `settings.json.broken` に残す）。
    """
    try:
        raw = _read_raw(path)
    except ValidationError:
        if path.exists():
            os.replace(path, path.with_name(path.name + ".broken"))
        raw = {}
    server = dict(raw.get("server") or {}) if isinstance(raw.get("server"), dict) else {}
    server["host"] = listen["host"]
    server["port"] = listen["port"]
    if listen.get("host_names"):
        server["host_names"] = list(listen["host_names"])
    else:
        server.pop("host_names", None)
    raw["server"] = server
    _write_raw(path, raw)


def _read_raw(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"settings.json を読めません: {exc.__class__.__name__}") from exc
    if not isinstance(raw, dict):
        raise ValidationError("settings.json はオブジェクトである必要があります")
    return raw


def _write_raw(path: Path, raw: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".writing")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)   # 途中で落ちても、古い内容か新しい内容のどちらかになる


def file_fingerprint(path: Path) -> tuple[int, int, str] | None:
    """設定のファイルの印（mtime・大きさ・中身の sha256）。無ければ None。手で直したかを見比べるのに使う。"""
    try:
        st = path.stat()
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return (-1, -1, "")
    return (st.st_mtime_ns, st.st_size, hashlib.sha256(data).hexdigest())


def listen_of(server: "ServerSettings") -> dict:
    """待ち受けの 3 つ（再起動が要る設定）。見比べと控え（last-good）に使う形。"""
    return {"host": server.host, "port": server.port, "host_names": list(server.host_names)}


def expected_url(listen: dict) -> str | None:
    """その待ち受けで起動したときに人に見せる URL（名前があれば 1 つ目、無ければアドレス）。ポート 0 なら分からない。"""
    port = listen.get("port")
    if not isinstance(port, int) or port <= 0:
        return None
    names = listen.get("host_names") or []
    host = names[0] if names else listen["host"]
    return f"http://{'[' + host + ']' if ':' in host else host}:{port}"


def _existing_port(path: Path) -> int:
    """いま書いてある port を保つ（この画面では触らせない）。"""
    if not path.exists():
        return DEFAULT_PORT
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_PORT
    port = (raw.get("server") or {}).get("port", DEFAULT_PORT) if isinstance(raw, dict) else DEFAULT_PORT
    return port if isinstance(port, int) and not isinstance(port, bool) else DEFAULT_PORT


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """アドレスとして読めればその値。IPv4 を写した IPv6（`::ffff:a.b.c.d`）は中の IPv4 として読む。名前なら None。"""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def is_loopback_host(host: str) -> bool:
    """この bind 先（や bind した結果のアドレス）がループバックか。**文字列の一致ではなくアドレスの意味で決める。**

    `0:0:0:0:0:0:0:1`（= `::1`）・`127.0.0.2`・`::ffff:127.0.0.1` もループバック。名前は `localhost` だけを
    ループバックとみなす（大文字小文字・末尾の `.` は問わない）。ループバックかどうかを決める場所は、ここ 1 か所に寄せる
    （ほかの場所で文字列を見比べると、別の書き方で `host_names` の禁止をすり抜けられる）。
    """
    if not isinstance(host, str):
        return False
    if host.lower().rstrip(".") == "localhost":
        return True
    addr = _address(host)
    return addr is not None and addr.is_loopback


def is_wildcard_host(host: str) -> bool:
    """「ぜんぶの口」（`0.0.0.0`・`::` とその別の書き方）か。"""
    addr = _address(host) if isinstance(host, str) else None
    return addr is not None and addr.is_unspecified


def bind_scope(host: str) -> str:
    """この bind 先は、この機械の中だけか、外にも届くか（health がそのまま出す）。"""
    return "loopback" if is_loopback_host(host) else "network"


def bind_refusal(host: str) -> str | None:
    """この bind 先を拒む理由。許すなら None。

    許すのは、ループバックと、**設定に書かれた 1 つのアドレス**。黙って 127.0.0.1 に落とさず、
    理由を言って止まる。どの網が安全かはアプリには分からないので、そこは判断しない。
    判断するのは 2 つだけ — 「ぜんぶの口」ではないこと、名前ではなくアドレスであること。
    """
    if is_loopback_host(host):
        return None
    if is_wildcard_host(host):
        return (
            f"{host} は「すべてのネットワーク」を意味するため使えません（server.host）。"
            "公開するネットワークの IP アドレスを書いてください"
        )
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return (
            f"{host} は名前です。IP アドレスで書いてください（server.host）。"
            "名前のままでは、どのネットワークに公開されるかを設定から決められないためです"
        )
    return None


@dataclass(frozen=True)
class IntakeSettings:
    max_manuscript_chars: int = 200_000
    max_transcript_chars: int = 400_000
    max_style_chars: int = 400
    transcript_ttl_hours: float = 24.0
    min_conversation_key_chars: int = 8
    extra_secret_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class LaunchSettings:
    token_ttl_seconds: int = 15 * 60


@dataclass(frozen=True)
class BrowserSettings:
    """ブラウザ 3 段経路の設定。

    - `mode`: `auto` / `external` / `manual`
    - `attach_wait_seconds`: 経路 1・2 のそれぞれで `launch ensure` が attach を待つ秒数（暫定 8 秒）。
      サーバは待たない（待つのは CLI）。ここでは値を保持・検証し、health で「設定としてこう読んだ」と報告するだけ。
    """

    mode: str = "auto"
    attach_wait_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.mode not in BROWSER_MODES:
            raise ValidationError(f"browser.mode は {'/'.join(BROWSER_MODES)} のいずれか")
        w = self.attach_wait_seconds
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w != w or not (0 < w <= 600):
            raise ValidationError("browser.attach_wait_seconds は 0 より大きく 600 以下の数値")


@dataclass(frozen=True)
class ServerSettings:
    """常駐サーバの bind 先。許した先以外は**起動時に拒否**する（黙って 127.0.0.1 に落とさない）。

    - `host`: ループバック（既定）か、利用者が書いたアドレス。可否は `bind_refusal` が一手に決める
    - `host_names`: `Host:` ヘッダで名乗ってよい名前（機械の名前など）。**ループバック以外に bind したときだけ**書ける。
      ループバックに別名を許すと、悪意ある頁が名前を 127.0.0.1 に付け替えて忍び込める（DNS リバインディング）
    """

    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    host_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host:
            raise ValidationError("server.host は非空の文字列")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not (0 <= self.port <= 65535):
            raise ValidationError("server.port は 0〜65535 の整数（0 は自動割当。テスト用）")
        if not isinstance(self.host_names, tuple):
            raise ValidationError("server.host_names は文字列の配列")
        for name in self.host_names:
            if not isinstance(name, str) or not name.strip():
                raise ValidationError("server.host_names の要素は非空の文字列")
            if any(c in name for c in ":/ "):
                raise ValidationError(f"server.host_names には名前だけを書きます（ポートも / も付けない）: {name!r}")
        if self.host_names and self.is_loopback:
            raise ValidationError(
                "server.host_names は server.host がループバック以外のアドレスのときだけ使えます。"
                "ループバックに別名を許すと、悪意ある頁からの名前の付け替えを通してしまいます"
            )

    @property
    def is_loopback(self) -> bool:
        return is_loopback_host(self.host)

    @property
    def display_host(self) -> str:
        """人に見せる URL に使う名前。名前を書いてあればそれ、無ければアドレスそのもの。"""
        return self.host_names[0] if self.host_names else self.host


@dataclass(frozen=True)
class WorkerSettings:
    """台本係・描き係（サーバの中のワーカー）。

    - `parallel`: 同時に進める仕事の数（別のデッキ・同じデッキの別のページの直し。既定 4。docs/adr/0012。
      1 つの仕事の中の並列（ページの絵を同時に描く）はエンジンが持つ。image-slide は既定 8 本）
    - `writer_timeout_seconds`: 台本係の LLM 1 回の上限
    - `render_timeout_seconds`: エンジン 1 回（描いて書き出すまで）の上限
    - `max_check_rounds`: 台本を書いて検査に通すまでの回数の上限（`NG:` を返して直させる）
    """

    parallel: int = 4
    writer_timeout_seconds: float = 900.0
    render_timeout_seconds: float = 3600.0
    max_check_rounds: int = 3

    def __post_init__(self) -> None:
        if isinstance(self.parallel, bool) or not isinstance(self.parallel, int) or not (1 <= self.parallel <= 8):
            raise ValidationError("worker.parallel は 1〜8 の整数")
        for name in ("writer_timeout_seconds", "render_timeout_seconds"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not (0 < v <= 24 * 3600):
                raise ValidationError(f"worker.{name} は 0 より大きく 86400 以下の数値")
        r = self.max_check_rounds
        if isinstance(r, bool) or not isinstance(r, int) or not (1 <= r <= 10):
            raise ValidationError("worker.max_check_rounds は 1〜10 の整数")


@dataclass(frozen=True)
class Settings:
    intake: IntakeSettings = field(default_factory=IntakeSettings)
    launch: LaunchSettings = field(default_factory=LaunchSettings)
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    server: ServerSettings = field(default_factory=ServerSettings)
    writer: str = "auto"
    worker: WorkerSettings = field(default_factory=WorkerSettings)
    default_brand_id: str | None = None

    def __post_init__(self) -> None:
        if self.writer not in WRITER_CHOICES:
            raise ValidationError(f"writer は {'/'.join(WRITER_CHOICES)} のいずれか")

    @classmethod
    def load(cls, path: Path, *, env: dict[str, str] | None = None) -> "Settings":
        """`settings.json` を読む。無ければ既定。`env`（既定は os.environ）の `QUICK_SLIDE_BROWSER` が `browser.mode` を上書きする。"""
        environ = os.environ if env is None else env
        raw: dict = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValidationError(f"settings.json を読めません: {exc.__class__.__name__}") from exc
            if not isinstance(raw, dict):
                raise ValidationError("settings.json はオブジェクトである必要があります")
        intake_raw = _section(raw, "intake")
        launch_raw = _section(raw, "launch")
        browser_raw = _section(raw, "browser")
        server_raw = _section(raw, "server")
        worker_raw = _section(raw, "worker")
        try:
            intake = IntakeSettings(
                max_manuscript_chars=int(intake_raw.get("max_manuscript_chars", IntakeSettings.max_manuscript_chars)),
                max_transcript_chars=int(intake_raw.get("max_transcript_chars", IntakeSettings.max_transcript_chars)),
                max_style_chars=int(intake_raw.get("max_style_chars", IntakeSettings.max_style_chars)),
                transcript_ttl_hours=float(intake_raw.get("transcript_ttl_hours", IntakeSettings.transcript_ttl_hours)),
                min_conversation_key_chars=int(
                    intake_raw.get("min_conversation_key_chars", IntakeSettings.min_conversation_key_chars)
                ),
                extra_secret_patterns=tuple(str(p) for p in intake_raw.get("extra_secret_patterns", ())),
            )
            launch = LaunchSettings(token_ttl_seconds=int(launch_raw.get("token_ttl_seconds", LaunchSettings.token_ttl_seconds)))
            mode = browser_raw.get("mode", BrowserSettings.mode)
            env_mode = environ.get(ENV_BROWSER)
            if env_mode is not None and env_mode != "":
                mode = env_mode
            wait_raw = browser_raw.get("attach_wait_seconds", BrowserSettings.attach_wait_seconds)
            if isinstance(wait_raw, bool) or not isinstance(wait_raw, (int, float)):
                raise ValidationError("browser.attach_wait_seconds は数値")
            browser = BrowserSettings(mode=mode, attach_wait_seconds=float(wait_raw))
            port_raw = server_raw.get("port", ServerSettings.port)
            if isinstance(port_raw, bool) or not isinstance(port_raw, int):
                raise ValidationError("server.port は整数")
            names_raw = server_raw.get("host_names", ())
            if isinstance(names_raw, str) or not isinstance(names_raw, (list, tuple)):
                raise ValidationError("server.host_names は文字列の配列")
            server = ServerSettings(
                host=server_raw.get("host", ServerSettings.host), port=port_raw, host_names=tuple(names_raw)
            )
            worker = WorkerSettings(
                parallel=worker_raw.get("parallel", WorkerSettings.parallel),
                writer_timeout_seconds=worker_raw.get("writer_timeout_seconds", WorkerSettings.writer_timeout_seconds),
                render_timeout_seconds=worker_raw.get("render_timeout_seconds", WorkerSettings.render_timeout_seconds),
                max_check_rounds=worker_raw.get("max_check_rounds", WorkerSettings.max_check_rounds),
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"settings.json の値の型が不正です（{exc.__class__.__name__}）") from exc
        default_brand = raw.get("default_brand_id")
        if default_brand is not None and not isinstance(default_brand, str):
            raise ValidationError("default_brand_id は文字列か null")
        writer = raw.get("writer", "auto")
        if not isinstance(writer, str):
            raise ValidationError("writer は文字列（auto / claude / codex）")
        return cls(intake=intake, launch=launch, browser=browser, server=server, writer=writer, worker=worker,
                   default_brand_id=default_brand)


def _section(raw: dict, key: str) -> dict:
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError(f"settings.json の {key} はオブジェクト（または省略）")
    return value
