"""P3: ピッチの複製を本物のエンジンで検証する。"""

import unittest

import p3_edit_helpers as edit_helpers


class PitchDuplicateTests(edit_helpers.EditBase):
    def test_duplicate_that_fails_midway_leaves_nothing(self) -> None:
        deck = self.make_deck()
        store = self.fx.store
        assets = self.fx.root.assets_dir / "decks"
        before = {p.name for p in assets.iterdir()}
        decks_before = {p.name for p in self.fx.root.decks_dir.iterdir()}
        real, calls = store.register_asset, []

        def broken(**kw):
            calls.append(1)
            if len(calls) == 3:
                raise OSError("容量不足のつもり")
            return real(**kw)

        store.register_asset = broken
        try:
            with self.fx.server.store_lock, self.assertRaises(OSError):
                store.duplicate_deck(deck)
        finally:
            store.register_asset = real
        self.assertEqual({p.name for p in assets.iterdir()}, before, "写しかけの絵が残った")
        self.assertEqual({p.name for p in self.fx.root.decks_dir.iterdir()}, decks_before, "写しかけのデッキの置き場が残った")
        with self.fx.server.store_lock:
            self.assertEqual([d for d in store.recent_decks(50) if d.get("origin_deck_id") == deck], [])

    def test_duplicate_copies_pages_and_rebuild_draws_nothing(self) -> None:
        deck = self.make_deck()
        self.instruct(deck, [3], "見出しを変えて")
        self.assertEqual(self.wait(deck)["state"], "done")
        self.duplicate_and_rebuild(deck)


if __name__ == "__main__":
    unittest.main()
