-- 0013_deck_template_snapshot: デッキがデザインテンプレートの控え（template_snapshots）を指す（docs/design-template-generation.md §2.2）。
-- 描き終えた回が無いデッキ（準備で止まった資料・その複製）でも、デッキが控えを失わないように、デッキ自体に持つ。
-- テンプレートを使わないデッキは NULL のまま。
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
ALTER TABLE decks ADD COLUMN template_snapshot_id TEXT REFERENCES template_snapshots(id);
