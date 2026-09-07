# Local LLM Gateway (LiteLLM + setup wizard)

Run many AI providers (Google Gemini, OpenRouter, Z.AI, TokenRouter, Ollama…)
through **one address on your own machine**: `http://localhost:4000`.
Any tool that speaks the OpenAI format (OpenCode, Cline, plain scripts) can use it —
you configure keys **once**, and every tool shares them.

> **Jargon buster (read this first, it makes everything below click)**
> - **Terminal / shell** — the black window where you type commands. On KDE, open it with `Ctrl+Alt+T`. Your shell is called `zsh`.
> - **LiteLLM** — a free program that pretends to be OpenAI, but secretly forwards your request to whichever real provider you configured. A *gateway*.
> - **Provider** — a company/service that sells or gives away AI access (Google, OpenRouter…).
> - **API key** — a long password a provider gives *you* so programs can use your account. Never share or upload keys.
> - **`~`** — shortcut for your home folder (`/home/yourname`). `~/.config/litellm` = a settings folder inside it.
> - **`venv`** — an isolated box holding the Python programs for this project, so they don't fight with system programs.
> - **`systemd` service** — a background task Linux starts automatically (here: on login) and restarts if it crashes.

---

## 1. What you need

- Linux (these steps were tested on Arch Linux; any distro works — only the install command in step 2 changes).
- Python 3.10+ (`python3 --version` to check).
- At least **one** provider API key, e.g.:
  - Google AI Studio key → https://aistudio.google.com/apikey (free tier)
  - OpenRouter key → https://openrouter.ai/keys (has free models)
  - You can add more providers later — the wizard loops, nothing is final.

## 2. Install the pieces

```bash
# 1) Python tools (Arch; on Ubuntu use: sudo apt install python3 python3-venv curl git)
sudo pacman -S --needed python python-virtualenv curl git github-cli

# 2) Folders
mkdir -p ~/.config/litellm ~/.config/systemd/user

# 3) Copy wizard.py and litellm.service from this repo into place:
cp wizard.py ~/.config/litellm/wizard.py
cp litellm.service ~/.config/systemd/user/litellm.service

# 4) Isolated Python box + install LiteLLM inside it (takes a few minutes)
python3 -m venv ~/.config/litellm/venv
~/.config/litellm/venv/bin/pip install -U pip litellm pyyaml requests

# 5) Shortcut so you can launch the wizard by typing one word
echo "alias litellm-add='~/.config/litellm/venv/bin/python ~/.config/litellm/wizard.py'" >> ~/.zshrc
source ~/.zshrc
```

## 3. Change the default password (do this!)

Everything on your machine talks to the gateway using a password called the **master key**.
It ships as `sk-litellm-local-secret`. Since it's public in this repo, pick your own:

```bash
# 1) Open the wizard and change the MASTER_KEY = "..." line near the top
nano ~/.config/litellm/wizard.py
# 2) Save (Ctrl+O, Enter, Ctrl+X) and remember the value — you'll need it twice below
```

> The wizard writes this value into its generated config automatically, so you only change it in this one place.

## 4. Start the gateway automatically

```bash
systemctl --user daemon-reload
systemctl --user enable --now litellm.service
# Keep it running even when you log out (laptops can skip this):
loginctl enable-linger "$USER"
# Check it's alive:
systemctl --user status litellm.service --no-pager | head -n 8
```

`enable` = start on every login. If it ever crashes, systemd restarts it after 3 seconds.

## 5. Add your providers with the wizard (the easy part)

```bash
litellm-add
```

What happens, step by step:

1. You see only what you've already configured (empty at first). Type `add google`
   (names are fuzzy: `google`, `openrouter`, `claude`, `gpt`, `zen`, `zai`, `glm`…).
   `all` shows every provider; a bare number (`5`) works too.
2. **Paste your API keys**, then an empty line / `DONE`.
   The wizard **tests every key directly against the real provider right then**.
   You only move on when all keys pass (`R` retry, `K` keep valid ones, `S` save anyway, `A` abort).
3. **Pick models from the live catalog** — no guessing IDs, no Google searches.
   Free models are shown first; type `MORE` for the paid rest, `/text` to filter,
   numbers or names to select, `DONE` when happy.
4. The wizard **test-calls each model** (a 1-word ping, ~3 tokens — costs essentially nothing).
   Broken/retired models are blocked before they can pollute your config.
5. Repeat for more providers. **Q** saves everything, rebuilds the config, restarts the gateway, quits. No tests run on quit.

Your secrets land in `~/.config/litellm/providers_db.json` and `config.yaml`
(locked to `chmod 600`, readable only by you). **Never upload these two files anywhere.**

## 6. Let your terminal know the password

```bash
echo 'export LITELLM_MASTER_KEY=put-your-master-key-here' >> ~/.zshrc
source ~/.zshrc   # or close + reopen the terminal
```

Same idea as step 3's box-and-label: any program you start from the terminal can now read the gateway password from `$LITELLM_MASTER_KEY` without it being pasted into files.

## 7. Prove it works (cheap checks)

```bash
# List everything the gateway currently serves:
curl -s http://localhost:4000/v1/models \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | python3 -m json.tool | grep '"id"'

# One 1-token ping through the whole chain (costs ~3 tokens):
curl -s http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"model":"gemini-3.5-flash-lite","messages":[{"role":"user","content":"hi"}],"max_tokens":1}'
# HTTP 200 with a "choices" block = working. HTTP 429 = rate-limited, wait a minute.
```

## 8. Use it in OpenCode CLI

OpenCode treats the gateway as one more provider. In `~/.config/opencode/opencode.json`,
inside the existing `"provider"` section, add:

```json
"litellm": {
  "npm": "@ai-sdk/openai-compatible",
  "name": "Local LiteLLM",
  "options": {
    "baseURL": "http://localhost:4000/v1",
    "apiKey": "{env:LITELLM_MASTER_KEY}"
  },
  "models": {
    "gemini-3.5-flash-lite": { "name": "Gemini 3.5 Flash Lite (local)" },
    "minimax-m3:free": { "name": "MiniMax M3 free (local)" }
  }
}
```

List only the aliases you actually use — each becomes `litellm/<alias>` in OpenCode's
`/models` picker. Notes:

- The **model that answers is chosen by you** (`litellm/<alias>`). The gateway's
  *routing rule* only picks *which key* serves it (least-used key first, 60s rest
  after failures, 3 retries) — that's already configured, nothing to do.
- Keep provider-native models where they belong: OpenCode Zen free models
  (`opencode/...`) work **only** inside OpenCode (they need its session protocol),
  so don't route those through LiteLLM.

## 9. Use it with anything else

Any OpenAI-compatible tool just needs two values:

```bash
export OPENAI_BASE_URL=http://localhost:4000/v1
export OPENAI_API_KEY="$LITELLM_MASTER_KEY"
```

## 10. Daily use & troubleshooting

| Situation | Command |
|---|---|
| Add/change providers | `litellm-add` (Q applies + restarts) |
| Is it running? | `systemctl --user status litellm.service --no-pager` |
| What broke? | `journalctl --user -u litellm.service -n 50 --no-pager` |
| Apply config by hand | `systemctl --user restart litellm.service` |
| Full sweep after edits | `~/.config/litellm/venv/bin/python ~/.config/litellm/test_all_models.py` (optional helper, not in this repo) |
| HTTP 429 | Free-tier throttle — wait out the 60s cooldown, it recovers |
| HTTP 401/403 | Wrong/expired provider key — re-run wizard for that provider |
| HTTP 500 + `Connection error` | Provider unreachable or bad base URL — check logs |

## 11. Files in this repo

| File | What | Secrets? |
|---|---|---|
| `wizard.py` | The setup wizard (key tests, live model catalog, per-model tests, config generator) | No — only the default master-key placeholder |
| `litellm.service` | systemd unit that runs the gateway on port 4000 | No |
| `README.md` | This guide | No |

`providers_db.json` and `config.yaml` are **deliberately absent** — they hold your real keys.
