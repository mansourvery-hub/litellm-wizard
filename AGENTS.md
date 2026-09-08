# Agent Instructions for `litellm-wizard`

This document provides context, conventions, and operational workflows for AI coding agents working in this repository.

---

## 1. Project Architecture & Structure

`litellm-wizard` is a CLI setup and configuration manager for LiteLLM gateway (`localhost:4000`).

- **`wizard.py`**: The interactive CLI wizard (Python 3.10+). Handles direct API key validation, live model catalog fetching, per-model completion pings, config generation (`config.yaml`), and database persistence (`providers_db.json`).
- **`sync-opencode.py`**: Stdlib-only script that syncs LiteLLM gateway aliases into OpenCode's `opencode.json` configuration file as a `litellm` provider block.
- **`litellm.service`**: Systemd user service definition running LiteLLM proxy on port 4000.
- **`README.md`**: User-facing setup guide and jargon buster.

---

## 2. Environment & Testing Guidelines

### Python Environment
- Python executable for running the wizard locally: `~/.config/litellm/venv/bin/python`
- System Python (`python3`) can run `sync-opencode.py` directly (stdlib only).

### Testing Code Changes Safely
Never modify real user configs or databases during testing. Use environment variable overrides:

```bash
LITELLM_DB_FILE=/tmp/test_db.json \
LITELLM_YAML_FILE=/tmp/test_config.yaml \
OPENCODE_JSON=/tmp/test_opencode.json \
~/.config/litellm/venv/bin/python wizard.py
```

### Syntax & Compilation Checks
Before completing changes, verify Python syntax:
```bash
python3 -m py_compile wizard.py sync-opencode.py
```

### Syncing the Installed Copy
The live `litellm-add` command runs from `~/.config/litellm/wizard.py` — a copy of this repo's
`wizard.py`. **At the end of every change to `wizard.py`, always copy it over** and keep the
exec bit:

```bash
cp wizard.py ~/.config/litellm/wizard.py
chmod +x ~/.config/litellm/wizard.py
```

Never run the wizard from the repo copy directly — it must match the installed version so
`litellm-add` actually uses the fixed code.

---

## 3. Versioning & Conventions

- Version banner `__version__` is maintained in `wizard.py`.
- Increment `__version__` when introducing user-facing features or major bug fixes.
- Keep `providers_db.json` and `config.yaml` out of git commits (`.gitignore`).
- Preserve stdlib-first design principles for `sync-opencode.py` so it works without extra pip packages.

---

## 4. Git & GitHub (`gh`) Usage Workflows

### Authentication & Cloning
- Use GitHub CLI (`gh`) for authentication and cloning on new environments:
  ```bash
  gh auth login
  gh repo clone mansourvery-hub/litellm-wizard
  ```

### Commit Conventions
- Keep commit messages concise, structured, and consistent with repo history.
- Format: `Wizard vX.Y.Z: short description of changes` or `sync-opencode: short description`.
- Examples:
  - `Wizard v1.7.0: prevent short-alias hijacking; offer custom endpoint creation on unknown add`
  - `sync-opencode: include custom_* providers; regex config parse`

### Inspection & Shipping Checklist
Before committing and pushing (`shipping`):
1. Run `git status` to verify modified and untracked files.
2. Run `git diff` to inspect exact code modifications.
3. Verify recent commit messages using `git log -n 5 --oneline`.
4. Stage only intended files (`git add wizard.py README.md AGENTS.md`).
5. Commit and push (`git commit -m "..." && git push origin main`).
