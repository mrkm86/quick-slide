-- 0014_deck_brandless: デッキが「ブランドなし」を自分で持つ（docs/adr/0035-brandless-decks.md）。
-- 取り込んだ PPTX のデッキは、ロゴ・フッタ・ページ番号・透かしを足さず、色は元の PPTX のテーマを使う。
-- これまでは描いた回の引数（imported）で見ていたが、直しの回には渡らず、直すとブランドのロゴが付いた。
-- brand_revision_id は NOT NULL のまま残す（ブランドなしのデッキでは使わない。表の作り直しを避ける）。
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
ALTER TABLE decks ADD COLUMN brandless INTEGER NOT NULL DEFAULT 0 CHECK (brandless IN (0,1));
-- 0014 より前に取り込んだデッキとその複製: 取り込みの回（引数に imported）を持つデッキと、そこから複製をたどれるデッキを、
-- ブランドなしにする（直したあとの複製は、写した回に imported が無いので、複製元からたどる）
WITH RECURSIVE took(id) AS (
 SELECT r.deck_id FROM runs r WHERE json_extract(r.args_json,'$.imported')=1
 UNION
 SELECT d.id FROM decks d JOIN took t ON d.origin_deck_id=t.id
)
UPDATE decks SET brandless=1 WHERE id IN (SELECT id FROM took);
