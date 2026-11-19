-- 0014_deck_brandless: デッキが「ブランドなし」を自分で持つ（docs/adr/0035-brandless-decks.md）。
-- 取り込んだ PPTX のデッキは、ロゴ・フッタ・ページ番号・透かしを足さず、色は元の PPTX のテーマを使う。
-- これまでは描いた回の引数（imported）で見ていたが、直しの回には渡らず、直すとブランドのロゴが付いた。
-- brand_revision_id は NOT NULL のまま残す（ブランドなしのデッキでは使わない。表の作り直しを避ける）。
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
ALTER TABLE decks ADD COLUMN brandless INTEGER NOT NULL DEFAULT 0 CHECK (brandless IN (0,1));
-- 0014 より前に取り込んだデッキとその複製を、ブランドなしにする。手掛かりは 3 つ:
-- 取り込みの回（引数に imported）を持つ・台本に取り込んだページ（型 html。エンジンの import だけが書く）を持つ・
-- そこから複製をたどれる（直したあとの複製は、写した回に imported が無い。複製元を完全に消した複製は、複製元を指していない）
WITH RECURSIVE took(id) AS (
 SELECT r.deck_id FROM runs r WHERE json_extract(r.args_json,'$.imported')=1
 UNION
 SELECT s.deck_id FROM scripts s JOIN script_pages sp ON sp.script_id=s.id JOIN decks d ON d.id=s.deck_id
  WHERE sp.type='html' AND d.mode='presentation'
 UNION
 SELECT d.id FROM decks d JOIN took t ON d.origin_deck_id=t.id
)
UPDATE decks SET brandless=1 WHERE id IN (SELECT id FROM took);
