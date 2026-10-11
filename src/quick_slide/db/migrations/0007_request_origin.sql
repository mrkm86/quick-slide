-- 0007_request_origin: 依頼から作る取り込みの出所の名前を「screen」から「request」にする（docs/adr/0002-create-from-screen.md）。
-- 画面からも会話（CLI）からも同じ `POST /api/intakes` に `request` を送るので、出所は「どの画面か」ではなく
-- 「依頼から作る道」を表す。違いは origin_runtime（browser / claude_code / codex …）だけ。
-- 0006 で入れた screen の行は request に写す。出所は CHECK で縛っているので、表を作り直す。
-- 外部キーを切って作り直し、COMMIT の前に foreign_key_check を通す（db/migrate.py の約束）。
-- requires_foreign_keys_off
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
CREATE TABLE intakes_v7 (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
 origin_kind TEXT NOT NULL CHECK(origin_kind IN ('conversation','paste','file','headless','request')),
 origin_runtime TEXT NOT NULL CHECK(origin_runtime IN ('claude_code','codex','browser','cli','other')),
 conversation_hash TEXT CHECK(conversation_hash IS NULL OR length(conversation_hash)=64),
 request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
 title_hint TEXT, manuscript TEXT NOT NULL,
 request TEXT,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 launch_id TEXT REFERENCES launches(id),
 created_at TEXT NOT NULL
);
INSERT INTO intakes_v7(id,deck_id,origin_kind,origin_runtime,conversation_hash,request_sha256,title_hint,manuscript,request,payload_json,launch_id,created_at)
 SELECT id,deck_id,CASE origin_kind WHEN 'screen' THEN 'request' ELSE origin_kind END,origin_runtime,conversation_hash,request_sha256,title_hint,manuscript,request,payload_json,launch_id,created_at
 FROM intakes ORDER BY rowid;
DROP TABLE intakes;
ALTER TABLE intakes_v7 RENAME TO intakes;
CREATE INDEX intake_deck ON intakes(deck_id,created_at);
CREATE INDEX intake_conversation ON intakes(conversation_hash,request_sha256,created_at) WHERE conversation_hash IS NOT NULL;
