"""Mini App button (?start=reonboard): a new person gets the greeting and the label app
(Anya, 25.11.2026)."""

import asyncio
import unittest
from unittest import mock

import main
import onboarding_flow as ob
import spice_stats

CID = 990001


def run_start(profile, st=None, arg="reonboard"):
    update = mock.MagicMock()
    update.effective_chat.id = CID
    update.effective_user.language_code = "ru"
    update.effective_user.first_name = "Имя"
    update.effective_user.last_name = None
    update.effective_user.username = "x"
    update.effective_user.id = CID
    context = mock.MagicMock()
    context.args = [arg] if arg else []
    context.bot_data = {}
    sent, marks, admin = [], [], []
    stored = dict(profile or {})

    def mark(cid, key):
        marks.append(key)
        stored.setdefault("cycle_flags", {})[key] = True
        return stored

    async def reply(_m, text, **kw):
        sent.append(text)

    async def note(_b, text):
        admin.append(text)

    async def vision(*a, **kw):
        return "VISION"

    flows = {} if st is None else {CID: st}
    with mock.patch.object(main, "onboarding", flows), \
            mock.patch.object(main, "user_profiles", {str(CID): profile} if profile else {}), \
            mock.patch.object(main, "subscribers", set()), \
            mock.patch.object(main.db_store, "get_profile", return_value=profile), \
            mock.patch.object(main.db_store, "update_profile", side_effect=lambda c, f: {**stored, **f}), \
            mock.patch.object(main.db_store, "mark_cycle_flag", side_effect=mark), \
            mock.patch.object(main.db_store, "save_subscriber"), \
            mock.patch.object(main, "_restore_cid_flow"), \
            mock.patch.object(main, "_bot_reply", reply), \
            mock.patch.object(main, "_name_kb", return_value=None), \
            mock.patch.object(main, "notify_admin", note), \
            mock.patch.object(ob, "kickoff_vision_opening", vision):
        asyncio.run(main.cmd_start(update, context))
    return sent, marks, admin, flows.get(CID)


class StartFromApp(unittest.TestCase):
    def test_new_person(self):
        sent, marks, admin, st = run_start(None)
        self.assertEqual(sent, [ob.get_greeting_new("ru")])
        self.assertEqual(marks, ["src_app"])
        self.assertIn("Откуда: app", admin[0])
        self.assertEqual(st["step"], ob.OB_NAME)
        self.assertTrue(st["src_ask_end"])

    def test_second_tap_does_not_skip_the_greeting(self):
        st = {"step": ob.OB_NAME, "lang": "ru", "src_ask_end": True}
        sent, marks, admin, st = run_start({"language_code": "ru"}, st)
        self.assertEqual(sent, [ob.get_greeting_new("ru")])
        self.assertTrue(st["src_ask_end"])
        self.assertEqual((marks, admin), ([], []))

    def test_returning_person_is_unchanged(self):
        p = {"name": "Катя", "main_goal": "Цель", "language_code": "ru",
             "cycle_flags": {"src_th_1": True}}
        sent, marks, admin, st = run_start(p)
        self.assertEqual((sent, marks, admin), (["VISION"], [], []))
        self.assertEqual(st["step"], ob.OB_VISION_DIALOG)
        self.assertEqual(st["name"], "Катя")

    def test_onboarding_under_way_is_unchanged(self):
        st = {"step": ob.OB_VISION_DIALOG, "name": "Катя", "lang": "ru"}
        sent, marks, _, _ = run_start({"language_code": "ru"}, st)
        self.assertEqual((sent, marks), (["VISION"], []))

    def test_ordinary_links_are_unchanged(self):
        _, marks, _, st = run_start(None, arg="th_1311")
        self.assertEqual(marks, ["src_th_1311"])
        self.assertNotIn("src_ask_end", st)
        _, marks, _, st = run_start(None, arg="")
        self.assertEqual(marks, [])
        self.assertTrue(st["src_ask_end"])


class Labels(unittest.TestCase):
    def test_answer_beats_app(self):
        f = {"src_app": True}
        self.assertEqual(spice_stats.profile_label({"cycle_flags": f}), "app")
        with mock.patch.object(ob.db, "get_profile", return_value={"cycle_flags": f}), \
                mock.patch.object(ob.db, "mark_cycle_flag") as mark:
            self.assertEqual(ob.save_source_answer(5, "tt"), "tt_ask")
            mark.assert_called_once_with(5, "src_tt_ask")
        self.assertEqual(spice_stats.profile_label({"cycle_flags": {**f, "src_tt_ask": True}}), "tt_ask")


if __name__ == "__main__":
    unittest.main()
