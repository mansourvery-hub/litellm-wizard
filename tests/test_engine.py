"""Engine facade tests (Milestone 1). Temp dirs + fake keys only."""
import json
import os
import stat
import tempfile
import unittest

import engine


def tmp_paths():
    tmp = tempfile.mkdtemp(prefix="engtest-")
    return engine.EnginePaths.temp(tmp), tmp


class PathsTest(unittest.TestCase):
    def test_env_overrides(self):
        tmp = tempfile.mkdtemp(prefix="engtest-")
        os.environ["LITELLM_DB_FILE"] = os.path.join(tmp, "db.json")
        try:
            p = engine.EnginePaths.from_env()
            self.assertEqual(p.db_file, os.path.join(tmp, "db.json"))
        finally:
            del os.environ["LITELLM_DB_FILE"]


class StateTest(unittest.TestCase):
    def test_roundtrip_preserves_unknown_fields(self):
        p, _ = tmp_paths()
        db = engine.load_state(p)
        db["_mystery_plugin"] = {"keep": 1}
        db["gemini"] = {"keys": [], "models": [], "endpoints": []}
        engine.save_state(db, p)
        back = engine.load_state(p)
        self.assertEqual(back["_mystery_plugin"], {"keep": 1})
        mode = stat.S_IMODE(os.stat(p.db_file).st_mode)
        self.assertEqual(oct(mode), "0o600")

    def test_credential_id_stable(self):
        self.assertEqual(engine.credential_id("abc"), engine.credential_id("abc"))
        self.assertTrue(engine.credential_id("abc").startswith("cred-"))
        self.assertNotIn("abc", engine.credential_id("a-very-long-secret-abc"))

    def test_mask_never_leaks(self):
        self.assertNotIn("sk-abcdef", engine.mask_secret("sk-abcdef123456"))


class CredentialsTest(unittest.TestCase):
    def test_add_and_list_and_remove(self):
        db = engine.load_state(engine.EnginePaths.temp(tempfile.mkdtemp()))
        ids = engine.add_credentials(db, "gemini", ["K1-FAKE", "K2-FAKE"])
        self.assertEqual(len(ids), 2)
        creds = engine.list_credentials(db, "gemini")
        self.assertEqual(len(creds), 2)
        for c in creds:  # secret-free summaries
            self.assertIn("suffix", c)
            self.assertNotIn("K1-FAKE", json.dumps(c))
            self.assertNotIn("K2-FAKE", json.dumps(c))
        n = engine.remove_credentials(db, "gemini", [creds[0]["id"]])
        self.assertEqual(n, 1)
        self.assertEqual(len(engine.list_credentials(db, "gemini")), 1)

    def test_capacity_counts_unique_domains_not_keys(self):
        db = engine.load_state(engine.EnginePaths.temp(tempfile.mkdtemp()))
        engine.add_credentials(db, "gemini", ["K1-FAKE", "K2-FAKE"])
        engine.set_quota_domains(db, "gemini", "shared")
        cap = engine.calculate_capacity(db, [("gemini", "gemini-3.7-flash")])
        self.assertEqual(cap["domains"], 1)

    def test_grouping_question_single_key_no_ask(self):
        import wizard as w
        db = w.migrate_db({"gemini": {"keys": ["ONLY-FAKE"], "models": [],
                                      "endpoints": []}})
        self.assertFalse(engine.needs_grouping_question(db, "gemini"))


class CompilerTest(unittest.TestCase):
    def test_compile_and_write_and_overview(self):
        p, _ = tmp_paths()
        db = engine.load_state(p)
        engine.add_credentials(db, "gemini", ["GK1-FAKE"])
        db["gemini"]["models"] = ["gemini-3.7-flash"]
        deps, _pools, _roles, errors = engine.compile_config(db)
        self.assertEqual(errors, [])
        self.assertEqual(len(deps), 1)
        n = engine.write_config(db, p)
        self.assertEqual(n, 1)
        mode = stat.S_IMODE(os.stat(p.yaml_file).st_mode)
        self.assertEqual(oct(mode), "0o600")
        ov = engine.gateway_overview(db, p, status="running")
        self.assertIn("gemini-3.7-flash", ov["pools"])

    def test_combine_tier_safety(self):
        db = engine.load_state(engine.EnginePaths.temp(tempfile.mkdtemp()))
        # different stems are MANUAL -> must not silently merge
        with self.assertRaises(ValueError):
            engine.combine_models(db, "mixed-pool",
                                  [("gemini", "gemini-3.7-flash"),
                                   ("ollama_local", "qwen2.5-coder:7b")])
        engine.combine_models(db, "flash-pool",
                              [("gemini", "gemini-3.7-flash"),
                               ("openrouter", "gemini-3.7-flash")])
        self.assertIn("flash-pool", engine.get_combined_models(db))


class ApplyTest(unittest.TestCase):
    def test_apply_no_restart_when_unchanged(self):
        p, _ = tmp_paths()
        db = engine.load_state(p)
        engine.add_credentials(db, "gemini", ["GK1-FAKE"])
        db["gemini"]["models"] = ["gemini-3.7-flash"]
        calls = []

        def runner(*a, **k):
            calls.append(a)
            class R:
                returncode = 0
            return R()

        r1 = engine.apply_config(db, p, runner=runner, wait_fn=lambda t: True)
        self.assertTrue(r1["changed"])
        self.assertTrue(r1["restarted"])
        calls.clear()
        r2 = engine.apply_config(db, p, runner=runner, wait_fn=lambda t: True)
        self.assertFalse(r2["changed"])
        self.assertFalse(r2["restarted"])
        self.assertEqual(calls, [])

    def test_gateway_status_mockable(self):
        class R:
            stdout = "active\n"
            returncode = 0
        self.assertEqual(engine.gateway_status(runner=lambda *a, **k: R()), "running")


class SyncTest(unittest.TestCase):
    def test_sync_dry_run_then_write(self):
        p, tmp = tmp_paths()
        db = engine.load_state(p)
        engine.add_credentials(db, "gemini", ["GK1-FAKE"])
        db["gemini"]["models"] = ["gemini-3.7-flash"]
        engine.write_config(db, p)
        oc = os.path.join(tmp, "opencode.json")
        with open(oc, "w") as f:
            json.dump({"provider": {}}, f)
        p.opencode_json = oc
        r = engine.sync_opencode(p, dry_run=True)
        self.assertFalse(r["wrote"])
        self.assertIn("gemini-3.7-flash", r["exposed"])
        r2 = engine.sync_opencode(p)
        self.assertTrue(r2["wrote"])
        with open(oc) as f:
            cfg = json.load(f)
        self.assertIn("gemini-3.7-flash", cfg["provider"]["litellm"]["models"])
        self.assertEqual(cfg["provider"]["litellm"]["options"]["apiKey"],
                         "{env:LITELLM_MASTER_KEY}")
        with open(oc) as f:
            on_disk = f.read()
        self.assertNotIn("GK1-FAKE", on_disk)
        self.assertTrue(any(n.startswith("opencode.json.bak-") for n in os.listdir(tmp)))


if __name__ == "__main__":
    unittest.main()
