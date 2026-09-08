"""Status screen: plain, one line per thing, no jargon noise."""
import io
import unittest
from contextlib import redirect_stdout

from helpers import load_wizard

w = load_wizard()


def fixture():
    return w.migrate_db({
        "gemini": {"keys": ["K1", "K2"], "models": ["gemini-3.7-flash"], "endpoints": []},
        "custom_apinex": {"keys": ["AX"], "models": ["free/deepseek-v4-flash-0731"],
                          "endpoints": [], "base_url": "https://a.example/v1",
                          "label": "apinex"},
        "custom_orcarouter": {"keys": ["OR"], "models": ["deepseek-v4-flash:free"],
                              "endpoints": [], "base_url": "https://b.example/v1",
                              "label": "OrcaRouter"},
        "_aliases": {"deepseek-v4-flash": [
            {"provider": "custom_apinex", "model": "free/deepseek-v4-flash-0731"},
            {"provider": "custom_orcarouter", "model": "deepseek-v4-flash:free"}]},
    })


def shown(fn, *args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


class StatusTest(unittest.TestCase):
    def test_normal_mode_plain(self):
        out = shown(w.print_status, fixture())
        self.assertIn("2 keys, 1 model", out)
        self.assertIn("1 key, 1 model", out)
        self.assertIn("via apinex, OrcaRouter", out)
        # no jargon noise in normal mode
        for bad in ("creds=", "deployments=", "quota domains", "0 healthy",
                    "RPM unknown", "credential:cred-", "control plane"):
            self.assertNotIn(bad, out)

    def test_plural(self):
        self.assertEqual(w._plural(1, "key"), "1 key")
        self.assertEqual(w._plural(13, "key"), "13 keys")

    def test_shared_quota_flag(self):
        db = fixture()
        for c in db["gemini"]["credentials"]:
            c["quota_domain"] = "google-project-a"
        w.ensure_quota_domain(db, "google-project-a", provider="gemini",
                              rpm=10, confidence="manual", source="test")
        out = shown(w.print_status, db)
        self.assertIn("keys share 1 quota", out)
        self.assertIn("~10/min", out)

    def test_help_plain(self):
        out = shown(w.show_help)
        self.assertIn("add <name>", out)
        for bad in ("control plane", "quota domain", "deployment"):
            self.assertNotIn(bad, out)


class QuotaHintTest(unittest.TestCase):
    def test_shows_while_ambiguous(self):
        db = fixture()
        self.assertTrue(w.needs_quota_hint(db, "gemini"))
        out = shown(w.print_status, db)
        self.assertIn("Same Google project for all keys? Type 'quota'", out)

    def test_silent_single_key(self):
        db = w.migrate_db({"gemini": {"keys": ["K1"], "models": ["m"], "endpoints": []}})
        self.assertFalse(w.needs_quota_hint(db, "gemini"))

    def test_silent_once_grouped(self):
        db = fixture()
        for c in db["gemini"]["credentials"]:
            c["quota_domain"] = "google-project-a"
        self.assertFalse(w.needs_quota_hint(db, "gemini"))
        out = shown(w.print_status, db)
        self.assertNotIn("Same Google project", out)

    def test_silent_once_reviewed(self):
        db = fixture()
        db["gemini"]["quota_reviewed"] = True
        self.assertFalse(w.needs_quota_hint(db, "gemini"))

    def test_silent_non_project_provider(self):
        db = w.migrate_db({"openrouter": {"keys": [f"K{i}" for i in range(10)],
                                          "models": ["m"], "endpoints": []}})
        self.assertFalse(w.needs_quota_hint(db, "openrouter"))

    def test_quota_screen_marks_reviewed(self):
        from unittest.mock import patch
        db = fixture()
        self.assertTrue(w.needs_quota_hint(db, "gemini"))
        with patch("builtins.input", side_effect=["q"]):
            w.manage_quota(db)
        self.assertTrue(db["gemini"]["quota_reviewed"])
        self.assertFalse(w.needs_quota_hint(db, "gemini"))


if __name__ == "__main__":
    unittest.main()
