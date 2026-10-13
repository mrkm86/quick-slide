-- 0008_samples: 同梱の見本（docs/adr/0006-bundled-samples.md）。
-- - samples_seen: 一度入れた見本（id）。消されても起動のたびに入れ直さない印。デッキを完全に削除しても残す
-- - sample_decks: 見本から入れたデッキ → どの見本のどの版か・入れたときの台本の版と描いた回（触っていないかを見る）。
--   デッキを完全に削除すると行も消える（store/management.py の _PURGE_DELETES）
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
CREATE TABLE samples_seen (
 sample_id TEXT PRIMARY KEY CHECK(length(sample_id) BETWEEN 1 AND 40),
 first_seen_at TEXT NOT NULL
);
CREATE TABLE sample_decks (
 deck_id TEXT PRIMARY KEY REFERENCES decks(id),
 sample_id TEXT NOT NULL CHECK(length(sample_id) BETWEEN 1 AND 40),
 version TEXT NOT NULL CHECK(length(version)=64),
 script_id TEXT NOT NULL REFERENCES scripts(id),
 run_id TEXT NOT NULL REFERENCES runs(id),
 imported_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX sample_deck_by_sample ON sample_decks(sample_id);
