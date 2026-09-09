"""TUI shell tests (Milestone 2). Temp dirs + fake keys only, no network.

Network guard: wizard HTTP adapters and urlopen raise if touched — any
screen that performs I/O beyond local files fails loudly.
"""
import asyncio
import tempfile
import unittest

import engine
import tui
import wizard
from tui import (
    ConfigureScreen,
    DoneScreen,
    HomeScreen,
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
        self.assertIn("not tested yet", test_lines(db))
        self.assertIn("Milestone", test_lines(db))
        self.assertIn("Milestone", done_lines(db))


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
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, HomeScreen)
            assert isinstance(app.screen, HomeScreen)
            self.assertIn("gemini-3.7-flash", app.screen.last_content)

    async def test_all_views_reachable(self):
        app = self.make_app()
        async with app.run_test() as pilot:
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

    async def test_save_keys_offline(self):
        paths = temp_paths()
        app = WizardApp(paths=paths, status="unknown", status_auto_refresh=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ProviderScreen("openrouter"))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ProviderScreen)
            screen.query_one("#keys-input", tui.TextArea).load_text(
                "OR1-FAKE, OR2-FAKE")
            await pilot.click("#save-keys")
            await pilot.pause()
            self.assertIn("Saved 2 key(s)", screen.last_result)
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
            async with app.run_test() as pilot:
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


if __name__ == "__main__":
    unittest.main()
