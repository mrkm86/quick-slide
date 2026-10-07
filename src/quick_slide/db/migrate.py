"""マイグレーション実行。

- 版は `PRAGMA user_version`（整数）。0 = 空 DB。`migrations/NNNN_*.sql` の NNNN が版番号
- 前進のみ。down マイグレーションは持たない。非空 DB を進める前に `db/backups/` へバックアップを取る
- 各マイグレーションは 1 つの `BEGIN IMMEDIATE` で「全文の実行 + `schema_migrations` への記録 +
  `user_version` の更新」を行う。途中で失敗したら丸ごと rollback され、版は進まない
- `schema_migrations(version, name, sha256, applied_at)` に適用済みファイルのハッシュを残し、
  適用済みファイルが後から改変されていたら起動を拒否する（StartupError）
- DB の `user_version` がコードの知る最新より大きければ、将来版の DB なので拒否する（StartupError）
- 冪等: 最新なら何もしない。並行起動は `BEGIN IMMEDIATE` で直列化される
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..clock import utcnow
from ..errors import StartupError

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
 version INTEGER PRIMARY KEY,
 name TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 applied_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    sha256: str

    @property
    def statements(self) -> list[str]:
        return split_statements(self.sql)


@dataclass(frozen=True)
class MigrationStatus:
    user_version: int
    latest_version: int
    applied: list[dict]
    pending: list[str]

    @property
    def up_to_date(self) -> bool:
        return self.user_version == self.latest_version and not self.pending


def split_statements(sql: str) -> list[str]:
    """`;` で機械的に切らず、`sqlite3.complete_statement` で文の完結を判定して分割する。
    コメント行（`--`）だけの塊は捨てる。"""
    statements: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stripped = _strip_comments(buf)
            if stripped:
                statements.append(stripped)
            buf = ""
    if _strip_comments(buf):
        raise StartupError("マイグレーション SQL の末尾に未完結の文があります")
    return statements


def _strip_comments(chunk: str) -> str:
    lines = [ln for ln in chunk.splitlines() if not ln.strip().startswith("--")]
    return "\n".join(lines).strip()


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        m = _NAME_RE.match(path.name)
        if not m:
            raise StartupError(f"マイグレーションファイル名が規約外です: {path.name}")
        sql = path.read_text(encoding="utf-8")
        found.append(
            Migration(
                version=int(m.group(1)),
                name=m.group(2),
                path=path,
                sql=sql,
                sha256=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    versions = [mg.version for mg in found]
    if versions != list(range(1, len(found) + 1)):
        raise StartupError(f"マイグレーションの版番号が 1 から連続していません: {versions}")
    return found


LATEST_VERSION = len(load_migrations())


def user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def status(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> MigrationStatus:
    migrations = load_migrations(directory)
    current = user_version(conn)
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    applied = (
        [dict(r) for r in conn.execute("SELECT version,name,sha256,applied_at FROM schema_migrations ORDER BY version")]
        if has_table
        else []
    )
    pending = [f"{m.version:04d}_{m.name}" for m in migrations if m.version > current]
    return MigrationStatus(user_version=current, latest_version=len(migrations), applied=applied, pending=pending)


def backup_database(db_path: Path, backup_dir: Path, from_version: int) -> Path:
    """バックアップ API で整合したコピーを取る（WAL 中の変更も含む）。鍵ファイルは含めない（DB だけ）。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().replace(":", "").replace("-", "").replace(".", "")
    target = backup_dir / f"quick-slide-v{from_version}-{stamp}.db"
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def migrate(
    conn: sqlite3.Connection,
    *,
    directory: Path = MIGRATIONS_DIR,
    db_path: Path | None = None,
    backup_dir: Path | None = None,
) -> MigrationStatus:
    """必要なマイグレーションを順に適用して最新にする。最新なら何もしない。"""
    migrations = load_migrations(directory)
    latest = len(migrations)

    # autocommit=True なのでステートメント単位で確定する。bootstrap は IF NOT EXISTS で冪等
    conn.executescript(_BOOTSTRAP_SQL)

    current = user_version(conn)
    if current > latest:
        raise StartupError(f"DB のスキーマ版 {current} はこのコードの最新 {latest} より新しい（将来版の DB）")

    _verify_applied_checksums(conn, migrations)

    if current == latest:
        return status(conn, directory)

    if current > 0 and db_path is not None and backup_dir is not None:
        backup_database(db_path, backup_dir, current)

    for mg in migrations:
        if mg.version <= current:
            continue
        _apply_one(conn, mg)
        current = mg.version
    return status(conn, directory)


def _apply_one(conn: sqlite3.Connection, mg: Migration) -> None:
    # SQLite はトランザクション中に foreign_keys を切り替えられない。CHECK を含む表を
    # 作り替える移行だけは、宣言マーカーで明示して開始前に無効化し、COMMIT 後に必ず戻す。
    requires_foreign_keys_off = "-- requires_foreign_keys_off" in mg.sql
    if requires_foreign_keys_off:
        conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        # 並行起動: 他プロセスが先に進めていたら二重適用しない
        if user_version(conn) >= mg.version:
            conn.execute("ROLLBACK")
            return
        for stmt in mg.statements:
            conn.execute(stmt)
        conn.execute(
            "INSERT INTO schema_migrations(version,name,sha256,applied_at) VALUES(?,?,?,?)",
            (mg.version, mg.name, mg.sha256, utcnow()),
        )
        conn.execute(f"PRAGMA user_version = {mg.version}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        if requires_foreign_keys_off:
            conn.execute("PRAGMA foreign_keys = ON")


def _verify_applied_checksums(conn: sqlite3.Connection, migrations: list[Migration]) -> None:
    by_version = {m.version: m for m in migrations}
    for row in conn.execute("SELECT version,name,sha256 FROM schema_migrations"):
        mg = by_version.get(row["version"])
        if mg is None:
            raise StartupError(f"DB に記録されたマイグレーション {row['version']} がコードに存在しません")
        if mg.sha256 != row["sha256"]:
            raise StartupError(
                f"適用済みマイグレーション {row['version']:04d}_{row['name']} がファイル側で改変されています"
            )
