-- 0009_import: 既存の PPTX の取り込み（docs/adr/0011-import-pptx.md）。台本の版の書き手に「取り込み」（import）を足す。
-- 取り込んだ台本はエンジンの import が PPTX から起こしたもので、台本係・画面・会話のどれも書いていない。
-- 書き手は CHECK で縛っているので、表を作り直す。外部キーを切って作り直し、COMMIT の前に foreign_key_check を通す（db/migrate.py の約束）。
-- requires_foreign_keys_off
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
CREATE TABLE scripts_v9 (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
 version INTEGER NOT NULL CHECK(version>0),
 path TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 written_by TEXT NOT NULL CHECK(written_by IN ('writer','screen','conversation','import')),
 writer_backend TEXT CHECK(writer_backend IS NULL OR writer_backend IN ('claude','codex')),
 intake_id TEXT REFERENCES intakes(id),
 instruction TEXT,
 round INTEGER CHECK(round IS NULL OR round>0),
 check_rc INTEGER,
 check_notes_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(check_notes_json)),
 page_count INTEGER NOT NULL CHECK(page_count>=0),
 created_at TEXT NOT NULL,
 UNIQUE(deck_id,version)
);
INSERT INTO scripts_v9(id,deck_id,version,path,sha256,written_by,writer_backend,intake_id,instruction,round,check_rc,check_notes_json,page_count,created_at)
 SELECT id,deck_id,version,path,sha256,written_by,writer_backend,intake_id,instruction,round,check_rc,check_notes_json,page_count,created_at
 FROM scripts ORDER BY rowid;
DROP TABLE scripts;
ALTER TABLE scripts_v9 RENAME TO scripts;
CREATE INDEX script_deck ON scripts(deck_id,version);
