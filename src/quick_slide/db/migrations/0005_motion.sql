-- 0005_motion: デッキのモードに「motion」（モーション）、描いた回のエンジンに「motion-slide」、
-- 書き出しの形式に「html」（HTML 1枚）を足す。
-- モード・エンジン・形式は CHECK で縛っているので、表を作り直す（decks・runs・exports。中身はそのまま写す）。
-- 外部キーを切って作り直し、COMMIT の前に foreign_key_check を通す（db/migrate.py の約束）。
-- requires_foreign_keys_off
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
CREATE TABLE decks_v5 (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, subtitle TEXT NOT NULL DEFAULT '',
 purpose TEXT NOT NULL DEFAULT '', audience TEXT NOT NULL DEFAULT '',
 mode TEXT NOT NULL CHECK(mode IN ('presentation','pitch','motion')),
 brand_revision_id TEXT NOT NULL REFERENCES brand_revisions(id),
 style_revision_id TEXT NOT NULL REFERENCES style_revisions(id),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','review','final')),
 pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)), archived_at TEXT, deleted_at TEXT,
 origin_deck_id TEXT REFERENCES decks(id),
 lock_version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
INSERT INTO decks_v5(id,title,subtitle,purpose,audience,mode,brand_revision_id,style_revision_id,status,pinned,archived_at,deleted_at,origin_deck_id,lock_version,created_at,updated_at)
 SELECT id,title,subtitle,purpose,audience,mode,brand_revision_id,style_revision_id,status,pinned,archived_at,deleted_at,origin_deck_id,lock_version,created_at,updated_at
 FROM decks ORDER BY rowid;
DROP TABLE decks;
ALTER TABLE decks_v5 RENAME TO decks;
CREATE INDEX deck_recent ON decks(updated_at DESC,id) WHERE deleted_at IS NULL AND archived_at IS NULL;
CREATE TABLE runs_v5 (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
 number INTEGER NOT NULL CHECK(number>0),
 script_id TEXT REFERENCES scripts(id),
 mode TEXT NOT NULL CHECK(mode IN ('presentation','pitch','motion')),
 engine TEXT NOT NULL CHECK(engine IN ('html-slide','image-slide','motion-slide')),
 args_json TEXT NOT NULL CHECK(json_valid(args_json)),
 state TEXT NOT NULL CHECK(state IN ('queued','writing','checking','rendering','importing','done','failed')),
 progress_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(progress_json)),
 engine_result_json TEXT CHECK(engine_result_json IS NULL OR json_valid(engine_result_json)),
 record_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(record_json)),
 rc INTEGER, message TEXT, failure_kind TEXT,
 out_dir TEXT,
 created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT, updated_at TEXT NOT NULL,
 CHECK(state NOT IN ('done','failed') OR ended_at IS NOT NULL),
 UNIQUE(deck_id,number)
);
INSERT INTO runs_v5(id,deck_id,number,script_id,mode,engine,args_json,state,progress_json,engine_result_json,record_json,rc,message,failure_kind,out_dir,created_at,started_at,ended_at,updated_at)
 SELECT id,deck_id,number,script_id,mode,engine,args_json,state,progress_json,engine_result_json,record_json,rc,message,failure_kind,out_dir,created_at,started_at,ended_at,updated_at
 FROM runs ORDER BY rowid;
DROP TABLE runs;
ALTER TABLE runs_v5 RENAME TO runs;
CREATE INDEX run_deck ON runs(deck_id,number);
CREATE TABLE exports_v5 (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
 run_id TEXT NOT NULL REFERENCES runs(id),
 format TEXT NOT NULL CHECK(format IN ('pptx','pdf','images','html')),
 path TEXT NOT NULL,
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 bytes INTEGER NOT NULL CHECK(bytes>=0),
 filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 255),
 created_at TEXT NOT NULL
);
INSERT INTO exports_v5(id,deck_id,run_id,format,path,sha256,bytes,filename,created_at)
 SELECT id,deck_id,run_id,format,path,sha256,bytes,filename,created_at FROM exports ORDER BY rowid;
DROP TABLE exports;
ALTER TABLE exports_v5 RENAME TO exports;
CREATE INDEX export_deck ON exports(deck_id,created_at);
