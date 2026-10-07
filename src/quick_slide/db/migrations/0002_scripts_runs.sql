-- 0002_scripts_runs: P2（会話から1冊）。
-- 取り込み（intakes）、台本の版（scripts と、そのページ script_pages）、台本のページの同一性（page_ids。
-- 台本の各ページの `id` に結ぶ）、描いた回（runs。再現の記録つき）とページごとの結果（run_pages）、
-- ワーカーの仕事の台帳（jobs。段ごとに書くので、サーバが止まっても続きから戻せる）。
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
CREATE TABLE intakes (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
 origin_kind TEXT NOT NULL CHECK(origin_kind IN ('conversation','paste','file','headless')),
 origin_runtime TEXT NOT NULL CHECK(origin_runtime IN ('claude_code','codex','browser','cli','other')),
 conversation_hash TEXT CHECK(conversation_hash IS NULL OR length(conversation_hash)=64),
 request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
 title_hint TEXT, manuscript TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 launch_id TEXT REFERENCES launches(id),
 created_at TEXT NOT NULL
);
CREATE TABLE page_ids (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
 key TEXT NOT NULL CHECK(length(key) BETWEEN 1 AND 40),
 created_at TEXT NOT NULL,
 UNIQUE(deck_id,key)
);
CREATE TABLE scripts (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
 version INTEGER NOT NULL CHECK(version>0),
 path TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 written_by TEXT NOT NULL CHECK(written_by IN ('writer','screen','conversation')),
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
CREATE TABLE script_pages (
 script_id TEXT NOT NULL REFERENCES scripts(id),
 page_no INTEGER NOT NULL CHECK(page_no>0),
 page_id TEXT NOT NULL REFERENCES page_ids(id),
 type TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(script_id,page_no), UNIQUE(script_id,page_id)
);
CREATE TABLE runs (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
 number INTEGER NOT NULL CHECK(number>0),
 script_id TEXT REFERENCES scripts(id),
 mode TEXT NOT NULL CHECK(mode IN ('presentation','pitch')),
 engine TEXT NOT NULL CHECK(engine IN ('html-slide','image-slide')),
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
CREATE TABLE run_pages (
 run_id TEXT NOT NULL REFERENCES runs(id),
 page_no INTEGER NOT NULL CHECK(page_no>0),
 page_id TEXT REFERENCES page_ids(id),
 asset_id TEXT REFERENCES assets(id),
 state TEXT NOT NULL CHECK(state IN ('pending','drawn','ready','failed')),
 warnings_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(warnings_json)),
 PRIMARY KEY(run_id,page_no),
 CHECK(state!='ready' OR asset_id IS NOT NULL)
);
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
 kind TEXT NOT NULL CHECK(kind IN ('make')),
 run_id TEXT NOT NULL REFERENCES runs(id),
 intake_id TEXT REFERENCES intakes(id),
 state TEXT NOT NULL CHECK(state IN ('queued','running','done','failed')),
 resume_from TEXT CHECK(resume_from IS NULL OR resume_from IN ('render','import')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 worker TEXT,
 error_class TEXT,
 created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT,
 CHECK(state NOT IN ('done','failed') OR ended_at IS NOT NULL)
);
CREATE INDEX intake_deck ON intakes(deck_id,created_at);
CREATE INDEX intake_conversation ON intakes(conversation_hash,request_sha256,created_at) WHERE conversation_hash IS NOT NULL;
CREATE INDEX script_deck ON scripts(deck_id,version);
CREATE INDEX run_deck ON runs(deck_id,number);
CREATE INDEX run_page_asset ON run_pages(asset_id) WHERE asset_id IS NOT NULL;
CREATE INDEX job_open ON jobs(state,created_at) WHERE state IN ('queued','running');
CREATE INDEX job_deck ON jobs(deck_id,state);
