"""常駐の記録と生存確認（`runtime/server.json`）。

- `server.json`: PID・開始時刻・インスタンス ID・bind 先・URL・セッショントークン。モード 0600。
  同じ利用者のプロセス（CLI の `stop` など）だけが読み、状態を変える要求にセッショントークンを添える
- 二重起動の防止は 3 段: `runtime/server.lock` の flock（プロセスが死ねば自動解放）→ `status()` の生存確認 → bind 自体
- **`status()` は `server.json` があるだけでは「稼働中」と言わない**。PID が生きていて、かつ HTTP の health が
  返り、その `instance_id` が記録と一致したときだけ `running=True`。それ以外は理由つきで `running=False`
"""

from __future__ import annotations

import fcntl
import http.client
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..paths import DataRoot

RECORD_VERSION = 1


@dataclass(frozen=True)
class ServerRecord:
    instance_id: str
    pid: int
    started_at: str
    host: str
    port: int
    url: str
    session_token: str
    version: str
    record_version: int = RECORD_VERSION

    def public(self) -> dict[str, Any]:
        """stdout / 応答に出してよい部分（セッショントークンを含めない）。"""
        d = asdict(self)
        d.pop("session_token")
        return d

    @classmethod
    def from_json(cls, raw: Any) -> "ServerRecord":
        if not isinstance(raw, dict):
            raise ValueError("server.json はオブジェクトではない")
        try:
            return cls(
                instance_id=str(raw["instance_id"]),
                pid=int(raw["pid"]),
                started_at=str(raw["started_at"]),
                host=str(raw["host"]),
                port=int(raw["port"]),
                url=str(raw["url"]),
                session_token=str(raw["session_token"]),
                version=str(raw.get("version", "")),
                record_version=int(raw.get("record_version", RECORD_VERSION)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"server.json の形が不正（{exc.__class__.__name__}）") from exc


def write_record(root: DataRoot, record: ServerRecord) -> Path:
    root.runtime_dir.mkdir(parents=True, exist_ok=True)
    path = root.server_json_path
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, json.dumps(asdict(record), ensure_ascii=False, indent=1).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def read_record(root: DataRoot) -> ServerRecord | None:
    """無ければ None。壊れていれば ValueError（呼び出し側が「無い」と区別する）。"""
    path = root.server_json_path
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"server.json を読めない（{exc.__class__.__name__}）") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("server.json が JSON として読めない") from exc
    return ServerRecord.from_json(raw)


def remove_record(root: DataRoot, instance_id: str) -> bool:
    """自分のインスタンスの記録だけ消す（後から起動した別インスタンスの記録を消さない）。"""
    try:
        current = read_record(root)
    except ValueError:
        current = None
    if current is not None and current.instance_id != instance_id:
        return False
    try:
        root.server_json_path.unlink()
        return True
    except FileNotFoundError:
        return False


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def http_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def probe_health(host: str, port: int, *, timeout: float = 2.0) -> dict[str, Any] | None:
    """実際に GET /api/health を打つ。応答が JSON オブジェクトでなければ None（届かなかった＝生きていない証拠は無い）。"""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/api/health", headers={"Host": f"{http_host(host)}:{port}"})
        resp = conn.getresponse()
        body = resp.read()
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    data["_http_status"] = resp.status
    return data


def status(root: DataRoot, *, timeout: float = 2.0) -> dict[str, Any]:
    """生存の証拠つきの状態。`running=True` は「PID 生存 かつ health 応答 かつ instance_id 一致」のときだけ。"""
    try:
        record = read_record(root)
    except ValueError as exc:
        return {"running": False, "reason": "server_json_corrupt", "detail": str(exc), "server_json": str(root.server_json_path)}
    if record is None:
        return {"running": False, "reason": "no_server_json", "server_json": str(root.server_json_path)}
    public = record.public()
    if not pid_alive(record.pid):
        return {"running": False, "reason": "stale_pid", "record": public}
    health = probe_health(record.host, record.port, timeout=timeout)
    if health is None:
        return {"running": False, "reason": "unreachable", "record": public}
    if health.get("instance_id") != record.instance_id:
        return {"running": False, "reason": "instance_mismatch", "record": public,
                "responding_instance_id": health.get("instance_id")}
    return {"running": True, "pid": record.pid, "url": record.url, "instance_id": record.instance_id,
            "started_at": record.started_at, "host": record.host, "port": record.port, "health": health}


class InstanceLock:
    """`runtime/server.lock` の排他 flock。プロセスが終われば OS が解放する。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None
