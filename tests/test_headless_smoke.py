"""Live acceptance must reject cached accounts and disconnected terminals."""

import json
import os
import unittest
from unittest.mock import patch

from headless_smoke import account_status, autologin, broker_config, journal_status


def response(**connection):
    return {"result": {"structuredContent": {
        "account": {"login": 123},
        "terminal": {"connection": {
            "state": "authorized", "server": "Broker-Demo",
            "last_authorized_at": "2026-11-16T00:00:00Z", **connection,
        }},
    }}}


class HeadlessAcceptanceTests(unittest.TestCase):
    def test_live_account_must_match_and_be_authorized_in_this_session(self):
        self.assertTrue(all(account_status(response(), "123", "Broker-Demo").values()))
        for payload, login, server in (
            (response(), "124", "Broker-Demo"),
            (response(), "123", "Other-Demo"),
            (response(state="disconnected"), "123", "Broker-Demo"),
            (response(state="connected"), "123", "Broker-Demo"),
            (response(last_authorized_at=None), "123", "Broker-Demo"),
        ):
            self.assertFalse(all(account_status(payload, login, server).values()))
        text = {"result": {"content": [{"type": "text", "text": json.dumps(response()["result"]["structuredContent"])}]}}
        self.assertTrue(all(account_status(text, "123", "Broker-Demo").values()))
        with self.assertRaises(RuntimeError):
            account_status({"result": {"isError": True}}, "123", "Broker-Demo")

    def test_login_timeout_cannot_pass_on_a_cached_disconnected_account(self):
        with patch("headless_smoke.request", return_value=(response(state="disconnected"), None)):
            with self.assertRaisesRegex(RuntimeError, "'connection_state': 'disconnected'"):
                autologin(1234, "token", "session", "123", "Broker-Demo", 0)

    def test_connection_diagnostics_do_not_expose_unexpected_broker_data(self):
        status = account_status(response(state="private broker response"), "123", "Broker-Demo")
        self.assertEqual(status["connection_state"], "unexpected-str")
        self.assertNotIn("private broker response", json.dumps(status))
        self.assertEqual(account_status(response(state="unknown"), "123", "Broker-Demo")["connection_state"], "unknown")
        self.assertFalse(account_status(response(state="unknown"), "123", "Broker-Demo")["connected"])

    def test_broker_journals_expose_only_known_status_flags(self):
        self.assertFalse(any(journal_status("private account data").values()))
        self.assertFalse(journal_status("previous authorization succeeded\nunrelated download failed")["authorization_failed"])
        status = journal_status(
            "'private login': authorized on private server\n"
            "authorization on private server failed (private reason)\n"
            "connection lost\nterminal synchronized with private server\nLiveUpdate\n"
        )
        self.assertTrue(all(status.values()))
        self.assertNotIn("private", json.dumps(status))

    def test_private_configuration_preserves_password_and_rejects_ini_injection(self):
        values = {"MT5_ACCOUNT": "123", "MT5_PASSWORD": "private $ % # pass", "MT5_SERVER": "Broker-Demo"}
        with patch.dict(os.environ, values, clear=True):
            config, login, server = broker_config()
            self.assertIn("Password=private $ % # pass\n", config)
            self.assertEqual((login, server), ("123", "Broker-Demo"))
        for key, value in (("MT5_ACCOUNT", "bad"), ("MT5_PASSWORD", ""), ("MT5_SERVER", "Demo\n[Experts]")):
            with patch.dict(os.environ, {**values, key: value}, clear=True):
                with self.assertRaises(ValueError):
                    broker_config()
