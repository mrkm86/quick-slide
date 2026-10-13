"""ブランドのエクスポート・インポート（ZIP）。形の単体（brand_package）と、API の通し（エクスポート → 確かめる → 上書き／追加）。

他人から受け取った ZIP として読むので、壊れた・知らないファイル入り・中身が膨らむ・画像が書き換えられた ZIP は断る。
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import unittest
import zipfile
from urllib.parse import unquote

from PIL import Image

from helpers import start_server

from quick_slide import brand_package as P

BRAND_ID = "11111111-2222-4333-8444-555555555555"


def png(size=(600, 100)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, (30, 60, 90, 255)).save(out, format="PNG")
    return out.getvalue()


def config() -> dict:
    return {"schema_version": 1, "colors": {"primary": "#F5A623", "accent": "#399DD1", "ink": "#1A1A1A"},
            "logo": {"variant": "transparent", "placement": {"corner": "bottom-left"}},
            "footer": {"text": "© 2026 見本", "style": {"position": "footer_left"}},
            "format": {"page_numbers": True, "watermark": "社外秘"}}


def package(**over) -> bytes:
    kw = {"brand_id": BRAND_ID, "name": "見本ブランド", "config": config(), "exported_at": "2026-10-12T10:00:00Z",
          "assets": [{"role": "logo_transparent", "label": "ロゴ", "media_type": "image/png", "data": png()}]}
    kw.update(over)
    return P.build(**kw)


def rezip(blob: bytes, change) -> bytes:
    """ZIP の中身を change(name, data) → (name, data) | None で書き換えた ZIP。"""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as zin, zipfile.ZipFile(out, "w") as zout:
        for name in zin.namelist():
            got = change(name, zin.read(name))
            if got:
                zout.writestr(got[0], got[1])
    return out.getvalue()


class PackageFormatTests(unittest.TestCase):
    def test_round_trip_keeps_the_settings_and_the_logo(self):
        got = P.read(package())
        self.assertEqual((got["id"], got["name"], got["config"]), (BRAND_ID, "見本ブランド", config()))
        self.assertEqual([(a["role"], a["media_type"], a["data"]) for a in got["assets"]], [("logo_transparent", "image/png", png())])

    def test_only_logo_roles_and_known_keys_are_written(self):
        blob = package(config={**config(), "scope": "x"},
                       assets=[{"role": "optional", "label": "差し絵", "media_type": "image/png", "data": png()}])
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            self.assertEqual(z.namelist(), ["brand.json"], "ロゴでない画像は入れない")
            self.assertNotIn("scope", json.loads(z.read("brand.json"))["config"])

    def test_untrusted_zips_are_refused(self):
        good = package()
        cases = {
            "ZIP でない": b"hello",
            "上の階層を指す名前": rezip(good, lambda n, d: ("../" + n, d) if n.startswith("assets/") else (n, d)),
            "brand.json が無い": rezip(good, lambda n, d: None if n == "brand.json" else (n, d)),
            "画像の書き換え": rezip(good, lambda n, d: (n, d[:-4] + b"XXXX") if n.startswith("assets/") else (n, d)),
            "形式の版が違う": rezip(good, lambda n, d: (n, json.dumps({**json.loads(d), "version": 2}).encode()) if n == "brand.json" else (n, d)),
            "ID の形が違う": rezip(good, lambda n, d: (n, json.dumps({**json.loads(d), "id": "../x"}).encode()) if n == "brand.json" else (n, d)),
            "知らない設定のキー": rezip(good, lambda n, d: (n, json.dumps({**json.loads(d), "config": {**config(), "x": 1}}).encode()) if n == "brand.json" else (n, d)),
        }
        # 一覧に無いファイルを足した ZIP
        extra = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(good)) as zin, zipfile.ZipFile(extra, "w") as zout:
            for n in zin.namelist():
                zout.writestr(n, zin.read(n))
            zout.writestr("assets/logo_white.png", png())
        cases["一覧に無い画像"] = extra.getvalue()
        for label, blob in cases.items():
            with self.subTest(label), self.assertRaises(P.PackageError):
                P.read(blob)

    def test_the_file_name_says_it_is_a_quick_slide_brand_and_is_not_checked_on_read(self):
        self.assertEqual(P.filename("見本ブランド"), "qs-ブランド-見本ブランド.zip")
        self.assertEqual(P.filename('A/B:C*?"<>|\\D\n'), "qs-ブランド-A_B_C_______D_.zip")
        self.assertEqual(P.filename("  "), "qs-ブランド-名称未設定.zip")
        # 受け渡しで改名されても読める（種類を見分けるのは中の brand.json）。read はファイル名を受け取らない
        self.assertEqual(P.read(package())["name"], "見本ブランド")

    def test_a_zip_that_swells_when_opened_is_refused(self):
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("brand.json", b" " * (P.MAX_MANIFEST_BYTES - 1))
        with self.assertRaises(P.PackageError):
            P.read(bomb.getvalue())


class ExportImportApiTests(unittest.TestCase):
    def setUp(self):
        self.fx = start_server(self)
        self.catalog = self.fx.get("/api/settings").json()

    def make_brand(self) -> dict:
        r = self.fx.request("POST", "/api/settings/images", raw_body=png(), session=True)
        self.assertEqual(r.status, 201, r.body)
        c = copy.deepcopy(self.catalog["brand_default"])
        c.update({k: v for k, v in config().items() if k != "schema_version"})
        b = {"name": "見本ブランド", "config": c, "assets": [{"asset_id": r.json()["asset_id"], "role": "logo_transparent", "label": "ロゴ"}]}
        r = self.fx.post("/api/settings/brands", b)
        self.assertEqual(r.status, 201, r.body)
        return r.json()["item"]

    def send(self, path: str, blob: bytes):
        return self.fx.request("POST", path, raw_body=blob, session=True)

    def test_export_check_replace_and_add(self):
        item = self.make_brand()
        r = self.fx.get(f"/api/settings/brands/{item['id']}/export")
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(r.headers.get("content-type") or r.headers.get("Content-Type"), "application/zip")
        cd = r.headers.get("content-disposition") or r.headers.get("Content-Disposition")
        self.assertEqual(unquote(cd.split("filename*=UTF-8''")[1]), "qs-ブランド-見本ブランド.zip")
        blob = r.body
        got = P.read(blob)
        self.assertEqual(got["id"], item["id"])
        self.assertEqual(got["config"]["colors"]["ink"], "#1A1A1A")

        check = self.send("/api/settings/brands/import/check", blob)
        self.assertEqual(check.status, 200, check.body)
        body = check.json()
        self.assertEqual(body["choices"], ["replace", "add"])
        self.assertEqual(body["existing"]["name"], "見本ブランド")
        self.assertEqual(body["package"]["logos"], ["logo_transparent"])
        self.assertEqual(len(self.fx.get("/api/settings").json()["brands"]), 2, "確かめるだけでは何も保存しない")

        rev = body["existing"]["current_revision_id"]
        r = self.send(f"/api/settings/brands/import?mode=replace&expected_revision_id={rev}", blob)
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual((r.json()["item"]["id"], r.json()["item"]["revision"]), (item["id"], 2), "上書きは新しい改訂")
        # 古い改訂を前提にした上書きは断る
        self.assertEqual(self.send(f"/api/settings/brands/import?mode=replace&expected_revision_id={rev}", blob).error_code,
                         "setting_changed")
        r = self.send("/api/settings/brands/import?mode=add", blob)
        self.assertEqual(r.status, 200, r.body)
        added = r.json()["item"]
        self.assertNotEqual(added["id"], item["id"])
        self.assertEqual(added["name"], "見本ブランド（インポート）")
        self.assertEqual([a["role"] for a in added["assets"]], ["logo_transparent"])
        image = self.fx.get(f"/api/assets/{added['assets'][0]['asset_id']}/file")
        self.assertEqual(hashlib.sha256(image.body).hexdigest(), hashlib.sha256(got["assets"][0]["data"]).hexdigest())

    def test_on_another_machine_the_same_id_is_kept(self):
        blob = package()
        check = self.send("/api/settings/brands/import/check", blob).json()
        self.assertEqual((check["choices"], check["existing"]), (["create"], None))
        r = self.send("/api/settings/brands/import?mode=add", blob)
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual((r.json()["item"]["id"], r.json()["item"]["name"]), (BRAND_ID, "見本ブランド"))
        self.assertEqual(self.send("/api/settings/brands/import/check", blob).json()["choices"], ["replace", "add"],
                         "次に同じ ZIP の新しい版を読めば上書きを選べる")
        self.assertEqual(self.send("/api/settings/brands/import?mode=replace", blob).error_code, "setting_changed",
                         "上書きは改訂の確かめが要る")

    def test_preset_brands_are_not_exported_and_bad_zips_are_refused_in_japanese(self):
        preset = self.catalog["brands"][0]
        self.assertEqual(self.fx.get(f"/api/settings/brands/{preset['id']}/export").error_code, "preset_not_exportable")
        r = self.send("/api/settings/brands/import/check", b"not a zip")
        self.assertEqual(r.error_code, "package_invalid")
        self.assertTrue(any(ord(c) > 0x2e80 for c in r.json()["error"]["message"]))
        self.assertEqual(self.send("/api/settings/brands/import/check", b"").error_code, "package_required")
        broken = package(config={**config(), "colors": {"primary": "red", "accent": "#399DD1"}})
        self.assertEqual(self.send("/api/settings/brands/import/check", broken).error_code, "invalid_setting")
        self.assertEqual(self.send("/api/settings/brands/import?mode=bogus", package()).error_code, "import_choice_changed")
        self.assertEqual(self.fx.request("POST", "/api/settings/brands/import/check", raw_body=package()).status, 403,
                         "インポートはセッションのトークンが要る")


if __name__ == "__main__":
    unittest.main()
