-- 0012_template_snapshots: デザインテンプレートで作る資料の控え（docs/design-template-generation.md §2.2）。
-- 受付のとき、テンプレートの文書全文・内部改訂 ID・内容ハッシュと、ブランドの改訂（確定 config・資産目録）を 1 行にまとめて残す。
-- 元のテンプレート・ブランドの履歴が無くても、この行だけから復元できる。行は不変で、同じ内容（snapshot_sha256）は 1 行を共有する。
-- 描いた回は runs.args の template_snapshot（{snapshot_id, snapshot_sha256}）でこの行を指す（全文は複製しない）。
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
CREATE TABLE template_snapshots (
 id TEXT PRIMARY KEY,
 snapshot_sha256 TEXT NOT NULL UNIQUE CHECK(length(snapshot_sha256)=64),
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),
 created_at TEXT NOT NULL
);
CREATE TRIGGER template_snapshots_no_update BEFORE UPDATE ON template_snapshots
BEGIN
 SELECT RAISE(ABORT, 'template_snapshots は不変');
END;
