-- 0004_exports: P5（書き出しの配布）。
-- 書き出し（exports）: どの描いた回の、どの形式のファイルを、どの中身（sha256）で配ったか。
-- ファイルは描いた回の控え（decks/<デッキ>/runs/<回>/deck.pptx など）をそのまま指す（写さない）。
-- 配るときに sha256 を照らすので、同じ書き出しの URL はいつも同じバイトを返す（違えば配らない）。
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
CREATE TABLE exports (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
 run_id TEXT NOT NULL REFERENCES runs(id),
 format TEXT NOT NULL CHECK(format IN ('pptx','pdf','images')),
 path TEXT NOT NULL,
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 bytes INTEGER NOT NULL CHECK(bytes>=0),
 filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 255),
 created_at TEXT NOT NULL
);
CREATE INDEX export_deck ON exports(deck_id,created_at);
