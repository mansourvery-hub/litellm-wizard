"""Shared test helpers: import wizard with isolated temp paths."""
import importlib
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_wizard():
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    import wizard as w
    importlib.reload(w)
    tmp = tempfile.mkdtemp(prefix="wiztest-")
    w.DB_FILE = os.path.join(tmp, "providers_db.json")
    w.YAML_FILE = os.path.join(tmp, "config.yaml")
    w.SECRET_FILE = os.path.join(tmp, ".master_key")
    return w


def load_sync():
    import importlib.util
    path = os.path.join(REPO, "sync-opencode.py")
    spec = importlib.util.spec_from_file_location("sync_opencode", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_db_two_keys_shared():
    return {
        "gemini": {"keys": ["K1-SECRET-AAA", "K2-SECRET-BBB"],
                   "models": ["gemini-3.7-flash"], "endpoints": []},
    }


def read_yaml(path):
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


LEGACY_DB = {
    "gemini": {"keys": ["OLDKEY1", "OLDKEY2"],
               "models": ["gemini-3.7-flash", "gemini-3.1-pro-preview"],
               "endpoints": []},
    "openrouter": {"keys": ["ORKEY"], "models": ["minimax/minimax-m3:free"],
                   "endpoints": []},
    "_unified": {
        "deepseek-v4-flash": [
            {"provider": "custom_a", "model": "free/deepseek-v4-flash-0731"},
            {"provider": "custom_a", "model": "free/deepseek-v4-flash-0731"},
            "garbage-entry",
            {"provider": "", "model": ""},
        ]
    },
}
