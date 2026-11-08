"""The dialog text log is off by default and only writes when LOG_DIALOG_TEXT=1."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import dialog_log


class DialogLogTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOG_DIALOG_TEXT", None)
            self.assertFalse(dialog_log.enabled())
            app = dialog_log.build_application("123:" + "a" * 35)
            self.assertNotIsInstance(app.bot, dialog_log.DialogLogBot)

    def test_enabled_logs_incoming_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dialog.log"
            env = {"LOG_DIALOG_TEXT": "1", "DIALOG_LOG_PATH": str(path)}
            with mock.patch.dict(os.environ, env):
                app = dialog_log.build_application("123:" + "a" * 35)
                self.assertIsInstance(app.bot, dialog_log.DialogLogBot)
                update = SimpleNamespace(
                    effective_chat=SimpleNamespace(id=42),
                    message=SimpleNamespace(text="хочу в зал", caption=None, photo=None),
                    callback_query=None,
                )
                asyncio.run(dialog_log._log_incoming(update, None))
                dialog_log.write(42, "БОТ:", "давай с малого")
            content = path.read_text(encoding="utf-8")
        self.assertIn("42 ОНА: хочу в зал", content)
        self.assertIn("42 БОТ: давай с малого", content)


if __name__ == "__main__":
    unittest.main()
