"""TUI tests (Milestones 2-3). Temp dirs + fake keys only.

Network guard: wizard HTTP adapters and urlopen raise if touched — any
unmocked network access fails loudly. Milestone 3 flow tests mock at the
``wizard.validate_keys`` / ``wizard.fetch_catalog`` / ``wizard.test_models``
level so the engine delegation path is genuinely exercised.
"""
import asyncio
import tempfile
import time
import unittest
from unittest import mock

import engine
import tui
import wizard
from tui import (
    ConfigureScreen,
    DoneScreen,
    HomeScreen,
    ModelScreen,
    ProviderScreen,
    TestScreen,
    WizardApp,
    done_lines,
    home_lines,
    split_keys,
    test_lines,
)


def temp_paths():
    tmp = tempfile.mkdtemp(prefix="tuitest-")
    return engine.EnginePaths.temp(tmp)


def seed_db(paths):
    db = engine.load_state(paths)
    engine.add_credentials(db, "gemini", ["GK1-FAKE"])
    db["gemini"]["models"] = ["gemini-3.7-flash"]
    engine.save_state(db, paths)
    return db


class HelpersTest(unittest.TestCase):
    def test_split_keys(self):
        self.assertEqual(split_keys("a b,c\nd a"), ["a", "b", "c", "d"])
        self.assertEqual(split_keys("  "), [])
        self.assertEqual(split_keys("'quoted' \"q2\""), ["quoted", "q2"])

    def test_home_lines_quiet(self):
        ov = {"gateway": "running", "pools": ["gemini-3.7-flash"],
              "members": {"gemini-3.7-flash": ["gemini"]}, "attention": []}
        text = home_lines(ov)
        self.assertIn("Running", text)
        self.assertIn("gemini-3.7-flash", text)
        self.assertNotIn("Needs attention", text)

    def test_home_lines_attention(self):
        ov = {"gateway": "unknown", "pools": [], "members": {},
              "attention": ["No config.yaml yet"]}
        text = home_lines(ov)
        self.assertIn("Unknown", text)
        self.assertIn("No config.yaml yet", text)

    def test_test_and_done_lines(self):
        paths = temp_paths()
        db = seed_db(paths)
        self.assertIn("gemini-3.7-flash", test_lines(db))
        self.assertIn("1 connection(s)", done_lines(db))
        self.assertIn("Run the test", test_lines(db))
        self.assertIn("Apply writes safely", done_lines(db))


class TuiPilotTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Fail loudly on any network attempt from any screen.
        self._get, wizard._get = wizard._get, _no_network
        self._post, wizard._post = wizard._post, _no_network
        import urllib.request
        self._urlopen = urllib.request.urlopen
        urllib.request.urlopen = _no_network_urlopen

    def tearDown(self):
        wizard._get = self._get
        wizard._post = self._post
        import urllib.request
        urllib.request.urlopen = self._urlopen

    def make_app(self, seed=True):
        paths = temp_paths()
        if seed:
            seed_db(paths)
        return WizardApp(paths=paths, status="unknown", status_auto_refresh=False)

    async def test_launch_shows_home(self):
        app = self.make_app()
        async with app.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, HomeScreen)
            assert isinstance(app.screen, HomeScreen)
            self.assertIn("gemini-3.7-flash", app.screen.last_content)

    async def test_all_views_reachable(self):
        app = self.make_app()
        async with app.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfigureScreen)
            # choose the first provider (keyboard: list is focused)
            view = app.screen.query_one("#provider-list", tui.ListView)
            self.assertGreater(len(view.children), 0)
            view.index = 0
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, ProviderScreen)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfigureScreen)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, HomeScreen)
            await pilot.press("t")
            await pilot.pause()
            self.assertIsInstance(app.screen, TestScreen)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, HomeScreen)
            await pilot.press("v")
            await pilot.pause()
            self.assertIsInstance(app.screen, DoneScreen)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, HomeScreen)

    async def test_check_and_save_keys(self):
        paths = temp_paths()
        app = WizardApp(paths=paths, status="unknown", status_auto_refresh=False)
        validated = ([("OR1-FAKE", True, "fine"), ("OR2-FAKE", True, "fine")], None)
        with mock.patch.object(wizard, "validate_keys", return_value=validated):
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause()
                app.push_screen(ProviderScreen("openrouter"))
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, ProviderScreen)
                screen.query_one("#keys-input", tui.TextArea).load_text(
                    "OR1-FAKE, OR2-FAKE")
                await pilot.click("#check")
                await _wait_until(lambda: screen.phase == "results")
                self.assertIn("All 2 connection(s) work", screen.last_result)
                await pilot.click("#save-continue")
                await _wait_until(lambda: isinstance(app.screen, ModelScreen))
                # persisted + readable by the engine/CLI path
                back = engine.load_state(paths)
                self.assertEqual(len(back["openrouter"]["keys"]), 2)

    async def test_background_refresh_applies(self):
        paths = temp_paths()
        calls = []
        real = engine.gateway_status
        engine.gateway_status = lambda *a, **k: calls.append(1) or "running"
        try:
            app = WizardApp(paths=paths)  # status unknown, bg refresh on
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause()
                for _ in range(200):
                    if app.status == "running":
                        break
                    await asyncio.sleep(0.05)
                self.assertEqual(app.status, "running")
                self.assertTrue(calls)
        finally:
            engine.gateway_status = real


def _no_network(*a, **k):
    raise AssertionError("network call attempted from TUI shell")


def _no_network_urlopen(*a, **k):
    raise AssertionError("network call attempted from TUI shell")


async def _wait_until(pred, timeout=15):
    start = time.monotonic()
    while not pred():
        if time.monotonic() - start > timeout:
            raise AssertionError("timed out waiting for UI state")
        await asyncio.sleep(0.05)


FREE_CATALOG = [("free-a:free", "Free A"), ("paid-b", "Paid B")]


async def _check_keys(pilot, app, pid, text, validate):
    """Drive ProviderScreen check; returns the screen in results phase."""
    app.push_screen(ProviderScreen(pid))
    await pilot.pause()
    screen = app.screen
    assert isinstance(screen, ProviderScreen)
    screen.query_one("#keys-input", tui.TextArea).load_text(text)
    with mock.patch.object(wizard, "validate_keys", return_value=validate):
        await pilot.click("#check")
        await _wait_until(lambda: screen.phase == "results")
    return screen


class ConfigureFlowTest(unittest.IsolatedAsyncioTestCase):
    """Milestone 3: provider -> check -> grouping -> models -> probe -> apply."""

    def setUp(self):
        self._get, wizard._get = wizard._get, _no_network
        self._post, wizard._post = wizard._post, _no_network
        import urllib.request
        self._urlopen = urllib.request.urlopen
        urllib.request.urlopen = _no_network_urlopen

    def tearDown(self):
        wizard._get = self._get
        wizard._post = self._post
        import urllib.request
        urllib.request.urlopen = self._urlopen

    def make_app(self):
        paths = temp_paths()
        return WizardApp(paths=paths, status="unknown",
                         status_auto_refresh=False), paths

    async def test_single_key_skips_grouping(self):
        app, _ = self.make_app()
        validated = ([("K1-FAKE", True, "fine")], None)
        async with app.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            screen = await _check_keys(pilot, app, "gemini", "K1-FAKE", validated)
            await pilot.click("#save-continue")
            await _wait_until(lambda: isinstance(app.screen, ModelScreen))
            self.assertEqual(screen.results, [("K1-FAKE", True, "fine")])

    async def test_two_keys_grouping_question(self):
        app, paths = self.make_app()
        validated = ([("K1-FAKE", True, "fine"), ("K2-FAKE", True, "fine")], None)
        async with app.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            screen = await _check_keys(pilot, app, "gemini",
                                       "K1-FAKE K2-FAKE", validated)
            await pilot.click("#save-continue")
            await _wait_until(lambda: screen.phase == "grouping")
            await pilot.click("#share")
            await _wait_until(lambda: isinstance(app.screen, ModelScreen))
            db = engine.load_state(paths)
            creds = list(db["gemini"]["credentials"])
            self.assertEqual({c["quota_domain"] for c in creds},
                             {"project:gemini-shared"})
            self.assertTrue(db["gemini"]["quota_reviewed"])

    async def test_partial_failure_keeps_valid_only(self):
        app, paths = self.make_app()
        validated = ([("K1-FAKE", True, "fine"),
                      ("K2-FAKE", False, "bad key")], None)
        async with app.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            screen = await _check_keys(pilot, app, "openrouter",
                                       "K1-FAKE K2-FAKE", validated)
            self.assertIn("1 of 2 work", screen.last_result)
            await pilot.click("#save-continue")
            await _wait_until(lambda: isinstance(app.screen, ModelScreen))
            model_screen = app.screen
            assert isinstance(model_screen, ModelScreen)
            self.assertEqual(model_screen.secrets, ["K1-FAKE"])
            back = engine.load_state(paths)
            self.assertEqual(back["openrouter"]["keys"], ["K1-FAKE"])

    async def test_select_probe_save_review(self):
        app, paths = self.make_app()
        validated = ([("K1-FAKE", True, "fine")], None)
        probed = [("paid-b", "OK", "fine")]
        with mock.patch.object(wizard, "fetch_catalog", return_value=FREE_CATALOG), \
                mock.patch.object(wizard, "test_models", return_value=probed):
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause()
                await _check_keys(pilot, app, "openrouter", "K1-FAKE", validated)
                await pilot.click("#save-continue")
                await _wait_until(lambda: isinstance(app.screen, ModelScreen))
                screen = app.screen
                assert isinstance(screen, ModelScreen)
                await _wait_until(lambda: screen.catalog_state == "ready")
                # free-first: only the free model shown until toggled
                mlist = screen.query_one("#model-list", tui.SelectionList)
                self.assertEqual(mlist.option_count, 1)
                await pilot.click("#show-toggle")
                await pilot.pause()
                self.assertEqual(mlist.option_count, 2)
                # filter narrows
                screen.query_one("#filter", tui.Input).value = "paid"
                await pilot.pause()
                self.assertEqual(mlist.option_count, 1)
                screen.query_one("#filter", tui.Input).value = ""
                await pilot.pause()
                mlist.select("paid-b")
                await pilot.click("#probe")
                await _wait_until(lambda: screen.catalog_state == "probed")
                self.assertIn("[OK] paid-b", screen.last_status)
                await pilot.click("#keep-passing")
                await _wait_until(lambda: isinstance(app.screen, DoneScreen))
                back = engine.load_state(paths)
                self.assertEqual(back["openrouter"]["models"], ["paid-b"])

    async def test_probe_blocks_failures(self):
        app, paths = self.make_app()
        validated = ([("K1-FAKE", True, "fine")], None)
        probed = [("free-a:free", "OK", "fine"),
                  ("paid-b", "AUTH_ERROR", "bad key")]
        with mock.patch.object(wizard, "fetch_catalog", return_value=FREE_CATALOG), \
                mock.patch.object(wizard, "test_models", return_value=probed):
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause()
                await _check_keys(pilot, app, "openrouter", "K1-FAKE", validated)
                await pilot.click("#save-continue")
                await _wait_until(lambda: isinstance(app.screen, ModelScreen))
                screen = app.screen
                assert isinstance(screen, ModelScreen)
                await _wait_until(lambda: screen.catalog_state == "ready")
                await pilot.click("#show-toggle")
                await pilot.pause()
                mlist = screen.query_one("#model-list", tui.SelectionList)
                mlist.select("free-a:free")
                mlist.select("paid-b")
                await pilot.click("#probe")
                await _wait_until(lambda: screen.catalog_state == "probed")
                self.assertIn("[FAIL] paid-b", screen.last_status)
                await pilot.click("#keep-passing")
                await _wait_until(lambda: isinstance(app.screen, DoneScreen))
                back = engine.load_state(paths)
                self.assertEqual(back["openrouter"]["models"], ["free-a:free"])

    async def test_manual_models_fallback(self):
        app, paths = self.make_app()
        validated = ([("K1-FAKE", True, "fine")], None)
        probed = [("custom-m1", "OK", "fine")]
        with mock.patch.object(wizard, "fetch_catalog", return_value=None), \
                mock.patch.object(wizard, "test_models", return_value=probed):
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause()
                await _check_keys(pilot, app, "openrouter", "K1-FAKE", validated)
                await pilot.click("#save-continue")
                await _wait_until(lambda: isinstance(app.screen, ModelScreen))
                screen = app.screen
                assert isinstance(screen, ModelScreen)
                await _wait_until(lambda: screen.catalog_state == "failed")
                screen.query_one("#manual-input", tui.TextArea).load_text(
                    "custom-m1")
                await pilot.click("#manual-use")
                await _wait_until(lambda: screen.catalog_state == "probed")
                await pilot.click("#keep-passing")
                await _wait_until(lambda: isinstance(app.screen, DoneScreen))
                back = engine.load_state(paths)
                self.assertEqual(back["openrouter"]["models"], ["custom-m1"])

    async def test_retired_models_noticed(self):
        app, _ = self.make_app()
        db = engine.load_state(app.paths)
        engine.add_credentials(db, "openrouter", ["K1-FAKE"])
        engine.set_models(db, "openrouter", ["old-retired"])
        engine.save_state(db, app.paths)
        app.db = engine.load_state(app.paths)
        validated = ([("K1-FAKE", True, "fine")], None)
        with mock.patch.object(wizard, "fetch_catalog", return_value=FREE_CATALOG):
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause()
                await _check_keys(pilot, app, "openrouter", "K1-FAKE", validated)
                await pilot.click("#save-continue")
                await _wait_until(lambda: isinstance(app.screen, ModelScreen))
                screen = app.screen
                assert isinstance(screen, ModelScreen)
                await _wait_until(lambda: screen.catalog_state == "ready")
                self.assertIn("old-retired", screen.last_status)

    async def test_apply_success_then_sync_offer(self):
        app, paths = self.make_app()
        db = engine.load_state(paths)
        engine.add_credentials(db, "gemini", ["GK1-FAKE"])
        engine.set_models(db, "gemini", ["gemini-3.7-flash"])
        engine.save_state(db, paths)
        app.db = engine.load_state(paths)
        with mock.patch.object(engine, "restart_gateway", return_value=True):
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause()
                app.push_screen(DoneScreen())
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, DoneScreen)
                await pilot.click("#apply")
                await _wait_until(lambda: screen.applied_ok)
                self.assertIn("Gateway working", screen.last_status)
                self.assertIn("out of sync", screen.last_status)
                # OpenCode file absent: sync failure reported separately
                await pilot.click("#sync")
                await pilot.pause()
                self.assertIn("gateway itself is working", screen.last_status)

    async def test_apply_restart_failure(self):
        app, paths = self.make_app()
        db = engine.load_state(paths)
        engine.add_credentials(db, "gemini", ["GK1-FAKE"])
        engine.set_models(db, "gemini", ["gemini-3.7-flash"])
        engine.save_state(db, paths)
        app.db = engine.load_state(paths)
        with mock.patch.object(engine, "restart_gateway", return_value=False):
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause()
                app.push_screen(DoneScreen())
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, DoneScreen)
                await pilot.click("#apply")
                await _wait_until(lambda: "not ready" in screen.last_status)
                self.assertFalse(screen.applied_ok)


class CombiningTest(unittest.IsolatedAsyncioTestCase):
    """Milestone 4: automatic combining across providers + suggested pools."""

    def setUp(self):
        self._get, wizard._get = wizard._get, _no_network
        self._post, wizard._post = wizard._post, _no_network
        import urllib.request
        self._urlopen = urllib.request.urlopen
        urllib.request.urlopen = _no_network_urlopen

    def tearDown(self):
        wizard._get = self._get
        wizard._post = self._post
        import urllib.request
        urllib.request.urlopen = self._urlopen

    def make_app(self):
        paths = temp_paths()
        return WizardApp(paths=paths, status="unknown",
                         status_auto_refresh=False), paths

    async def test_two_providers_auto_combine(self):
        app, paths = self.make_app()
        db = engine.load_state(paths)
        engine.add_credentials(db, "gemini", ["GK1-FAKE"])
        engine.set_models(db, "gemini", ["gemini-3.7-flash"])
        engine.save_state(db, paths)
        app.db = engine.load_state(paths)
        validated = ([("OR1-FAKE", True, "fine")], None)
        catalog = [("gemini-3.7-flash", "Gemini Flash")]
        probed = [("gemini-3.7-flash", "OK", "fine")]
        with mock.patch.object(wizard, "validate_keys", return_value=validated), \
                mock.patch.object(wizard, "fetch_catalog", return_value=catalog), \
                mock.patch.object(wizard, "test_models", return_value=probed):
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause()
                await _check_keys(pilot, app, "openrouter",
                                  "OR1-FAKE", validated)
                await pilot.click("#save-continue")
                await _wait_until(lambda: isinstance(app.screen, ModelScreen))
                model = app.screen
                assert isinstance(model, ModelScreen)
                await _wait_until(lambda: model.catalog_state == "ready")
                model.query_one("#model-list", tui.SelectionList).select(
                    "gemini-3.7-flash")
                await pilot.click("#probe")
                await _wait_until(lambda: model.catalog_state == "probed")
                await pilot.click("#keep-passing")
                await _wait_until(lambda: isinstance(app.screen, DoneScreen))
                done = app.screen
                assert isinstance(done, DoneScreen)
                self.assertIn("multiple providers", done.last_status)
                back = engine.load_state(paths)
                members = back["_aliases"]["gemini-3.7-flash"]
                self.assertEqual({m["provider"] for m in members},
                                 {"gemini", "openrouter"})
                _deps, pools, _, errors = engine.compile_config(back)
                self.assertEqual(errors, [])
                self.assertEqual(len(pools["gemini-3.7-flash"]), 2)

    async def test_suggested_pool_grouped_on_review(self):
        app, paths = self.make_app()
        db = engine.load_state(paths)
        engine.add_credentials(db, "openrouter", ["OR1-FAKE"])
        engine.add_credentials(db, "zai", ["Z1-FAKE"])
        engine.set_models(db, "openrouter", ["mimo-v2.5-free"])
        engine.set_models(db, "zai", ["mimo-v2.5-free"])
        engine.save_state(db, paths)
        app.db = engine.load_state(paths)
        async with app.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            app.push_screen(DoneScreen())
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, DoneScreen)
            self.assertIn("mimo-v2.5", screen.last_content)
            await pilot.click("#group-suggested")
            await pilot.pause()
            self.assertIn("Grouped mimo-v2.5", screen.last_status)
            back = engine.load_state(paths)
            self.assertIn("mimo-v2.5", back["_aliases"])
            self.assertFalse(screen.applied_ok)


class GatewayTestScreenTest(unittest.IsolatedAsyncioTestCase):
    """Milestone 4: simple default test, diagnose, park failing connections."""

    def setUp(self):
        self._get, wizard._get = wizard._get, _no_network
        self._post, wizard._post = wizard._post, _no_network
        import urllib.request
        self._urlopen = urllib.request.urlopen
        urllib.request.urlopen = _no_network_urlopen

    def tearDown(self):
        wizard._get = self._get
        wizard._post = self._post
        import urllib.request
        urllib.request.urlopen = self._urlopen

    def make_seeded_app(self):
        paths = temp_paths()
        db = engine.load_state(paths)
        engine.add_credentials(db, "gemini", ["GK1-FAKE"])
        engine.set_models(db, "gemini", ["gemini-3.7-flash"])
        engine.save_state(db, paths)
        engine.write_config(db, paths)
        app = WizardApp(paths=paths, status="unknown",
                        status_auto_refresh=False)
        app.db = engine.load_state(paths)
        return app, paths

    async def test_run_all_ok(self):
        app, _ = self.make_seeded_app()
        with mock.patch.object(engine, "probe_gateway_alias",
                               return_value=("OK", "choices OK")):
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause()
                app.push_screen(TestScreen())
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, TestScreen)
                await pilot.click("#run-test")
                await _wait_until(lambda: screen.phase == "done")
                self.assertIn("All models answer", screen.last_status)
                self.assertIn("1 OK", screen.last_results)

    async def test_run_failure_diagnose_park(self):
        app, paths = self.make_seeded_app()
        with mock.patch.object(engine, "probe_gateway_alias",
                               return_value=("AUTH_ERROR", "HTTP 401: bad key")), \
                mock.patch.object(engine, "probe_model",
                                  return_value=("AUTH_ERROR", "bad key")):
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause()
                app.push_screen(TestScreen())
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, TestScreen)
                await pilot.click("#run-test")
                await _wait_until(lambda: screen.phase == "done")
                self.assertIn("failing", screen.last_status)
                await pilot.click("#diagnose")
                await _wait_until(lambda: screen.phase == "diagnosed")
                self.assertTrue(screen._parkable())
                await pilot.click("#park-fixes")
                await pilot.pause()
                self.assertIn("Parked 1 connection", screen.last_status)
                back = engine.load_state(paths)
                creds = list(back["gemini"]["credentials"])
                self.assertTrue(all(c.get("quarantined") for c in creds))
                await pilot.click("#review")
                await pilot.pause()
                self.assertIsInstance(app.screen, DoneScreen)


if __name__ == "__main__":
    unittest.main()
