-- 0010_page_previews: 非表示のページの下見の絵（docs/adr/0012-hidden-page-previews.md）。
-- 非表示のページは描く回のページ（run_pages）に入らない（発表・書き出し・枚数に入らない）。中身は見たいので、
-- 描く回の最後に非表示のページだけを別に描き、その絵をここに持つ。画面は非表示のページの絵として薄く出す。
-- 中身の sha256 がいまのページと合うときだけ使う（中身を直したら古い絵は出さない）。1 ページに 1 枚（描き直したら差し替える）。
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
CREATE TABLE page_previews (
 deck_id TEXT NOT NULL REFERENCES decks(id),
 page_id TEXT NOT NULL REFERENCES page_ids(id),
 asset_id TEXT NOT NULL REFERENCES assets(id),
 run_id TEXT NOT NULL REFERENCES runs(id),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 created_at TEXT NOT NULL,
 PRIMARY KEY(deck_id,page_id)
);
CREATE INDEX page_preview_asset ON page_previews(asset_id);
