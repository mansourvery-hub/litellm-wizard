#!/usr/bin/env python3
"""Sync LiteLLM gateway aliases into OpenCode CLI config.

Reads the model aliases your LiteLLM gateway serves (from the wizard's
config.yaml, falling back to providers_db.json) and writes them as a
"litellm" provider block into opencode.json — so every gateway model shows
up in OpenCode's /models picker as litellm/<alias>.

- Stdlib only (works with system python3, no venv needed).
- Backs up opencode.json before touching it.
- Tolerates JSONC (comments + trailing commas) in opencode.json.
- Idempotent: re-running just refreshes the litellm block.
- Contains NO secrets: auth uses the {env:LITELLM_MASTER_KEY} placeholder.

Usage:
    python3 sync-opencode.py [--dry-run] [--print]
    python3 sync-opencode.py --opencode ~/.config/opencode/opencode.json \\
        --litellm-config ~/.config/litellm/config.yaml
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys

LITELLM_DIR = os.path.join(os.path.expanduser("~"), ".config", "litellm")
OPENCODE_JSON = os.path.join(os.path.expanduser("~"), ".config", "opencode", "opencode.json")
GATEWAY_URL = "http://localhost:4000/v1"


def _strip_jsonc(text):
    """Remove // and /* */ comments (outside strings) + trailing commas."""
    out, i, n, in_str, esc = [], 0, len(text), False, False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            esc = (c == "\\" and not esc)
            if c == '"' and not esc:
                in_str = False
            elif c != "\\":
                esc = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    cleaned = "".join(out)
    return re.sub(r",(\s*[}\]])", r"\1", cleaned)


def load_aliases(litellm_config):
    """Unique LiteLLM model aliases, in config order."""
    if litellm_config and os.path.exists(litellm_config):
        # Fast path: regex over model_name lines (no PyYAML needed).
        seen, aliases = set(), []
        with open(litellm_config) as f:
            for line in f:
                m = re.match(r"\s*model_name:\s*(\S+)\s*$", line)
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    aliases.append(m.group(1))
        if aliases:
            return aliases, f"config ({litellm_config})"
        try:
            import yaml  # type: ignore
            with open(litellm_config) as f:
                cfg = yaml.safe_load(f) or {}
            seen, aliases = set(), []
            for item in cfg.get("model_list", []):
                a = item.get("model_name")
                if a and a not in seen:
                    seen.add(a)
                    aliases.append(a)
            if aliases:
                return aliases, f"config ({litellm_config})"
        except ImportError:
            pass  # fall through to providers_db.json
    db_path = os.path.join(LITELLM_DIR, "providers_db.json")
    with open(db_path) as f:
        db = json.load(f)
    pids = ["gemini", "openrouter", "ollama_cloud", "zai", "tokenrouter",
            "anthropic", "openai", "opencode_zen"]
    pids += sorted(k for k in db if k.startswith("custom_") and k not in pids)
    seen, aliases = set(), []
    for pid in pids:
        for m in db.get(pid, {}).get("models", []):
            # LiteLLM alias convention: last segment (matches model_name in config.yaml)
            a = m.split("/")[-1] if "/" in m else m
            if a not in seen:
                seen.add(a)
                aliases.append(a)
    return aliases, f"provider DB ({db_path})"


def main():
    ap = argparse.ArgumentParser(description="Sync LiteLLM aliases into opencode.json")
    ap.add_argument("--opencode", default=OPENCODE_JSON)
    ap.add_argument("--litellm-config", default=os.path.join(LITELLM_DIR, "config.yaml"))
    ap.add_argument("--dry-run", action="store_true", help="print block, change nothing")
    ap.add_argument("--print", action="store_true", help="print resulting litellm block")
    args = ap.parse_args()

    aliases, source = load_aliases(args.litellm_config)
    if not aliases:
        print("[!] No gateway aliases found. Run the wizard (litellm-add) first.")
        sys.exit(1)
    print(f"[*] {len(aliases)} gateway aliases from {source}")

    block = {
        "npm": "@ai-sdk/openai-compatible",
        "name": "Local LiteLLM",
        "options": {
            "baseURL": GATEWAY_URL,
            "apiKey": "{env:LITELLM_MASTER_KEY}"
        },
        "models": {a: {"name": a} for a in aliases},
    }

    if args.dry_run:
        print(json.dumps({"litellm": block}, indent=2))
        return

    if not os.path.exists(args.opencode):
        print(f"[!] Not found: {args.opencode}")
        sys.exit(1)
    with open(args.opencode) as f:
        raw = f.read()
    try:
        cfg = json.loads(_strip_jsonc(raw))
    except json.JSONDecodeError as e:
        print(f"[!] Could not parse {args.opencode}: {e}")
        sys.exit(1)

    cfg.setdefault("provider", {})["litellm"] = block
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{args.opencode}.bak-{stamp}"
    shutil.copy2(args.opencode, backup)
    with open(args.opencode, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    # strict-JSON sanity check on what we just wrote
    json.load(open(args.opencode))
    print(f"[+] Backup: {backup}")
    print(f"[+] Wrote litellm block with {len(aliases)} models to {args.opencode}")
    if args.print:
        print(json.dumps({"litellm": block}, indent=2))
    print("[*] Restart the OpenCode TUI, then /models -> litellm/<alias>.")


if __name__ == "__main__":
    main()
