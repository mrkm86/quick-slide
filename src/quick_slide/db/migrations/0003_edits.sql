-- 0003_edits: P3（直す）。
-- - 仕事（jobs）に「直す」（edit）を足す。何をするか（ページを指定した直し・差し込み・複製・非表示・並べ替え・
--   版から戻す・描き直し）は request_json に書く（原稿は入れない。利用者の指示文は入る）
-- - デッキのいまの台本（deck_heads）。画面のページと、次の直しの元はこの版
-- - 台本のページ（script_pages）に、中身の sha256 と非表示の印。描いた回のページ（run_pages）に、
--   そのときの中身の sha256 と絵の sha256（版スタックで同じ姿をまとめる鍵）
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
CREATE TABLE jobs_v3 (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
 kind TEXT NOT NULL CHECK(kind IN ('make','edit')),
 run_id TEXT NOT NULL REFERENCES runs(id),
 intake_id TEXT REFERENCES intakes(id),
 request_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(request_json)),
 state TEXT NOT NULL CHECK(state IN ('queued','running','done','failed')),
 resume_from TEXT CHECK(resume_from IS NULL OR resume_from IN ('render','import')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 worker TEXT,
 error_class TEXT,
 created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT,
 CHECK(state NOT IN ('done','failed') OR ended_at IS NOT NULL)
);
INSERT INTO jobs_v3(id,deck_id,kind,run_id,intake_id,state,resume_from,attempts,worker,error_class,created_at,started_at,ended_at)
 SELECT id,deck_id,kind,run_id,intake_id,state,resume_from,attempts,worker,error_class,created_at,started_at,ended_at
 FROM jobs ORDER BY rowid;
DROP TABLE jobs;
ALTER TABLE jobs_v3 RENAME TO jobs;
CREATE INDEX job_open ON jobs(state,created_at) WHERE state IN ('queued','running');
CREATE INDEX job_deck ON jobs(deck_id,state);
CREATE TABLE deck_heads (
 deck_id TEXT PRIMARY KEY REFERENCES decks(id),
 script_id TEXT NOT NULL REFERENCES scripts(id),
 updated_at TEXT NOT NULL
);
ALTER TABLE script_pages ADD COLUMN content_sha256 TEXT;
ALTER TABLE script_pages ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0,1));
ALTER TABLE run_pages ADD COLUMN content_sha256 TEXT;
ALTER TABLE run_pages ADD COLUMN picture_sha256 TEXT;
-- P2 で描き終えたデッキは、一番新しい描き終えた回の台本をいまの台本にする
INSERT INTO deck_heads(deck_id,script_id,updated_at)
 SELECT r.deck_id, r.script_id, r.updated_at FROM runs r
 WHERE r.state='done' AND r.script_id IS NOT NULL
   AND r.number=(SELECT MAX(r2.number) FROM runs r2 WHERE r2.deck_id=r.deck_id AND r2.state='done' AND r2.script_id IS NOT NULL);
