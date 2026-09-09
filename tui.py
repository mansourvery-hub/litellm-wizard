"""Textual TUI shell for litellm-wizard (Milestone 2).

Thin presentation layer only. All product logic (provider validation,
quota math, config compilation, secret handling, OpenCode sync) lives in
``engine.py`` / ``wizard.py`` — this file must never implement any of it.

Views (intentionally few): Home -> Configure -> Provider, Home -> Test,
Home -> Review(Done). Quota/speed/routing/alias/service/opencode
internals are NOT standalone screens; they surface inside these flows
only when needed to complete the user's one job (later milestones).

Milestone 2 scope: shell + navigation, offline data only. No network
calls are made from any screen. Configure-flow validation/discovery,
testing, and apply arrive in Milestones 3-4.
"""

from __future__ import annotations

from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Static,
    TextArea,
)

import engine

STATUS_MARK = {"running": "[green]●[/]", "stopped": "[red]●[/]",
               "unknown": "[yellow]●[/]"}


# ------------------------------------------------------- pure helpers ---

def split_keys(text: str) -> list[str]:
    """Split pasted keys on whitespace/commas, preserving order, deduped."""
    seen: set[str] = set()
    out: list[str] = []
    for chunk in text.replace(",", " ").split():
        chunk = chunk.strip().strip("\"'")
        if chunk and chunk not in seen:
            seen.add(chunk)
            out.append(chunk)
    return out


def status_word(status: str) -> str:
    return {"running": "Running", "stopped": "Stopped"}.get(status, "Unknown")


def home_lines(overview: dict[str, Any]) -> str:
    """Plain-text home content (unit-testable without a running app)."""
    mark = STATUS_MARK.get(overview.get("gateway", "unknown"),
                           STATUS_MARK["unknown"])
    lines = [f"Gateway  {mark} {status_word(str(overview.get('gateway', 'unknown')))}",
             "", "Models"]
    members = overview.get("members", {})
    for pool in overview.get("pools", []):
        lines.append(f"  {pool}")
        provs = members.get(pool, [])
        if provs:
            lines.append(f"    {', '.join(provs)}")
    if not overview.get("pools"):
        lines.append("  (none yet — choose Configure to add keys)")
    if overview.get("attention"):
        lines.append("")
        lines.append("Needs attention")
        for item in overview["attention"]:
            lines.append(f"  ! {item}")
    return "\n".join(lines)


def test_lines(db: dict[str, Any]) -> str:
    """Offline pool listing for the Test screen (no probing in M2)."""
    _deps, pools, _roles, errors = engine.compile_config(db)
    lines = ["Test your setup", ""]
    if errors:
        lines.append("Configuration has problems:")
        lines.extend(f"  ! {e}" for e in errors[:5])
        return "\n".join(lines)
    if not pools:
        return "Test your setup\n\nNothing to test yet — configure a provider first."
    lines.append("Models")
    for pool in sorted(pools):
        provs = sorted({d["provider"] for d in pools[pool]})
        lines.append(f"  ? {pool}")
        lines.append(f"    {', '.join(provs)} (not tested yet)")
    lines += ["", "One-button testing arrives in Milestone 4."]
    return "\n".join(lines)


def done_lines(db: dict[str, Any]) -> str:
    """Offline apply preview for the Review screen (no writing in M2)."""
    deps, pools, roles, errors = engine.compile_config(db)
    if errors:
        return "\n".join(["Review", "",
                           "Configuration has problems:",
                           *[f"  ! {e}" for e in errors[:5]]])
    lines = ["Review", "",
             f"{len(deps)} connection(s) across {len(pools)} model(s)"
             + (f" + {len(roles)} role(s)" if roles else ""),
             ""]
    for pool in sorted(pools):
        provs = sorted({d["provider"] for d in pools[pool]})
        lines.append(f"  {pool}  ({', '.join(provs)})")
    lines += ["", "Apply (write + restart + sync) arrives in Milestone 3."]
    return "\n".join(lines)


# -------------------------------------------------------------- screens ---

class HomeScreen(Screen):
    BINDINGS = [  # noqa: RUF012 -- Textual API
        ("c", "configure", "Configure"), ("t", "test", "Test"),
        ("v", "review", "Review")]

    def __init__(self) -> None:
        super().__init__()
        self.last_content = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            yield Label("LiteLLM Wizard", id="title")
            yield Static("", id="home-content")
            yield Button("Configure", id="go-configure", variant="primary")
            yield Button("Test", id="go-test")
            yield Button("Review", id="go-review")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_content()
        app = self.app
        assert isinstance(app, WizardApp)
        app.refresh_status_background()

    def on_screen_resume(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        app = self.app
        assert isinstance(app, WizardApp)
        overview = engine.gateway_overview(app.db, app.paths, status=app.status)
        self.last_content = home_lines(overview)
        self.query_one("#home-content", Static).update(self.last_content)

    def action_configure(self) -> None:
        self.app.push_screen(ConfigureScreen())

    def action_test(self) -> None:
        self.app.push_screen(TestScreen())

    def action_review(self) -> None:
        self.app.push_screen(DoneScreen())

    @on(Button.Pressed, "#go-configure")
    def _go_configure(self) -> None:
        self.action_configure()

    @on(Button.Pressed, "#go-test")
    def _go_test(self) -> None:
        self.action_test()

    @on(Button.Pressed, "#go-review")
    def _go_review(self) -> None:
        self.action_review()


class ConfigureScreen(Screen):
    BINDINGS = [("escape", "back", "Back")]  # noqa: RUF012 -- Textual API

    def __init__(self) -> None:
        super().__init__()
        self.pids: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            yield Label("Choose provider", id="title")
            yield ListView(id="provider-list")
            yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        app = self.app
        assert isinstance(app, WizardApp)
        view = self.query_one("#provider-list", ListView)
        for p in engine.list_providers(app.db):
            self.pids.append(p["id"])
            view.append(ListItem(Label(f"{p['name']}")))
        view.focus()

    @on(ListView.Selected)
    def _provider_chosen(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self.pids):
            self.app.push_screen(ProviderScreen(self.pids[idx]))

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.action_back()


class ProviderScreen(Screen):
    BINDINGS = [("escape", "back", "Back")]  # noqa: RUF012 -- Textual API

    def __init__(self, pid: str) -> None:
        super().__init__()
        self.pid = pid
        self.last_result = ""

    def compose(self) -> ComposeResult:
        app = self.app if self.is_running else None
        name = self.pid
        if isinstance(app, WizardApp):
            prov = engine.get_provider(self.pid, app.db)
            if prov:
                name = str(prov.get("name", self.pid))
        with Vertical(id="body"):
            yield Label(f"Paste your {name} API key(s)", id="title")
            yield TextArea(id="keys-input")
            yield Button("Save keys", id="save-keys", variant="primary")
            yield Static("", id="save-result")
            yield Button("Back", id="back")
        yield Footer()

    def save_keys_from_text(self, text: str) -> str:
        """Offline save (no validation yet). Returns user-facing message."""
        app = self.app
        assert isinstance(app, WizardApp)
        secrets = split_keys(text)
        if not secrets:
            return "Paste at least one key first."
        try:
            added = engine.add_credentials(app.db, self.pid, secrets)
            engine.save_state(app.db, app.paths)
        except OSError as e:
            return f"Could not save: {e}"
        if not added:
            return "Those keys are already saved."
        n = len(added)
        return (f"Saved {n} key(s). "
                "Checking them and choosing models arrives in Milestone 3.")

    @on(Button.Pressed, "#save-keys")
    def _save(self) -> None:
        text = self.query_one("#keys-input", TextArea).text
        self.last_result = self.save_keys_from_text(text)
        self.query_one("#save-result", Static).update(self.last_result)

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.action_back()


class TestScreen(Screen):
    BINDINGS = [("escape", "back", "Back")]  # noqa: RUF012 -- Textual API

    def __init__(self) -> None:
        super().__init__()
        self.last_content = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            yield Label("Test your setup", id="title")
            yield Static("", id="test-content")
            yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_content()

    def on_screen_resume(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        app = self.app
        assert isinstance(app, WizardApp)
        self.last_content = test_lines(app.db)
        self.query_one("#test-content", Static).update(self.last_content)

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.action_back()


class DoneScreen(Screen):
    BINDINGS = [("escape", "back", "Back")]  # noqa: RUF012 -- Textual API

    def __init__(self) -> None:
        super().__init__()
        self.last_content = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            yield Label("Review", id="title")
            yield Static("", id="done-content")
            yield Button("Back to Home", id="back", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_content()

    def on_screen_resume(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        app = self.app
        assert isinstance(app, WizardApp)
        self.last_content = done_lines(app.db)
        self.query_one("#done-content", Static).update(self.last_content)

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.action_back()


# ------------------------------------------------------------------ app ---

class WizardApp(App):
    TITLE = "LiteLLM Wizard"
    CSS = """
    #body { width: 72; height: auto; margin: 1 2; }
    #title { text-style: bold; margin-bottom: 1; }
    #home-content, #test-content, #done-content { margin-bottom: 1; }
    #keys-input { height: 6; margin-bottom: 1; }
    #save-result { margin: 1 0; }
    Button { margin-bottom: 1; }
    """

    def __init__(self, paths: engine.EnginePaths | None = None,
                 status: str | None = None, status_auto_refresh: bool = True) -> None:
        super().__init__()
        self.paths = paths or engine.EnginePaths.from_env()
        self.db: dict[str, Any] = engine.load_state(self.paths)
        self.status = status or "unknown"
        # Note: named to avoid colliding with Textual's own auto_refresh.
        self.status_auto_refresh = status_auto_refresh and status is None

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())

    def refresh_status_background(self) -> None:
        if self.status_auto_refresh and self.status == "unknown":
            self.run_worker(self._query_status, thread=True, exclusive=True)

    def _query_status(self) -> None:
        status = engine.gateway_status()
        self.call_from_thread(self._apply_status, status)

    def _apply_status(self, status: str) -> None:
        self.status = status
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen):
                screen.refresh_content()


def main() -> None:
    WizardApp().run()


if __name__ == "__main__":
    main()
