"""Reads share a connection and are repeated once; writes are never repeated (25.11.2026)."""

import unittest
from unittest import mock

import httpx

import db


class Reads(unittest.TestCase):
    def test_one_client(self):
        self.assertIs(db._reader(), db._reader())

    def test_read_is_repeated_once(self):
        c = mock.MagicMock()
        c.request.side_effect = [httpx.RemoteProtocolError("closed"), "ok"]
        with mock.patch.object(db, "_reader", return_value=c):
            self.assertEqual(db._send("GET", "https://x"), "ok")
        self.assertEqual(c.request.call_count, 2)

    def test_write_is_not_repeated(self):
        c = mock.MagicMock()
        c.__enter__.return_value.request.side_effect = httpx.RemoteProtocolError("closed")
        c.__exit__.return_value = False
        with mock.patch.object(db.httpx, "Client", return_value=c), self.assertRaises(httpx.RemoteProtocolError):
            db._send("POST", "https://x")
        self.assertEqual(c.__enter__.return_value.request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
