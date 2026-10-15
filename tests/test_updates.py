"""新しいリリースのお知らせ（docs/adr/0020-release-and-update-notice.md）。GitHub は叩かない（`fetch` を差す）。

- 版の比べ方（SemVer。プレリリースは正式版より古い）とタグの読み方
- 覚える長さ・失敗したときの答え（前の答えを stale で返す・無ければ黙る）・「今すぐ確かめる」の間隔
- GitHub の答えの読み方（リンクは公開リポジトリのリリースの頁だけ・本文の長さ）
- API: `GET /api/update`・`POST /api/settings/updates`、設定と環境変数で止まること
"""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from helpers import start_server, temp_dir

from quick_slide import updates as U
from quick_slide.settings import Settings, save_updates_section


def release(tag: str = "v2.1.0", **extra) -> bytes:
    body = {"tag_name": tag, "name": f"Quick Slide {tag.lstrip('v')}", "html_url": f"{U.RELEASES_PAGE}/tag/{tag}",
            "published_at": "2026-11-01T00:00:00Z", "body": "### 追加\n\n- 新しいこと"}
    body.update(extra)
    return json.dumps(body).encode("utf-8")


class FakeFetch:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls = 0

    def __call__(self) -> bytes:
        self.calls += 1
        answer = self.answers[min(self.calls, len(self.answers)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class VersionTests(unittest.TestCase):
    def test_semver_order(self) -> None:
        self.assertTrue(U.is_newer("2.1.0", "2.0.0"))
        self.assertTrue(U.is_newer("2.0.10", "2.0.9"))
        self.assertTrue(U.is_newer("3.0.0", "2.99.99"))
        self.assertTrue(U.is_newer("2.1.0", "2.1.0-rc.1"))
        self.assertTrue(U.is_newer("2.1.0-rc.2", "2.1.0-rc.1"))
        self.assertTrue(U.is_newer("2.1.0-rc.10", "2.1.0-rc.9"))
        self.assertFalse(U.is_newer("2.0.0", "2.0.0"))
        self.assertFalse(U.is_newer("2.0.0", "2.1.0"))
        self.assertFalse(U.is_newer("2.1.0-rc.1", "2.1.0"))

    def test_not_versions_are_never_newer(self) -> None:
        for bad in ("", "2.1", "v2.1.0", "2.1.0.1", "02.1.0", "2.1.0a1", None, 3):
            self.assertIsNone(U.parse_version(bad), bad)
            self.assertFalse(U.is_newer(bad, "2.0.0"), bad)  # type: ignore[arg-type]

    def test_tags(self) -> None:
        self.assertEqual(U.version_from_tag("v2.1.0"), "2.1.0")
        self.assertEqual(U.version_from_tag("2.1.0"), "2.1.0")
        self.assertEqual(U.version_from_tag("quick-slide--v2.1.0"), "2.1.0")
        self.assertIsNone(U.version_from_tag("release-2.1"))
        self.assertIsNone(U.version_from_tag(None))

    def test_current_version_is_semver(self) -> None:
        from quick_slide import __version__
        self.assertIsNotNone(U.parse_version(__version__))

    def test_env_switch(self) -> None:
        for on in ("1", "true", "YES", " on "):
            self.assertTrue(U.env_disables({U.ENV_NO_CHECK: on}), on)
        for off in ("", "0", "false", "no"):
            self.assertFalse(U.env_disables({U.ENV_NO_CHECK: off}), off)
        self.assertFalse(U.env_disables({}))


class ReadReleaseTests(unittest.TestCase):
    def test_reads_what_the_screen_needs(self) -> None:
        got = U.read_release(release())
        self.assertEqual(got["version"], "2.1.0")
        self.assertEqual(got["url"], f"{U.RELEASES_PAGE}/tag/v2.1.0")
        self.assertEqual(got["name"], "Quick Slide 2.1.0")
        self.assertEqual(got["notes"], "### 追加\n\n- 新しいこと")

    def test_link_is_only_our_release_page(self) -> None:
        for url in ("https://example.com/x", f"{U.RELEASES_PAGE}evil/tag/v2.1.0", "javascript:alert(1)", None):
            got = U.read_release(release(html_url=url))
            self.assertEqual(got["url"], f"{U.RELEASES_PAGE}/tag/v2.1.0", url)

    def test_long_notes_are_cut(self) -> None:
        got = U.read_release(release(body="あ" * (U.MAX_NOTES_CHARS + 50)))
        self.assertLessEqual(len(got["notes"]), U.MAX_NOTES_CHARS + 40)
        self.assertIn("リリースの頁", got["notes"])

    def test_bad_answers(self) -> None:
        for data in (b"not json", b"[]", json.dumps({"tag_name": "nightly"}).encode(), b"\xff"):
            with self.assertRaises(U.FetchFailed) as ctx:
                U.read_release(data)
            self.assertEqual(ctx.exception.code, U.BAD_RESPONSE)


class CheckerTests(unittest.TestCase):
    def checker(self, *answers, current: str = "2.0.0") -> tuple[U.UpdateChecker, FakeFetch, Clock]:
        fetch, clock = FakeFetch(*answers), Clock()
        return U.UpdateChecker(fetch, current=current, now=clock), fetch, clock

    def test_newer_release_is_announced(self) -> None:
        c, _f, _clock = self.checker(release("v2.1.0"))
        st = c.status(disabled_by=None)
        self.assertTrue(st["update_available"])
        self.assertEqual((st["current_version"], st["latest_version"]), ("2.0.0", "2.1.0"))
        self.assertEqual(st["release_url"], f"{U.RELEASES_PAGE}/tag/v2.1.0")
        self.assertIsNone(st["error"])
        self.assertFalse(st["stale"])
        self.assertTrue(st["checked_at"].endswith("Z"))

    def test_same_or_older_is_quiet(self) -> None:
        for tag in ("v2.0.0", "v1.9.0"):
            c, _f, _clock = self.checker(release(tag))
            st = c.status(disabled_by=None)
            self.assertFalse(st["update_available"], tag)
            self.assertEqual(st["latest_version"], tag[1:])

    def test_answer_is_kept_for_a_while(self) -> None:
        c, f, clock = self.checker(release("v2.1.0"), release("v2.2.0"))
        c.status(disabled_by=None)
        clock.t += U.CACHE_SECONDS - 1
        self.assertEqual(c.status(disabled_by=None)["latest_version"], "2.1.0")
        self.assertEqual(f.calls, 1)
        clock.t += 2
        self.assertEqual(c.status(disabled_by=None)["latest_version"], "2.2.0")
        self.assertEqual(f.calls, 2)

    def test_failure_without_a_previous_answer_is_quiet(self) -> None:
        c, f, clock = self.checker(U.FetchFailed(U.UNREACHABLE), release("v2.1.0"))
        st = c.status(disabled_by=None)
        self.assertEqual(st["error"], U.UNREACHABLE)
        self.assertFalse(st["update_available"])
        self.assertIsNone(st["latest_version"])
        # 失敗したあとは、少し間をあけるまで問い合わせない（画面を開くたびに叩かない）
        clock.t += U.RETRY_SECONDS - 1
        c.status(disabled_by=None)
        self.assertEqual(f.calls, 1)
        clock.t += 2
        self.assertTrue(c.status(disabled_by=None)["update_available"])

    def test_failure_keeps_the_previous_answer_as_stale(self) -> None:
        c, _f, clock = self.checker(release("v2.1.0"), U.FetchFailed(U.UNREACHABLE))
        c.status(disabled_by=None)
        clock.t += U.CACHE_SECONDS + 1
        st = c.status(disabled_by=None)
        self.assertTrue(st["stale"])
        self.assertTrue(st["update_available"])
        self.assertEqual(st["error"], U.UNREACHABLE)

    def test_unexpected_errors_do_not_break_the_screen(self) -> None:
        c, _f, _clock = self.checker(RuntimeError("boom"))
        st = c.status(disabled_by=None)
        self.assertEqual(st["error"], U.UNREACHABLE)
        self.assertFalse(st["update_available"])

    def test_refresh_asks_again_but_not_too_often(self) -> None:
        c, f, clock = self.checker(release("v2.0.0"), release("v2.1.0"))
        c.status(disabled_by=None)
        c.status(disabled_by=None, refresh=True)
        self.assertEqual(f.calls, 1)   # 続けて押しても叩かない
        clock.t += U.REFRESH_MIN_SECONDS + 1
        self.assertTrue(c.status(disabled_by=None, refresh=True)["update_available"])
        self.assertEqual(f.calls, 2)

    def test_disabled_never_fetches(self) -> None:
        for reason in ("settings", "env"):
            c, f, _clock = self.checker(release("v2.1.0"))
            st = c.status(disabled_by=reason, refresh=True)
            self.assertEqual(f.calls, 0)
            self.assertEqual((st["enabled"], st["disabled_by"], st["update_available"]), (False, reason, False))

    def test_no_fetch_means_no_network(self) -> None:
        st = U.UpdateChecker(None).status(disabled_by=None)
        self.assertEqual((st["enabled"], st["disabled_by"]), (False, "server"))


class SettingsFileTests(unittest.TestCase):
    def test_default_on_and_saved_alone(self) -> None:
        path = temp_dir(self) / "settings.json"
        self.assertTrue(Settings.load(path, env={}).updates.check)
        path.write_text(json.dumps({"writer": "codex", "server": {"port": 7802}, "mine": 1}), encoding="utf-8")
        save_updates_section(path, check=False)
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw, {"writer": "codex", "server": {"port": 7802}, "mine": 1, "updates": {"check": False}})
        self.assertFalse(Settings.load(path, env={}).updates.check)

    def test_bad_value_is_refused(self) -> None:
        path = temp_dir(self) / "settings.json"
        path.write_text(json.dumps({"updates": {"check": "no"}}), encoding="utf-8")
        with self.assertRaises(Exception):
            Settings.load(path, env={})


@mock.patch.dict(os.environ, {U.ENV_NO_CHECK: ""})
class UpdateApiTests(unittest.TestCase):
    def test_status_and_refresh(self) -> None:
        fetch = FakeFetch(release(f"v{U.__version__}"), release("v9.0.0"))   # 1 回目は今と同じ版
        fx = start_server(self, update_fetch=fetch)
        st = fx.get("/api/update").json()
        self.assertTrue(st["enabled"])
        self.assertFalse(st["update_available"])
        fx.server.updates._last_try -= U.REFRESH_MIN_SECONDS + 1
        st = fx.get("/api/update?refresh=1").json()
        self.assertTrue(st["update_available"])
        self.assertEqual(st["latest_version"], "9.0.0")
        self.assertEqual(fetch.calls, 2)

    def test_the_test_server_does_not_reach_out(self) -> None:
        fx = start_server(self)
        st = fx.get("/api/update").json()
        self.assertEqual((st["enabled"], st["disabled_by"], st["update_available"]), (False, "server", False))

    def test_switch_off_and_on_from_the_screen(self) -> None:
        fetch = FakeFetch(release("v9.0.0"))
        fx = start_server(self, update_fetch=fetch)
        r = fx.post("/api/settings/updates", {"check": False})
        self.assertEqual(r.status, 200, r)
        self.assertEqual((r.json()["enabled"], r.json()["disabled_by"]), (False, "settings"))
        self.assertFalse(fx.get("/api/update").json()["update_available"])
        self.assertEqual(fetch.calls, 0)
        self.assertEqual(json.loads(fx.root.settings_path.read_text(encoding="utf-8"))["updates"], {"check": False})
        r = fx.post("/api/settings/updates", {"check": True})
        self.assertTrue(r.json()["update_available"])
        self.assertEqual(fetch.calls, 1)

    def test_env_wins(self) -> None:
        fetch = FakeFetch(release("v9.0.0"))
        fx = start_server(self, update_fetch=fetch)
        with mock.patch.dict(os.environ, {U.ENV_NO_CHECK: "1"}):
            st = fx.get("/api/update?refresh=1").json()
        self.assertEqual((st["enabled"], st["disabled_by"]), (False, "env"))
        self.assertEqual(fetch.calls, 0)

    def test_save_needs_session_and_a_boolean(self) -> None:
        fx = start_server(self, update_fetch=FakeFetch(release()))
        self.assertEqual(fx.post("/api/settings/updates", {"check": False}, session=False).status, 403)
        for body in ({"check": "no"}, {}, {"check": True, "extra": 1}):
            r = fx.post("/api/settings/updates", body)
            self.assertEqual((r.status, r.error_code), (400, "bad_update_setting"), body)


if __name__ == "__main__":
    unittest.main()
