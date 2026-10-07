"""開ける口は、設定に書いた 1 つのアドレスだけ。

ここが守るのは 4 つ。書いたアドレスには開くこと、「ぜんぶの口」（`0.0.0.0` / `::`）は拒むこと、
名前ではなくアドレスで書かせること、そして**既定（ループバック）の口は 1 ミリも広がっていない**こと。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import start_server, temp_dir

from quick_slide.errors import StartupError, ValidationError
from quick_slide.paths import DataRoot
from quick_slide.server import QuickSlideServer
from quick_slide.server.app import allowed_hosts
from quick_slide.settings import (
    ServerSettings,
    Settings,
    bind_refusal,
    bind_scope,
    is_loopback_host,
    save_server_section,
)

# 例のアドレス。どの網のアドレスかで通るのではなく、利用者が設定に書いたから通る。
ELSEWHERE = "100.100.1.23"
ELSEWHERE_V6 = "fd7a:115c:a1e0::1234"


class WhereTheServerWillListenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self)

    def _build(self, host: str) -> QuickSlideServer:
        s = Settings(server=ServerSettings(host=host))
        return QuickSlideServer(DataRoot(self.tmp / "home"), settings=s, port=0)

    def test_the_wide_open_address_is_still_refused(self) -> None:
        """`0.0.0.0` を許すと、書いた網と同時に Wi-Fi にも出る。ここは絶対に開けない。"""
        self.assertIsNotNone(bind_refusal("0.0.0.0"))
        with self.assertRaises(StartupError):
            self._build("0.0.0.0")

    def test_the_other_wide_open_address_is_refused_too(self) -> None:
        self.assertIsNotNone(bind_refusal("::"))
        with self.assertRaises(StartupError):
            self._build("::")

    def test_an_address_the_person_wrote_is_opened(self) -> None:
        """どの網が安全かはアプリには決められない。書いてあるアドレスに開く。"""
        for host in ("192.168.18.103", "10.0.0.5", "172.16.3.9", ELSEWHERE, ELSEWHERE_V6):
            self.assertIsNone(bind_refusal(host), host)
            self._build(host)  # 例外が出ないことがこのテストの主張（bind はしない）

    def test_a_name_cannot_be_written_as_the_bind_address(self) -> None:
        """名前の指す先は設定を読んだだけでは分からない。アドレスで書かせる。"""
        for host in ("my-mac", "my-mac.example.net", "example.test"):
            self.assertIsNotNone(bind_refusal(host), host)
            with self.assertRaises(StartupError, msg=host):
                self._build(host)

    def test_loopback_is_still_allowed_and_still_the_default(self) -> None:
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertIsNone(bind_refusal(host), host)
        self.assertEqual((Settings().server.host, Settings().server.host_names), ("127.0.0.1", ()))

    def test_the_refusal_says_what_to_do_instead(self) -> None:
        self.assertIn("アドレスを書いて", bind_refusal("0.0.0.0") or "")
        self.assertIn("アドレスで書いて", bind_refusal("my-mac") or "")


class WhoMayKnockTests(unittest.TestCase):
    """`Host:` ヘッダの許可リスト（I9）。"""

    def test_loopback_allows_only_the_three_usual_names(self) -> None:
        self.assertEqual(
            allowed_hosts("127.0.0.1", 7801),
            {"127.0.0.1:7801", "localhost:7801", "[::1]:7801"},
        )

    def test_a_name_is_not_added_when_the_server_listens_on_loopback(self) -> None:
        """既定の口は広げない。ループバックに別名を許すと DNS の付け替えが通る。"""
        self.assertNotIn("my-mac:7801", allowed_hosts("127.0.0.1", 7801, ("my-mac",)))

    def test_another_address_adds_itself_and_the_configured_names(self) -> None:
        got = allowed_hosts(ELSEWHERE, 7801, ("my-mac", "my-mac.example.net"))
        self.assertIn(f"{ELSEWHERE}:7801", got)
        self.assertIn("my-mac:7801", got)
        self.assertIn("my-mac.example.net:7801", got)
        self.assertIn("127.0.0.1:7801", got)

    def test_an_unlisted_name_is_not_allowed(self) -> None:
        got = allowed_hosts(ELSEWHERE, 7801, ("my-mac",))
        self.assertNotIn("evil.test:7801", got)
        self.assertNotIn("my-mac2:7801", got)

    def test_the_name_is_matched_without_caring_about_case(self) -> None:
        self.assertIn("my-mac:7801", allowed_hosts(ELSEWHERE, 7801, ("MY-MAC",)))

    def test_an_ipv6_address_is_wrapped_in_brackets(self) -> None:
        self.assertIn(f"[{ELSEWHERE_V6}]:7801", allowed_hosts(ELSEWHERE_V6, 7801))


class NamesAreWrittenNotGuessedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self)
        self.path = self.tmp / "settings.json"

    def _load(self, raw: dict) -> Settings:
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        return Settings.load(self.path, env={})

    def test_the_names_come_from_the_settings_file(self) -> None:
        s = self._load({"server": {"host": ELSEWHERE, "host_names": ["my-mac", "my-mac.example.net"]}})
        self.assertEqual(s.server.host_names, ("my-mac", "my-mac.example.net"))
        self.assertFalse(s.server.is_loopback)

    def test_a_name_on_loopback_is_refused_while_reading_the_settings(self) -> None:
        with self.assertRaises(ValidationError):
            self._load({"server": {"host": "127.0.0.1", "host_names": ["my-mac"]}})

    def test_a_name_may_not_carry_a_port_or_a_path(self) -> None:
        for name in ("my-mac:7801", "my-mac/slides", "mac 01"):
            with self.assertRaises(ValidationError, msg=name):
                self._load({"server": {"host": ELSEWHERE, "host_names": [name]}})

    def test_the_names_must_be_a_list(self) -> None:
        with self.assertRaises(ValidationError):
            self._load({"server": {"host": ELSEWHERE, "host_names": "my-mac"}})

    def test_no_names_means_only_the_address_opens_it(self) -> None:
        s = self._load({"server": {"host": ELSEWHERE}})
        self.assertEqual(s.server.host_names, ())
        self.assertNotIn("my-mac:7801", allowed_hosts(s.server.host, 7801, s.server.host_names))


class TheUrlWeShowThePersonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self)

    def _server(self, settings: Settings, **kw) -> QuickSlideServer:
        return QuickSlideServer(DataRoot(self.tmp / "home"), settings=settings, port=0, **kw)

    def test_the_written_name_is_what_the_person_sees(self) -> None:
        s = Settings(server=ServerSettings(host=ELSEWHERE, host_names=("my-mac",)))
        self.assertEqual(self._server(s)._display_host, "my-mac")

    def test_without_a_name_the_address_is_shown(self) -> None:
        s = Settings(server=ServerSettings(host=ELSEWHERE))
        self.assertEqual(self._server(s)._display_host, ELSEWHERE)

    def test_overriding_the_host_drops_the_configured_name(self) -> None:
        """`--host` で上書きしたのに設定の名前を見せると、設定と実態が食い違う。"""
        s = Settings(server=ServerSettings(host=ELSEWHERE, host_names=("my-mac",)))
        self.assertEqual(self._server(s, host="127.0.0.1")._display_host, "127.0.0.1")


class TheDefaultDoorDidNotWidenTests(unittest.TestCase):
    """本物のサーバに本物の HTTP で訊く。既定のままなら機械の名前は通らない。"""

    def test_a_machine_name_is_still_turned_away_on_a_default_server(self) -> None:
        fx = start_server(self)
        r = fx.get("/api/health", headers={"Host": "my-mac:7801"})
        self.assertEqual((r.status, r.error_code), (421, "bad_host"))

    def test_the_usual_names_still_work(self) -> None:
        fx = start_server(self)
        self.assertEqual(fx.get("/api/health").status, 200)

    def test_health_says_it_is_shut_when_it_is_shut(self) -> None:
        """開いたなら開いたと言う。数字だけ見た人が取り違えないように。"""
        fx = start_server(self)
        self.assertEqual(fx.get("/api/health").json()["bind_scope"], "loopback")


class ItSaysSoWhenItIsOpenTests(unittest.TestCase):
    """開いた口を health が正直に言うこと。

    **実サーバを外向きのアドレスで立てる試験は置いていない。** 試験機に何のアドレスが
    生えているかは機械ごとに違い（macOS は `127.0.0.2` すら既定で無い）、条件付きで飛ばす網は
    「通った」と「試していない」の区別がつかなくなる。代わりに、判断する関数そのものと、
    health がその関数を通っていることを見る。
    """

    def test_the_loopback_names_are_the_only_shut_ones(self) -> None:
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertEqual(bind_scope(host), "loopback", host)
        for host in (ELSEWHERE, ELSEWHERE_V6, "192.168.18.103", "10.0.0.5"):
            self.assertEqual(bind_scope(host), "network", host)

    def test_health_asks_that_function_rather_than_deciding_again(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src/quick_slide/server/app.py").read_text(encoding="utf-8")
        line = next(ln for ln in source.splitlines() if '"bind_scope"' in ln)
        self.assertIn("bind_scope(self.host)", line)


# 同じループバックの別の書き方。文字列で見比べると「ループバック以外」に見えてしまうもの。
LOOPBACK_SPELLINGS = (
    "0:0:0:0:0:0:0:1",        # ::1 を省略せずに書いた形
    "0000:0000:0000:0000:0000:0000:0000:0001",
    "127.0.0.2",              # 127.0.0.0/8 はぜんぶループバック
    "127.255.255.254",
    "::ffff:127.0.0.1",       # IPv4 を写した IPv6
    "::ffff:7f00:1",
    "LOCALHOST",
    "localhost.",
)


class OtherSpellingsOfLoopbackTests(unittest.TestCase):
    """ループバックかどうかはアドレスの意味で決める。別の書き方で名前（`host_names`）を足させない。"""

    def setUp(self) -> None:
        self.tmp = temp_dir(self)

    def test_every_spelling_is_loopback(self) -> None:
        for host in LOOPBACK_SPELLINGS:
            self.assertTrue(is_loopback_host(host), host)
            self.assertEqual(bind_scope(host), "loopback", host)
            self.assertTrue(ServerSettings(host=host).is_loopback, host)
        for host in (ELSEWHERE, ELSEWHERE_V6, "::ffff:100.100.1.23", "my-mac", "localhost.example.net", "127.1"):
            self.assertFalse(is_loopback_host(host), host)

    def test_a_name_cannot_be_attached_to_another_spelling(self) -> None:
        for host in LOOPBACK_SPELLINGS:
            with self.assertRaises(ValidationError, msg=host):
                ServerSettings(host=host, host_names=("my-mac",))

    def test_the_screen_cannot_save_a_name_on_another_spelling(self) -> None:
        path = self.tmp / "settings.json"
        for host in ("0:0:0:0:0:0:0:1", "127.0.0.2", "::ffff:127.0.0.1"):
            with self.assertRaises(ValidationError, msg=host):
                save_server_section(path, host=host, host_names=("my-mac",))
            self.assertFalse(path.exists(), host)

    def test_the_settings_file_cannot_attach_a_name_either(self) -> None:
        path = self.tmp / "settings.json"
        for host in ("0:0:0:0:0:0:0:1", "127.0.0.2", "::ffff:127.0.0.1"):
            path.write_text(json.dumps({"server": {"host": host, "host_names": ["my-mac"]}}), encoding="utf-8")
            with self.assertRaises(ValidationError, msg=host):
                Settings.load(path, env={})

    def test_names_never_join_the_allowed_hosts_on_loopback(self) -> None:
        for host in LOOPBACK_SPELLINGS:
            got = allowed_hosts(host, 7801, ("my-mac",))
            self.assertNotIn("my-mac:7801", got, host)
            self.assertIn("127.0.0.1:7801", got, host)

    def test_the_bound_loopback_address_itself_may_knock(self) -> None:
        """`127.0.0.2` に開いたら、そのアドレスでは届く（アドレスは DNS で付け替えられない）。"""
        self.assertIn("127.0.0.2:7801", allowed_hosts("127.0.0.2", 7801))
        self.assertIn("[::1]:7801", allowed_hosts("0:0:0:0:0:0:0:1", 7801))

    def test_other_spellings_of_everywhere_are_refused(self) -> None:
        for host in ("0:0:0:0:0:0:0:0", "::0", "::ffff:0.0.0.0"):
            self.assertIsNotNone(bind_refusal(host), host)
            self.assertIn("ぜんぶの口", bind_refusal(host) or "", host)

    def test_no_one_compares_loopback_as_a_string_any_more(self) -> None:
        """判定は `is_loopback_host` の 1 か所。文字列の一覧で比べる場所が戻ってきたら落とす。"""
        src = Path(__file__).resolve().parents[1] / "src/quick_slide"
        for path in src.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in ("in LOOPBACK_HOSTS", "in WILDCARD_HOSTS"):
                self.assertNotIn(needle, text, f"{path.relative_to(src)}: {needle}")


if __name__ == "__main__":
    unittest.main()
