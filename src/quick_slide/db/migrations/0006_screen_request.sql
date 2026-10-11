-- 0006_screen_request: 画面の入力欄から 1 冊作る（docs/adr/0002-create-from-screen.md）。
-- 取り込みの出所に「screen」（画面）を足し、依頼の文（request）の列を足す。依頼から作る取り込みは、下調べ係が原稿を
-- 起こすまで manuscript が空で、起こした原稿をそこへ書く（台本係は manuscript だけを読む。いままでと同じ）。
-- 出所は CHECK で縛っているので、表を作り直す（中身はそのまま写し、request は NULL）。
-- 外部キーを切って作り直し、COMMIT の前に foreign_key_check を通す（db/migrate.py の約束）。
-- requires_foreign_keys_off
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
CREATE TABLE intakes_v6 (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
 origin_kind TEXT NOT NULL CHECK(origin_kind IN ('conversation','paste','file','headless','screen')),
 origin_runtime TEXT NOT NULL CHECK(origin_runtime IN ('claude_code','codex','browser','cli','other')),
 conversation_hash TEXT CHECK(conversation_hash IS NULL OR length(conversation_hash)=64),
 request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
 title_hint TEXT, manuscript TEXT NOT NULL,
 request TEXT,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 launch_id TEXT REFERENCES launches(id),
 created_at TEXT NOT NULL
);
INSERT INTO intakes_v6(id,deck_id,origin_kind,origin_runtime,conversation_hash,request_sha256,title_hint,manuscript,request,payload_json,launch_id,created_at)
 SELECT id,deck_id,origin_kind,origin_runtime,conversation_hash,request_sha256,title_hint,manuscript,NULL,payload_json,launch_id,created_at
 FROM intakes ORDER BY rowid;
DROP TABLE intakes;
ALTER TABLE intakes_v6 RENAME TO intakes;
CREATE INDEX intake_deck ON intakes(deck_id,created_at);
CREATE INDEX intake_conversation ON intakes(conversation_hash,request_sha256,created_at) WHERE conversation_hash IS NOT NULL;
