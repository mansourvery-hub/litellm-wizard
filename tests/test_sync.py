"""OpenCode sync: JSONC, backup, idempotence, preservation, no secrets."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from helpers import REPO, load_sync, load_wizard

sync = load_sync()
w = load_wizard()


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


class StripJsoncTest(unittest.TestCase):
    def test_comments_and_trailing_commas(self):
        raw = '{\n// comment\n"a": 1,\n/* multi\nline */\n"b": [1, 2,],\n}'
        self.assertEqual(json.loads(sync._strip_jsonc(raw)), {"a": 1, "b": [1, 2]})


class LoadAliasesTest(unittest.TestCase):
    def test_pools_and_roles(self):
        tmp = tempfile.mkdtemp(prefix="synctest-")
        cfg_path = os.path.join(tmp, "config.yaml")
        write(cfg_path,
              "model_list:\n- model_name: pool-a\n  litellm_params: {model: x}\n"
              "model_group_alias:\n  fast: pool-a\n")
        aliases, roles, _ = sync.load_aliases(cfg_path)
        self.assertEqual(aliases, ["pool-a"])
        self.assertEqual(roles, ["fast"])


class SyncFlowTest(unittest.TestCase):
    def run_sync(self, opencode_text, config_text, extra=()):
        tmp = tempfile.mkdtemp(prefix="synctest-")
        oc = os.path.join(tmp, "opencode.json")
        lc = os.path.join(tmp, "config.yaml")
        write(oc, opencode_text)
        write(lc, config_text)
        cmd = [sys.executable, os.path.join(REPO, "sync-opencode.py"),
               "--opencode", oc, "--litellm-config", lc, *extra]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        with open(oc) as f:
            after = f.read()
        return r, after, tmp

    CONFIG = ("model_list:\n- model_name: pool-a\n  litellm_params: {model: x}\n"
              "- model_name: pool-b\n  litellm_params: {model: y}\n"
              "model_group_alias:\n  fast: pool-a\n")

    def test_sync_writes_block_and_backup(self):
        oc = '{"provider": {"other": {"x": 1}}, "other": true}'
        r, after, tmp = self.run_sync(oc, self.CONFIG)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        cfg = json.loads(after)
        self.assertEqual(cfg["provider"]["other"], {"x": 1})  # untouched
        self.assertTrue(cfg["other"])
        models = cfg["provider"]["litellm"]["models"]
        self.assertIn("pool-a", models)
        self.assertIn("fast", models)  # roles included
        self.assertEqual(cfg["provider"]["litellm"]["options"]["apiKey"],
                         "{env:LITELLM_MASTER_KEY}")
        self.assertTrue(any(n.startswith("opencode.json.bak-") for n in os.listdir(tmp)))

    def test_no_roles_flag(self):
        oc = '{"provider": {}}'
        r, after, _ = self.run_sync(oc, self.CONFIG, extra=("--no-roles",))
        cfg = json.loads(after)
        self.assertNotIn("fast", cfg["provider"]["litellm"]["models"])

    def test_no_secrets_leak(self):
        oc = '{"provider": {}}'
        r, after, _ = self.run_sync(oc, self.CONFIG)
        for secret in ("sk-or-v1-SECRET", "AQ.SECRET", "thk_live_SECRET"):
            self.assertNotIn(secret, after)

    def test_idempotent(self):
        oc = '{"provider": {}}'
        tmp = tempfile.mkdtemp(prefix="synctest-")
        ocp = os.path.join(tmp, "opencode.json")
        lcp = os.path.join(tmp, "config.yaml")
        write(ocp, oc)
        write(lcp, self.CONFIG)
        base = [sys.executable, os.path.join(REPO, "sync-opencode.py"),
                "--opencode", ocp, "--litellm-config", lcp]
        subprocess.run(base, check=True, capture_output=True, timeout=60)
        with open(ocp) as f:
            first = json.load(f)
        subprocess.run(base, check=True, capture_output=True, timeout=60)
        with open(ocp) as f:
            second = json.load(f)
        self.assertEqual(first, second)

    def test_bad_json_does_nothing(self):
        tmp = tempfile.mkdtemp(prefix="synctest-")
        ocp = os.path.join(tmp, "opencode.json")
        lcp = os.path.join(tmp, "config.yaml")
        write(ocp, "{not valid json!!!")
        write(lcp, self.CONFIG)
        r = subprocess.run([sys.executable, os.path.join(REPO, "sync-opencode.py"),
                            "--opencode", ocp, "--litellm-config", lcp],
                           capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        with open(ocp) as f:
            self.assertEqual(f.read(), "{not valid json!!!")

    def test_dry_run_changes_nothing(self):
        oc = '{"provider": {}}'
        r, after, _ = self.run_sync(oc, self.CONFIG, extra=("--dry-run",))
        self.assertEqual(json.loads(after), {"provider": {}})


class SecretsTest(unittest.TestCase):
    def test_masking(self):
        self.assertEqual(w._mask_secret("sk-abcdef123456"), "...3456")
        self.assertNotIn("sk-abcdef", w._mask_secret("sk-abcdef123456"))

    def test_generated_yaml_has_no_master_secret(self):
        db = w.migrate_db({"gemini": {"keys": ["SUPERSECRETK"], "models": ["m"],
                                      "endpoints": []}})
        w.generate_yaml(db)
        with open(w.YAML_FILE) as f:
            text = f.read()
        self.assertNotIn("sk-litellm-local-secret", text)


if __name__ == "__main__":
    unittest.main()
