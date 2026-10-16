-- 0013_deck_template_snapshot: デッキがデザインテンプレートの控え（template_snapshots）を指す（docs/design-template-generation.md §2.2）。
-- 描き終えた回が無いデッキ（準備で止まった資料・その複製）でも、デッキが控えを失わないように、デッキ自体に持つ。
-- テンプレートを使わないデッキは NULL のまま。
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
ALTER TABLE decks ADD COLUMN template_snapshot_id TEXT REFERENCES template_snapshots(id);
-- 0013 より前に受け付けたテンプレートのデッキ: 一番古い回の引数（template_snapshot）が指す控えを、デッキへ写す（控えが在るものだけ）
UPDATE decks SET template_snapshot_id=(
 SELECT s.id FROM runs r JOIN template_snapshots s ON s.id=json_extract(r.args_json,'$.template_snapshot.snapshot_id')
 WHERE r.deck_id=decks.id ORDER BY r.number LIMIT 1)
 WHERE template_snapshot_id IS NULL;
