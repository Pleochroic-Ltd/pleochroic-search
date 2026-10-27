# SPDX-License-Identifier: AGPL-3.0-or-later
# pylint: disable=missing-module-docstring,missing-class-docstring,invalid-name,protected-access,too-few-public-methods
# pylint: disable=unbalanced-tuple-unpacking

import time

from mock import patch

import searx.engines
from searx import pleochroic_proxy
from searx.network import network as network_module
from searx.network.network import Network
from searx.results import ResultContainer
from searx.search.processors import PROCESSORS
from tests import SearxTestCase

PROXY = "socks5h://acct-region-US-sid-AbC12345-t-60:pw@proxy.example:3010"


class TestSessionId(SearxTestCase):

    def test_with_new_sid_replaces_only_the_session_id(self):
        self.assertEqual(
            "socks5h://acct-region-US-sid-Zz9Zz9Zz-t-60:pw@proxy.example:3010",
            pleochroic_proxy.with_new_sid(PROXY, "Zz9Zz9Zz"),
        )

    def test_with_new_sid_leaves_a_url_without_one(self):
        url = "socks5h://acct:pw@proxy.example:3010"
        self.assertEqual(url, pleochroic_proxy.with_new_sid(url, "Zz9Zz9Zz"))

    def test_new_sid_keeps_the_length_and_alphabet(self):
        sid = pleochroic_proxy.new_sid("AbC12345")
        self.assertEqual(8, len(sid))
        self.assertTrue(sid.isalnum() and sid.isascii())


class TestExitFailed(SearxTestCase):

    def test_exit_errors(self):
        for error in (
            "timeout",
            "curl_cffi.requests.exceptions.Timeout",
            "curl_cffi.requests.exceptions.ProxyError",
            "searx.exceptions.SearxEngineCaptchaException",
            "searx.exceptions.SearxEngineAccessDeniedException",
        ):
            self.assertTrue(pleochroic_proxy.is_exit_error(error), error)
        for error in (
            "searx.exceptions.SearxEngineAPIException",
            "curl_cffi.requests.exceptions.HTTPError",
            "curl_cffi.requests.exceptions.TooManyRedirects",
            "KeyError",
        ):
            self.assertFalse(pleochroic_proxy.is_exit_error(error), error)

    def test_thresholds(self):
        self.assertTrue(pleochroic_proxy.exit_failed(engines=7, failed=4))
        self.assertFalse(pleochroic_proxy.exit_failed(engines=7, failed=3))
        self.assertFalse(pleochroic_proxy.exit_failed(engines=1, failed=1))
        self.assertFalse(pleochroic_proxy.exit_failed(engines=0, failed=0))


class TestSwap(SearxTestCase):

    def setUp(self):
        super().setUp()
        self.shared = Network(proxies={"all://": [PROXY]})
        self.direct = Network()
        networks = {"default": self.shared, "google": self.shared, "wikipedia": self.direct}
        self.setattr4test(network_module, "NETWORKS", networks)
        self.setattr4test(pleochroic_proxy, "_last_swap", -pleochroic_proxy.COOLDOWN)
        self.setattr4test(pleochroic_proxy, "CLOSE_AFTER", 0.05)

    def sid(self) -> str:
        url = self.shared.proxies["all://"][0]
        return pleochroic_proxy._SID.search(url).group(2)

    def test_swap_moves_every_network_to_one_new_session_and_closes_old_clients_later(self):
        self.shared._clients = {"old": _Client()}
        stale = self.shared._clients["old"]

        self.assertTrue(pleochroic_proxy.swap())

        self.assertNotEqual("AbC12345", self.sid())
        self.assertEqual({}, self.shared._clients)
        self.assertFalse(stale.closed)
        time.sleep(0.5)
        self.assertTrue(stale.closed)
        self.assertEqual(((("all://", self.shared.proxies["all://"][0]),)), next(self.shared._proxies_cycle))
        self.assertIsNone(self.direct.proxies)

    def test_swap_lifts_only_exit_suspensions(self):
        flagged, broken = list(PROCESSORS.values())[:2]
        flagged.suspended_status.suspend(3600, "searx.exceptions.SearxEngineCaptchaException")
        broken.suspended_status.suspend(3600, "searx.exceptions.SearxEngineAPIException")
        try:
            pleochroic_proxy.swap()

            self.assertFalse(flagged.suspended_status.is_suspended)
            self.assertTrue(broken.suspended_status.is_suspended)
        finally:
            flagged.suspended_status.resume()
            broken.suspended_status.resume()

    def test_swap_without_a_session_id_does_nothing(self):
        self.shared.proxies = {"all://": ["socks5h://acct:pw@proxy.example:3010"]}
        self.shared._clients = {"kept": _Client()}

        self.assertFalse(pleochroic_proxy.swap())

        self.assertIn("kept", self.shared._clients)

    def test_swap_gives_each_proxy_url_its_own_session(self):
        other = PROXY.replace("AbC12345", "XyZ98765")
        self.shared.proxies = {"all://": [PROXY, other]}

        pleochroic_proxy.swap()

        first, second = (pleochroic_proxy._SID.search(u).group(2) for u in self.shared.proxies["all://"])
        self.assertNotIn(first, ("AbC12345", "XyZ98765"))
        self.assertNotIn(second, ("AbC12345", "XyZ98765", first))

    def test_after_search_swaps_once_per_cooldown(self):
        def failing_search() -> ResultContainer:
            container = ResultContainer()
            for engine in searx.engines.engines:
                container.add_unresponsive_engine(engine, "curl_cffi.requests.exceptions.Timeout")
            return container

        with patch.object(pleochroic_proxy, "swap", wraps=pleochroic_proxy.swap) as swap:
            pleochroic_proxy.after_search(failing_search())
            pleochroic_proxy.after_search(failing_search())

        self.assertEqual(1, swap.call_count)
        self.assertNotEqual("AbC12345", self.sid())

    def test_after_search_ignores_engines_skipped_as_already_suspended(self):
        container = ResultContainer()
        for engine in searx.engines.engines:
            container.add_unresponsive_engine(engine, "searx.exceptions.SearxEngineCaptchaException", suspended=True)

        pleochroic_proxy.after_search(container)

        self.assertEqual("AbC12345", self.sid())

    def test_a_failed_swap_does_not_use_up_the_cooldown(self):
        def failing_search() -> ResultContainer:
            container = ResultContainer()
            for engine in searx.engines.engines:
                container.add_unresponsive_engine(engine, "curl_cffi.requests.exceptions.ProxyError")
            return container

        with patch.object(pleochroic_proxy, "swap", side_effect=[TimeoutError(), True]) as swap:
            pleochroic_proxy.after_search(failing_search())
            pleochroic_proxy.after_search(failing_search())

        self.assertEqual(2, swap.call_count)

    def test_after_search_leaves_a_mostly_working_search(self):
        working, flagged = list(searx.engines.engines)[:2]
        container = ResultContainer()
        container.add_timing(working, 0.1, 0.1)
        container.add_unresponsive_engine(flagged, "searx.exceptions.SearxEngineCaptchaException")

        pleochroic_proxy.after_search(container)

        self.assertEqual("AbC12345", self.sid())


class _Client:
    closed = False

    async def aclose(self):
        self.closed = True
