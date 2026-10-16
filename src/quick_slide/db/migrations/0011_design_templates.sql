-- 0011_design_templates: デザインテンプレートの保存（docs/design-template-contract.md・docs/design-template-implementation.md §3）。
-- 利用者が見る版の番号（revision: v1・v2）と、保存のたびに増える不変の改訂の行（design_template_revisions.id）を分ける。
-- 「いまの版を上書き」は、同じ revision の新しい行を足す操作（UPDATE しない）。同じ版の行は save_sequence で順に並ぶ。
-- 削除は deleted_at（論理削除）。改訂の行は残す。
-- 既存のブランド・スタイル・デッキにテンプレートは作らない（旧デッキとの関連は空のまま）。
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
CREATE TABLE design_templates (
 id TEXT PRIMARY KEY,
 brand_id TEXT NOT NULL REFERENCES brands(id),
 current_revision_id TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
 FOREIGN KEY(id,current_revision_id) REFERENCES design_template_revisions(template_id,id)
 DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE design_template_revisions (
 id TEXT PRIMARY KEY,
 template_id TEXT NOT NULL REFERENCES design_templates(id),
 revision INTEGER NOT NULL CHECK(revision>=1),
 save_sequence INTEGER NOT NULL CHECK(save_sequence>=1),
 document_json TEXT NOT NULL CHECK(json_valid(document_json)),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 based_on_revision_id TEXT,
 brand_revision_id TEXT NOT NULL REFERENCES brand_revisions(id),
 origin_runtime TEXT CHECK(origin_runtime IS NULL OR origin_runtime IN ('claude_code','codex','browser','cli','other')),
 conversation_hash TEXT CHECK(conversation_hash IS NULL OR length(conversation_hash)=64),
 save_kind TEXT NOT NULL CHECK(save_kind IN ('create','overwrite','new_version','copy','duplicate')),
 created_at TEXT NOT NULL,
 UNIQUE(template_id,save_sequence),
 UNIQUE(template_id,id),
 FOREIGN KEY(template_id,based_on_revision_id) REFERENCES design_template_revisions(template_id,id)
);
CREATE INDEX design_template_revisions_by_version ON design_template_revisions(template_id,revision,save_sequence);
CREATE INDEX design_templates_by_brand ON design_templates(brand_id,deleted_at);
CREATE TRIGGER design_template_revisions_no_update BEFORE UPDATE ON design_template_revisions
BEGIN
 SELECT RAISE(ABORT, 'design_template_revisions は不変');
END;
CREATE TRIGGER design_template_revisions_no_delete BEFORE DELETE ON design_template_revisions
BEGIN
 SELECT RAISE(ABORT, 'design_template_revisions は不変');
END;
