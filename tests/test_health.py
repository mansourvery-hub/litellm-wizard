"""Error classification + validation modes (mocked, deterministic)."""
import unittest

from helpers import load_wizard

w = load_wizard()


class ClassifyTest(unittest.TestCase):
    def test_429_is_ratelimited(self):
        self.assertEqual(w.classify_http_error(429, "slow down"), "RATE_LIMITED")
        self.assertEqual(w.classify_http_error(None, "RATELIMIT xyz"), "RATE_LIMITED")

    def test_auth(self):
        self.assertEqual(w.classify_http_error(401, "bad key"), "AUTH_ERROR")
        self.assertEqual(w.classify_http_error(403, "forbidden"), "AUTH_ERROR")

    def test_bad_request(self):
        self.assertEqual(w.classify_http_error(400, "x"), "BAD_REQUEST")
        self.assertEqual(w.classify_http_error(404, "no model"), "BAD_REQUEST")

    def test_server_vs_timeout(self):
        self.assertEqual(w.classify_http_error(500, "boom"), "SERVER_ERROR")
        self.assertEqual(w.classify_http_error(503, "busy"), "SERVER_ERROR")
        self.assertEqual(w.classify_http_error(None, "Connection timed out"), "TIMEOUT")
        self.assertEqual(w.classify_http_error(None, "Connection refused"), "UNAVAILABLE")

    def test_probe_wrapper_maps(self):
        orig = w.test_single_model
        try:
            w.test_single_model = lambda *a, **k: ("FAIL", "HTTP 401: bad")
            self.assertEqual(w.probe_model_classified("gemini", "m", "k")[0], "AUTH_ERROR")
            w.test_single_model = lambda *a, **k: ("RATELIMIT", "throttled")
            self.assertEqual(w.probe_model_classified("gemini", "m", "k")[0], "RATE_LIMITED")
            w.test_single_model = lambda *a, **k: ("OK", "ok")
            self.assertEqual(w.probe_model_classified("gemini", "m", "k")[0], "OK")
        finally:
            w.test_single_model = orig


class MatrixTest(unittest.TestCase):
    def test_fast(self):
        m = w.plan_probe_matrix("gemini", ["a", "b"], ["K1", "K2"], [], mode="FAST")
        self.assertEqual(m, [("a", "K1", None), ("b", "K1", None)])

    def test_strict(self):
        m = w.plan_probe_matrix("gemini", ["a", "b"], ["K1", "K2"], [], mode="STRICT")
        self.assertEqual(len(m), 4)

    def test_sample_one_per_domain(self):
        db = w.migrate_db({"gemini": {"keys": ["K1", "K2", "K3"], "models": ["a"],
                                      "endpoints": []}})
        creds = db["gemini"]["credentials"]
        creds[0]["quota_domain"] = "proj-a"
        creds[1]["quota_domain"] = "proj-a"
        creds[2]["quota_domain"] = "proj-b"
        m = w.plan_probe_matrix("gemini", ["a"], ["K1", "K2", "K3"], [],
                                mode="SAMPLE", sample_size=2, db=db)
        keys = [k for _, k, _ in m]
        self.assertEqual(len(keys), 2)
        self.assertIn("K3", keys)  # second domain represented

    def test_collapse_prefers_ok(self):
        calls = {"n": 0}

        def fake(pid, model, key, endpoint=None):
            calls["n"] += 1
            if key == "K1":
                return "OK", "fine"
            return "RATE_LIMITED", "slow"
        orig = w.probe_model_classified
        try:
            w.probe_model_classified = fake
            res = w.test_models("gemini", ["a"], "K1", None, keys=["K1", "K2"],
                                mode="STRICT", db=None, sleep_s=0)
            self.assertEqual(res[0][1], "OK")
            self.assertEqual(calls["n"], 2)
        finally:
            w.probe_model_classified = orig


if __name__ == "__main__":
    unittest.main()
