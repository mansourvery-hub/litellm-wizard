#!/home/mohamed/.config/litellm/venv/bin/python
"""Single unified LiteLLM config wizard: keys -> validated -> models -> loop -> proxy test."""
__version__ = "1.5.0"
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import yaml
except ImportError:
    # Re-exec with the bundled venv python (sibling ./venv) when launched
    # via system python (e.g. `./wizard.py` with /usr/bin/env python3).
    _venv_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "python")
    if os.path.exists(_venv_py) and os.path.abspath(sys.executable) != os.path.abspath(_venv_py):
        os.execv(_venv_py, [_venv_py, os.path.abspath(__file__)] + sys.argv[1:])
    print("[!] PyYAML missing. Run with: ~/.config/litellm/venv/bin/python ~/.config/litellm/wizard.py")
    sys.exit(1)

CONFIG_DIR = os.path.expanduser("~/.config/litellm")
# Env overrides for safe testing (e.g. LITELLM_DB_FILE=/tmp/test_db.json)
DB_FILE = os.environ.get("LITELLM_DB_FILE", os.path.join(CONFIG_DIR, "providers_db.json"))
YAML_FILE = os.environ.get("LITELLM_YAML_FILE", os.path.join(CONFIG_DIR, "config.yaml"))
MASTER_KEY = "sk-litellm-local-secret"
PROXY_URL = "http://localhost:4000"
TIMEOUT = 15
UA = {"User-Agent": "litellm-wizard/1.0", "Accept": "application/json"}

PROVIDERS = {
    "1": {"id": "gemini", "name": "Google Gemini (AI Studio)", "prefix": "gemini/", "type": "api"},
    "2": {"id": "openrouter", "name": "OpenRouter", "prefix": "openrouter/", "type": "api"},
    "3": {"id": "anthropic", "name": "Anthropic (Claude)", "prefix": "anthropic/", "type": "api"},
    "4": {"id": "openai", "name": "OpenAI (GPT/o3)", "prefix": "openai/", "type": "api"},
    "5": {"id": "opencode_zen", "name": "OpenCode Zen", "prefix": "openai/", "base_url": "https://opencode.ai/zen/v1", "type": "custom_api"},
    "6": {"id": "tokenrouter", "name": "TokenRouter", "prefix": "openai/", "base_url": "https://api.tokenrouter.com/v1", "type": "custom_api"},
    "7": {"id": "zai", "name": "Z.AI (GLM Models)", "prefix": "openai/", "base_url": "https://api.z.ai/api/paas/v4", "type": "custom_api"},
    "8": {"id": "ollama_cloud", "name": "Ollama Cloud / Hosted Remote", "prefix": "ollama/", "type": "remote_ollama"},
    "9": {"id": "ollama_local", "name": "Local Ollama Engine", "prefix": "ollama/", "type": "local_ollama"},
    "10": {"id": "custom", "name": "Custom OpenAI-compatible endpoint", "prefix": "openai/", "type": "custom_api"},
}

MODEL_HINTS = {
    "opencode_zen": "Examples: big-pickle, mimo-v2.5-free, nemotron-3-ultra-free (bare IDs, no opencode/ prefix)",
    "tokenrouter": "Console: tokenrouter.com/console/token, base https://api.tokenrouter.com/v1 (sk-... keys). api.tokenrouter.io (tr_... keys) also accepted — wizard auto-detects.",
    "zai": "Examples: glm-4.7-flash, glm-5.3 (check /models for current IDs)",
    "ollama_cloud": "Examples: gemma4:31b, nemotron-3-ultra, gpt-oss:120b",
    "ollama_local": "Examples: qwen2.5-coder:7b, llama3.3:70b",
    "gemini": "Examples: gemini-3.8-flash, gemini-3.5-flash, gemini-3.1-pro-preview",
    "openrouter": "Examples: minimax/minimax-m3:free, nvidia/nemotron-3.5-lightning:free",
    "custom": "Pick from the live catalog fetched from YOUR base URL — no guessing needed",
}


def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {}


def save_db(data):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)


def snippet(k):
    k = str(k)
    return f"...{k[-6:]}" if len(k) > 6 else k


def generate_yaml(db_data):
    model_list = []

    for provider_id, pdata in db_data.items():
        p_info = next((p for p in PROVIDERS.values() if p["id"] == provider_id), None)
        if not p_info and (provider_id == "custom" or provider_id.startswith("custom_")):
            if not pdata.get("base_url"):
                continue  # custom entry without endpoint: nothing to route
            p_info = {"id": provider_id, "name": pdata.get("label", provider_id),
                      "prefix": "openai/", "type": "custom_api"}
        if not p_info:
            continue

        p_type = p_info.get("type")
        prefix = p_info.get("prefix", "")
        keys = pdata.get("keys", [])
        models = pdata.get("models", [])
        endpoints = pdata.get("endpoints", [])

        if p_type == "local_ollama":
            for endpoint in endpoints:
                for m in models:
                    model_list.append({
                        "model_name": m,
                        "litellm_params": {
                            "model": f"ollama/{m}",
                            "api_base": endpoint
                        }
                    })

        elif p_type == "remote_ollama":
            for endpoint in endpoints:
                for m in models:
                    for k in keys:
                        model_list.append({
                            "model_name": m,
                            "litellm_params": {
                                "model": f"ollama/{m}",
                                "api_base": endpoint,
                                "api_key": k
                            }
                        })

        elif p_type == "custom_api":
            # Per-entry base_url override wins (e.g. TokenRouter .com vs .io auto-detect,
            # custom providers always store their own base_url)
            base_url = pdata.get("base_url") or p_info.get("base_url")
            if not base_url:
                continue
            for m in models:
                if provider_id == "tokenrouter" or provider_id == "custom" or provider_id.startswith("custom_"):
                    mid, alias = m, (m.split("/")[-1] if "/" in m else m)
                else:
                    # Strip opencode/ prefix for Zen: upstream expects bare ID
                    bare = m.split("/")[-1] if "/" in m else m
                    mid, alias = bare, bare
                for k in keys:
                    model_list.append({
                        "model_name": alias,
                        "litellm_params": {
                            "model": f"openai/{mid}",
                            "api_base": base_url,
                            "api_key": k
                        }
                    })

        elif p_type == "api":
            for m in models:
                alias = m.split("/")[-1] if "/" in m else m
                full_model_path = f"{prefix}{m}" if not m.startswith(prefix) else m

                for k in keys:
                    entry = {
                        "model_name": alias,
                        "litellm_params": {
                            "model": full_model_path,
                            "api_key": k
                        }
                    }
                    if provider_id == "gemini":
                        entry["litellm_params"]["rpm"] = 15
                    model_list.append(entry)

    config = {
        "model_list": model_list,
        "router_settings": {
            "routing_strategy": "usage-based-routing-v2",
            "num_retries": 3,
            "cooldown_time": 60
        },
        "general_settings": {
            "master_key": MASTER_KEY
        }
    }

    with open(YAML_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

    return len(model_list)


# ---------- direct provider validation (no proxy needed) ----------

def _get(url, headers=None, timeout=TIMEOUT):
    """GET JSON. Returns (status:int|None, data:dict|None, raw:str)."""
    h = dict(UA)
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raw), raw[:500]
            except Exception:
                return r.status, None, raw[:500]
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode(errors="replace")
        except Exception:
            raw = str(e)
        try:
            return e.code, json.loads(raw), raw[:500]
        except Exception:
            return e.code, None, raw[:500]
    except Exception as e:
        return None, None, str(e)[:300]


def _post(url, payload, headers=None, timeout=30):
    """POST JSON. Returns (status:int|None, data:dict|None, raw:str)."""
    h = dict(UA)
    h.update(headers or {})
    h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raw), raw[:800]
            except Exception:
                return r.status, None, raw[:800]
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode(errors="replace")
        except Exception:
            raw = str(e)
        low = raw.lower()
        tag = "RATELIMIT" if (e.code == 429 or '"code":429' in raw or '"code": 429' in low) else ""
        try:
            return e.code, json.loads(raw), (tag + " " + raw[:600]).strip()
        except Exception:
            return e.code, None, (tag + " " + raw[:600]).strip()
    except Exception as e:
        return None, None, str(e)[:300]


def _gemini_models(key):
    s, d, raw = _get(f"https://generativelanguage.googleapis.com/v1beta/models?key={urllib.parse.quote(key)}")
    if s == 200 and d and "models" in d:
        ids = [m.get("name", "").replace("models/", "") for m in d["models"]]
        return True, f"OK ({len(ids)} models visible)", ids
    if s == 400 and "API key not valid" in raw:
        return False, "invalid key (400 API key not valid)", None
    if s in (401, 403):
        return False, f"rejected (HTTP {s}): {raw[:120]}", None
    return False, f"HTTP {s}: {raw[:150]}", None


def _openrouter_key(key):
    s, d, raw = _get("https://openrouter.ai/api/v1/auth/key",
                     {"Authorization": f"Bearer {key}"})
    if s == 200:
        return True, "OK (key valid)", None
    return False, f"HTTP {s}: {raw[:150]}", None


def _openrouter_models(key):
    s, d, raw = _get("https://openrouter.ai/api/v1/models",
                     {"Authorization": f"Bearer {key}"})
    if s == 200 and d and "data" in d:
        return [m.get("id") for m in d["data"] if m.get("id")]
    return None


def _anthropic_key(key):
    s, d, raw = _get("https://api.anthropic.com/v1/models",
                     {"x-api-key": key, "anthropic-version": "2023-06-01"})
    if s == 200 and d and "data" in d:
        return True, f"OK ({len(d['data'])} models)", [m.get("id") for m in d["data"]]
    if s in (401, 403):
        return False, f"invalid key (HTTP {s})", None
    return False, f"HTTP {s}: {raw[:150]}", None


def _openai_key(key):
    s, d, raw = _get("https://api.openai.com/v1/models",
                     {"Authorization": f"Bearer {key}"})
    if s == 200 and d and "data" in d:
        return True, f"OK ({len(d['data'])} models)", [m.get("id") for m in d["data"]]
    if s == 401:
        return False, "invalid key (401 incorrect API key)", None
    return False, f"HTTP {s}: {raw[:150]}", None


def _zen_key(key):
    s, d, raw = _get("https://opencode.ai/zen/v1/models",
                     {"Authorization": f"Bearer {key}"})
    if s == 200 and d and "data" in d:
        return True, f"OK ({len(d['data'])} models)", [m.get("id") for m in d["data"]]
    if s in (401, 403):
        return False, f"rejected (HTTP {s}): {raw[:150]}", None
    return False, f"HTTP {s}: {raw[:150]}", None


TOKENROUTER_BASES = ["https://api.tokenrouter.com/v1", "https://api.tokenrouter.io/v1"]


def _tokenrouter_key(key):
    # Two live endpoints: tokenrouter.com (sk-... keys, official console) and
    # tokenrouter.io v2 (tr_... keys). Try .com first, fall back to .io.
    # Returns (ok, msg, ids|None, base|None).
    last = None
    for base in TOKENROUTER_BASES:
        s, d, raw = _get(f"{base}/models", {"Authorization": f"Bearer {key}"})
        if s == 200 and d:
            ids = [m.get("id") for m in d.get("data", [])] if isinstance(d.get("data"), list) else None
            return True, f"OK @ {base} ({len(ids) if ids else '?'} models)", ids, base
        last = (s, raw, base)
    s, raw, base = last
    if s == 401:
        return False, f"invalid key (401 @ both .com and .io) — check key at tokenrouter.com/console/token", None, None
    return False, f"HTTP {s}: {raw[:150]}", None, None


def _zai_key(key):
    s, d, raw = _get("https://api.z.ai/api/paas/v4/models",
                     {"Authorization": f"Bearer {key}"})
    if s == 200 and d and "data" in d:
        return True, f"OK ({len(d['data'])} models)", [m.get("id") for m in d["data"]]
    if s in (401, 403):
        return False, f"invalid key (HTTP {s})", None
    return False, f"HTTP {s}: {raw[:150]}", None


def _ollama_cloud_ep_key(endpoint, key):
    base = endpoint.rstrip("/")
    s, d, raw = _get(f"{base}/api/tags", {"Authorization": f"Bearer {key}"})
    if s == 200 and d and "models" in d:
        return True, f"OK ({len(d['models'])} models)", [m.get("name") for m in d["models"]]
    if s in (401, 403):
        return False, f"invalid key (HTTP {s})", None
    return False, f"HTTP {s}: {raw[:150]}", None


def _ollama_local_ep(endpoint):
    base = endpoint.rstrip("/")
    s, d, raw = _get(f"{base}/api/tags")
    if s == 200 and d and "models" in d:
        names = [m.get("name") for m in d["models"]]
        return True, f"OK reachable ({len(names)} local models)", names
    if s is None:
        return False, f"unreachable: {raw[:150]}", None
    return False, f"HTTP {s}: {raw[:150]}", None


def _oai_compat_models(base, key):
    """Generic OpenAI-compatible GET {base}/models. Returns (ok, msg, [(id,label)]|None)."""
    s, d, raw = _get(f"{base.rstrip('/')}/models", {"Authorization": f"Bearer {key}"})
    if s == 200 and d and "data" in d:
        items = sorted([(m["id"], m.get("name") or m["id"]) for m in d["data"] if m.get("id")])
        return True, f"OK @ {base.rstrip('/')} ({len(items)} models)", items
    if s in (401, 403):
        return False, f"invalid key (HTTP {s})", None
    return False, f"HTTP {s}: {raw[:150]}", None


def validate_keys(pid, keys, endpoints):
    """Test every key directly. Returns (results, available_models|None).

    results = [(key, ok, msg), ...]
    """
    results = []
    avail = None
    print(f"  [*] Testing {len(keys)} key(s) directly against provider...")
    if pid == "gemini":
        for k in keys:
            ok, msg, ids = _gemini_models(k)
            if ids and avail is None:
                avail = ids
            print(f"      [{'OK' if ok else 'FAIL'}] {snippet(k)} -> {msg}")
            results.append((k, ok, msg))
    elif pid == "openrouter":
        # one models fetch for hint, per-key auth check
        if keys:
            avail = _openrouter_models(keys[0])
        for k in keys:
            ok, msg, _ = _openrouter_key(k)
            print(f"      [{'OK' if ok else 'FAIL'}] {snippet(k)} -> {msg}")
            results.append((k, ok, msg))
    elif pid == "anthropic":
        for k in keys:
            ok, msg, ids = _anthropic_key(k)
            if ids and avail is None:
                avail = ids
            print(f"      [{'OK' if ok else 'FAIL'}] {snippet(k)} -> {msg}")
            results.append((k, ok, msg))
    elif pid == "openai":
        for k in keys:
            ok, msg, ids = _openai_key(k)
            if ids and avail is None:
                avail = ids
            print(f"      [{'OK' if ok else 'FAIL'}] {snippet(k)} -> {msg}")
            results.append((k, ok, msg))
    elif pid == "opencode_zen":
        for k in keys:
            ok, msg, ids = _zen_key(k)
            if ids and avail is None:
                avail = ids
            print(f"      [{'OK' if ok else 'FAIL'}] {snippet(k)} -> {msg}")
            results.append((k, ok, msg))
    elif pid == "tokenrouter":
        for k in keys:
            ok, msg, ids, base = _tokenrouter_key(k)
            if ids and avail is None:
                avail = ids
            print(f"      [{'OK' if ok else 'FAIL'}] {snippet(k)} -> {msg}")
            results.append((k, ok, msg))
    elif pid == "zai":
        for k in keys:
            ok, msg, ids = _zai_key(k)
            if ids and avail is None:
                avail = ids
            print(f"      [{'OK' if ok else 'FAIL'}] {snippet(k)} -> {msg}")
            results.append((k, ok, msg))
    elif pid == "ollama_cloud":
        ep = (endpoints or ["https://ollama.com"])[0]
        for k in keys:
            ok, msg, ids = _ollama_cloud_ep_key(ep, k)
            if ids and avail is None:
                avail = ids
            print(f"      [{'OK' if ok else 'FAIL'}] {snippet(k)} -> {msg}")
            results.append((k, ok, msg))
    elif pid == "ollama_local":
        pass  # endpoint validated separately
    elif pid == "custom" or pid.startswith("custom_"):
        base = (endpoints or [None])[0]
        if not base:
            print("      [FAIL] no base URL stored for custom provider")
            return [(k, False, "no base URL") for k in keys], None
        for k in keys:
            ok, msg, items = _oai_compat_models(base, k)
            if items and avail is None:
                avail = [mid for mid, _ in items]
            print(f"      [{'OK' if ok else 'FAIL'}] {snippet(k)} -> {msg}")
            results.append((k, ok, msg))
    return results, avail


# ---------- online model catalog (no more guessing IDs) ----------

def fetch_catalog(pid, key, endpoint=None):
    """Fetch live model list -> [(id, label), ...] or None on failure."""
    try:
        if pid == "gemini":
            s, d, _ = _get(f"https://generativelanguage.googleapis.com/v1beta/models?key={urllib.parse.quote(key)}")
            if s != 200 or not d or "models" not in d:
                return None
            out = []
            for m in d["models"]:
                if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                    continue
                mid = m.get("name", "").replace("models/", "")
                if mid:
                    out.append((mid, m.get("displayName") or mid))
            return sorted(out)
        if pid == "openrouter":
            s, d, _ = _get("https://openrouter.ai/api/v1/models",
                           {"Authorization": f"Bearer {key}"})
            if s != 200 or not d or "data" not in d:
                # public fallback (no auth)
                s, d, _ = _get("https://openrouter.ai/api/v1/models")
                if s != 200 or not d or "data" not in d:
                    return None
            return sorted([(m["id"], m.get("name") or m["id"]) for m in d["data"] if m.get("id")])
        if pid == "anthropic":
            s, d, _ = _get("https://api.anthropic.com/v1/models",
                           {"x-api-key": key, "anthropic-version": "2023-06-01"})
            if s != 200 or not d or "data" not in d:
                return None
            return sorted([(m["id"], m.get("display_name") or m["id"]) for m in d["data"] if m.get("id")])
        if pid == "openai":
            s, d, _ = _get("https://api.openai.com/v1/models",
                           {"Authorization": f"Bearer {key}"})
            if s != 200 or not d or "data" not in d:
                return None
            return sorted([(m["id"], m["id"]) for m in d["data"] if m.get("id")])
        if pid == "opencode_zen":
            s, d, _ = _get("https://opencode.ai/zen/v1/models",
                           {"Authorization": f"Bearer {key}"})
            if s != 200 or not d or "data" not in d:
                return None
            return sorted([(m["id"], m["id"]) for m in d["data"] if m.get("id")])
        if pid == "tokenrouter":
            bases = [endpoint] if endpoint else TOKENROUTER_BASES
            for base in bases:
                s, d, _ = _get(f"{base.rstrip('/')}/models",
                               {"Authorization": f"Bearer {key}"})
                if s == 200 and d and isinstance(d.get("data"), list):
                    return sorted([(m["id"], m.get("name") or m["id"]) for m in d["data"] if m.get("id")])
            return None
        if pid == "custom" or pid.startswith("custom_"):
            if not endpoint:
                return None
            ok, _, items = _oai_compat_models(endpoint, key)
            return items if ok else None
        if pid == "zai":
            s, d, _ = _get("https://api.z.ai/api/paas/v4/models",
                           {"Authorization": f"Bearer {key}"})
            if s != 200 or not d or "data" not in d:
                return None
            return sorted([(m["id"], m["id"]) for m in d["data"] if m.get("id")])
        if pid == "ollama_cloud":
            base = (endpoint or "https://ollama.com").rstrip("/")
            s, d, _ = _get(f"{base}/api/tags", {"Authorization": f"Bearer {key}"})
            if s != 200 or not d or "models" not in d:
                return None
            return sorted([(m["name"], m["name"]) for m in d["models"] if m.get("name")])
        if pid == "ollama_local":
            base = (endpoint or "http://localhost:11434").rstrip("/")
            s, d, _ = _get(f"{base}/api/tags")
            if s != 200 or not d or "models" not in d:
                return None
            return sorted([(m["name"], m["name"]) for m in d["models"] if m.get("name")])
    except Exception:
        return None
    return None


def _norm(s):
    return s.strip().lower().replace("_", "-").replace(" ", "-").replace("--", "-")


def _is_free_model(mid, label=""):
    """Heuristic: free-tier models (OpenRouter :free, Zen free IDs, etc.)."""
    m = (mid or "").lower()
    return "free" in m or m == "big-pickle" or "free" in (label or "").lower()


def pick_models(pid, pname, existing, catalog):
    """Interactive picker over live catalog. Returns list of model IDs (bare)."""
    print(f"\nModels for {pname} — live catalog ({len(catalog)} available, no guessing needed).")
    if existing:
        print(f"Already saved: {' '.join(existing)}")
    print("How to select: numbers/ranges (1,3,5-8) | /filter text | exact IDs | display names | ALL | DONE to finish | CLEAR to reset")
    # alias map: normalized id + normalized label -> id
    alias = {}
    for mid, label in catalog:
        alias[_norm(mid)] = mid
        alias[_norm(label)] = mid
        alias[_norm(mid.split("/")[-1])] = mid
    chosen = []
    free_only = [item for item in catalog if _is_free_model(item[0], item[1])]
    if free_only and len(free_only) < len(catalog):
        view = free_only
        expanded = False
        print(f"  [FREE] showing {len(free_only)} free models first — type MORE for all {len(catalog)}")
    else:
        view = list(catalog)
        expanded = True
    page, per = 0, 30
    while True:
        total_pages = max(1, (len(view) + per - 1) // per)
        page = max(0, min(page, total_pages - 1))
        chunk = view[page * per:(page + 1) * per]
        scope = "free" if not expanded else "all"
        print(f"\n--- catalog ({scope}) page {page + 1}/{total_pages} ({len(view)} shown) ---")
        for i, (mid, label) in enumerate(chunk, start=page * per + 1):
            mark = "*" if mid in chosen or mid in (existing or []) else " "
            extra = f"  [{label}]" if label != mid else ""
            print(f"  {mark}[{i:3d}] {mid}{extra[:80]}")
        if len(view) > per:
            print("  [N]ext [P]rev")
        if not expanded:
            print("  [MORE] show all models")
        try:
            raw = input(f"Select (chosen={len(chosen)}, DONE to finish): ").strip()
        except EOFError:
            break
        if not raw:
            continue
        low = raw.lower()
        if low in ("done", "d", "q", "finish"):
            break
        if low == "clear":
            chosen = []
            continue
        if low in ("more", "paid", "rest", "show all", "showall"):
            view = list(catalog)
            expanded = True
            page = 0
            continue
        if low in ("n", "next") and len(view) > per:
            page += 1
            continue
        if low in ("p", "prev") and len(view) > per:
            page -= 1
            continue
        if low == "all":
            for mid, _ in view:
                if mid not in chosen:
                    chosen.append(mid)
            print(f"  [+] selected all {len(view)} in view")
            continue
        if raw.startswith("/"):
            q = _norm(raw[1:])
            view = [(m, l) for m, l in catalog if q in _norm(m) or q in _norm(l)]
            page = 0
            if not view:
                print("  [!] filter matched nothing — showing full catalog")
                view = list(catalog)
            continue
        # token stream: numbers, ranges, IDs, or multi-word display names
        # ("1 Nemotron 3.5 Lightning Free" -> #1 + display-name match)
        ok_any = False
        words = raw.replace(",", " ").split()
        nums, rest = [], []
        for tok in words:
            if tok.isdigit() or ("-" in tok and tok.replace("-", "").isdigit()):
                nums.append(tok)
            else:
                rest.append(tok)
        for tok in nums:
            if "-" in tok:
                try:
                    a, b = tok.split("-", 1)
                    for n in range(int(a), int(b) + 1):
                        if 1 <= n <= len(view):
                            mid = view[n - 1][0]
                            if mid not in chosen:
                                chosen.append(mid)
                            ok_any = True
                        else:
                            print(f"  [!] #{n} out of range")
                except Exception:
                    pass
            else:
                n = int(tok)
                if 1 <= n <= len(view):
                    mid = view[n - 1][0]
                    if mid not in chosen:
                        chosen.append(mid)
                    ok_any = True
                else:
                    print(f"  [!] #{tok} out of range")
        # greedy longest-phrase match for display names ("Nemotron 3.5 Lightning Free")
        i = 0
        while i < len(rest):
            matched = None
            for j in range(min(len(rest), i + 6), i, -1):
                phrase = " ".join(rest[i:j])
                t = phrase.split("/")[-1] if phrase.startswith("opencode/") else phrase
                mid = alias.get(_norm(t))
                if mid:
                    matched = (mid, j)
                    break
            if matched:
                mid, j = matched
                if mid not in chosen:
                    chosen.append(mid)
                ok_any = True
                i = j
            else:
                # strip opencode/ prefix automatically
                t = rest[i].split("/")[-1] if rest[i].startswith("opencode/") else rest[i]
                mid = alias.get(_norm(t))
                if mid:
                    if mid not in chosen:
                        chosen.append(mid)
                    ok_any = True
                else:
                    # unknown manual ID — allow (new/preview), warn
                    if t not in chosen:
                        chosen.append(t)
                    print(f"  [?] '{rest[i]}' not in catalog — kept as manual ID (check spelling)")
                    ok_any = True
                i += 1
        if ok_any:
            print(f"  [+] chosen now ({len(chosen)}): {' '.join(chosen[-8:])}" + (" ..." if len(chosen) > 8 else ""))
    return chosen


# ---------- per-model direct test (not just keys) ----------

def test_single_model(pid, model, key, endpoint=None):
    """Direct minimal completion. Returns (status, msg): OK | RATELIMIT | FAIL."""
    try:
        if pid == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(key)}"
            s, d, raw = _post(url, {"contents": [{"parts": [{"text": "hi"}]}],
                                    "generationConfig": {"maxOutputTokens": 1}})
        elif pid == "openrouter":
            s, d, raw = _post("https://openrouter.ai/api/v1/chat/completions",
                              {"model": model, "messages": [{"role": "user", "content": "hi"}],
                               "max_tokens": 1},
                              {"Authorization": f"Bearer {key}",
                               "HTTP-Referer": "http://localhost", "X-Title": "litellm-wizard"})
        elif pid == "anthropic":
            s, d, raw = _post("https://api.anthropic.com/v1/messages",
                              {"model": model, "max_tokens": 1,
                               "messages": [{"role": "user", "content": "hi"}]},
                              {"x-api-key": key, "anthropic-version": "2023-06-01"})
        elif pid == "openai":
            s, d, raw = _post("https://api.openai.com/v1/chat/completions",
                              {"model": model, "messages": [{"role": "user", "content": "hi"}],
                               "max_tokens": 1},
                              {"Authorization": f"Bearer {key}"})
        elif pid in ("opencode_zen", "tokenrouter", "zai") or pid == "custom" or pid.startswith("custom_"):
            if pid == "tokenrouter":
                bases = [endpoint] if endpoint else TOKENROUTER_BASES
                mid = model  # .com needs full prefixed ID (z-ai/...)
                to = 90  # cold starts are slow
            elif pid == "custom" or pid.startswith("custom_"):
                if not endpoint:
                    return "FAIL", "no base URL stored for custom provider"
                bases = [endpoint]
                mid = model  # keep full ID from catalog
                to = 60
            else:
                bases = [{"opencode_zen": "https://opencode.ai/zen/v1",
                          "zai": "https://api.z.ai/api/paas/v4"}[pid]]
                mid = model.split("/")[-1]  # bare ID
                to = 30
            bare = mid
            s, d, raw = None, None, ""
            for base in bases:
                s, d, raw = _post(f"{base.rstrip('/')}/chat/completions",
                                  {"model": mid, "messages": [{"role": "user", "content": "hi"}],
                                   "max_tokens": 1},
                                  {"Authorization": f"Bearer {key}"}, timeout=to)
                if s != 401:
                    break
        elif pid in ("ollama_cloud", "ollama_local"):
            base = (endpoint or ("https://ollama.com" if pid == "ollama_cloud" else "http://localhost:11434")).rstrip("/")
            hdr = {"Authorization": f"Bearer {key}"} if pid == "ollama_cloud" else {}
            s, d, raw = _post(f"{base}/api/chat",
                              {"model": model, "messages": [{"role": "user", "content": "hi"}],
                               "stream": False, "options": {"num_predict": 1}}, hdr, timeout=60)
        else:
            return "FAIL", "unknown provider"
    except Exception as e:
        return "FAIL", str(e)[:150]
    if s == 200:
        return "OK", "completion OK"
    if s == 429 or (isinstance(raw, str) and raw.startswith("RATELIMIT")):
        return "RATELIMIT", "valid but throttled (429) — kept"
    low = (raw or "")[:200].replace("\n", " ")
    hint = ""
    if "not supported" in low and pid == "opencode_zen":
        hint = " (Responses-only or OpenCode-client-only free tier)"
    if "MissingSessionID" in (raw or ""):
        hint = " (free tier works only inside OpenCode client)"
    if s in (401, 403):
        return "FAIL", f"HTTP {s}: {low[:140]}{hint}"
    if s == 404:
        return "FAIL", f"model not found (404): {low[:140]}"
    return "FAIL", f"HTTP {s}: {low[:150]}{hint}"


def test_models(pid, models, key, endpoint=None):
    print(f"  [*] Testing {len(models)} model(s) directly (tiny ping, first key {snippet(key)})...")
    results = []
    for m in models:
        st, msg = test_single_model(pid, m, key, endpoint)
        print(f"      [{'OK' if st == 'OK' else ('WAIT' if st == 'RATELIMIT' else 'FAIL')}] {m} -> {msg}")
        results.append((m, st, msg))
        time.sleep(1.5)
    return results


# ---------- input helpers ----------

def input_keys(prompt_name, existing=None):
    """Collect keys. Returns (added, removed): new key strings + existing keys to drop."""
    existing = list(existing or [])
    print(f"\nEnter API keys for {prompt_name} (paste, space/comma/newline separated).")
    if existing:
        print(f"  Current keys ({len(existing)}): " +
              ", ".join(f"[{i}] {snippet(k)}" for i, k in enumerate(existing, 1)))
        print("  Type REMOVE to delete some first.")
    print("Type DONE on an empty line when finished (empty = keep existing).")
    lines, removed = [], []
    while True:
        try:
            line = input().strip()
        except EOFError:
            break
        if line.upper() == "REMOVE" and existing:
            try:
                nums = input("  Numbers to delete (e.g. 1,3 — empty cancels): ").strip()
            except EOFError:
                continue
            if not nums:
                continue
            for tok in nums.replace(",", " ").split():
                if tok.isdigit() and 1 <= int(tok) <= len(existing):
                    k = existing[int(tok) - 1]
                    if k not in removed:
                        removed.append(k)
                        print(f"  [-] will delete {snippet(k)}")
                else:
                    print(f"  [!] #{tok} out of range")
            remaining = [k for k in existing if k not in removed]
            print(f"  Remaining: {', '.join(snippet(k) for k in remaining) or '(none)'}")
            continue
        if line.upper() == "DONE" or (not line and lines):
            break
        if not line and not lines:
            return [], removed  # keep existing (minus removals)
        if line:
            lines.append(line)
    added = [k.strip() for k in " ".join(lines).replace(",", " ").split() if k.strip()]
    return added, removed


def input_models_manual(pid, pname, existing):
    print(f"\nModels for {pname} (manual entry — catalog unreachable).")
    if pid in MODEL_HINTS:
        print(MODEL_HINTS[pid])
    elif pid == "custom" or pid.startswith("custom_"):
        print(MODEL_HINTS["custom"])
    if existing:
        print(f"Current: {' '.join(existing)}")
    print("Empty = keep current. 'CLEAR' = replace all.")
    try:
        raw = input("Models: ").strip()
    except EOFError:
        return existing
    if not raw:
        return existing
    if raw.upper() == "CLEAR":
        return []
    return [t.split("/")[-1] if t.startswith("opencode/") else t
            for t in raw.replace(",", " ").split() if t.strip()]


def check_models_against_available(models, available):
    if not available or not models:
        return
    avail_set = set(available)
    # normalize gemini models/ prefix
    norm_avail = {a.replace("models/", "") for a in avail_set}
    unknown = [m for m in models if m not in avail_set and m.split("/")[-1] not in norm_avail and m not in norm_avail]
    if unknown:
        print(f"  [!] These models were NOT in the provider list: {' '.join(unknown)}")
        print(f"      (may be new/preview IDs — allowed, but double-check spelling)")


# ---------- per-provider flow with validation gate ----------

def _custom_id(name):
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "endpoint"
    return f"custom_{slug}"


def create_custom_provider(db):
    """Prompt for name + base URL of an OpenAI-compatible endpoint.

    Returns a dynamic provider dict (or None on abort). The entry is created
    in db immediately so keys/models/steps below have somewhere to live.
    """
    print("\nCustom OpenAI-compatible endpoint (DeepSeek direct, Groq, Mistral, xAI, Together, ...)")
    print("Works with any API shaped like OpenAI: GET {base}/models + POST {base}/chat/completions.")
    try:
        name = input("Short name (e.g. DeepSeek direct): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not name:
        print("  [!] Name required.")
        return None
    pid = _custom_id(name)
    cur_base = db.get(pid, {}).get("base_url", "")
    if cur_base:
        print(f"  Current base URL: {cur_base}")
    try:
        base = input("Base URL (e.g. https://api.deepseek.com/v1, empty=keep): ").strip().rstrip("/")
    except (EOFError, KeyboardInterrupt):
        return None
    if not base:
        base = cur_base
    if not base:
        print("  [!] Base URL required.")
        return None
    if pid not in db:
        db[pid] = {"keys": [], "models": [], "endpoints": []}
    db[pid]["base_url"] = base
    db[pid]["label"] = name
    save_db(db)
    print(f"  [+] Endpoint: {base}")
    return {"id": pid, "name": f"{name} (custom)", "prefix": "openai/",
            "base_url": base, "type": "custom_api"}


def _custom_entries(db):
    """(pid, label) for user-created custom endpoints, sorted."""
    out = []
    for pid, entry in db.items():
        if pid == "custom" or pid.startswith("custom_"):
            out.append((pid, entry.get("label") or pid))
    return sorted(out)


def configure_provider(db, provider):
    pid = provider["id"]
    pname = provider["name"]
    ptype = provider["type"]
    if pid not in db:
        db[pid] = {"keys": [], "models": [], "endpoints": []}
    entry = db[pid]
    saved_snapshot = json.loads(json.dumps(entry))  # for abort

    # --- step 1: endpoints ---
    if ptype == "local_ollama":
        cur = entry.get("endpoints", [])
        if cur:
            print(f"  Current endpoint(s): {' '.join(cur)}")
        try:
            ep = input(f"Local Ollama URL (default http://localhost:11434, empty=keep): ").strip()
        except EOFError:
            return False
        endpoints = cur if not ep else [ep]
        if endpoints != cur:
            entry["endpoints"] = endpoints
        # GATE: endpoint must be reachable before models step
        while True:
            ok, msg, avail = _ollama_local_ep(endpoints[0])
            print(f"  [{'OK' if ok else 'FAIL'}] {endpoints[0]} -> {msg}")
            if ok:
                catalog = [(n, n) for n in (avail or [])]
                return _models_step(db, provider, avail, catalog, [], endpoints)
            nxt = input("  Endpoint unreachable. [R]etry / [C]hange / [A]bort? ").strip().lower()
            if nxt == "c":
                try:
                    ep = input("Local Ollama URL: ").strip()
                except EOFError:
                    db[pid] = saved_snapshot
                    return False
                if ep:
                    endpoints = [ep]
                    entry["endpoints"] = endpoints
            elif nxt == "a":
                db[pid] = saved_snapshot
                print("  [-] Aborted, nothing saved.")
                return False
            # r = retest loop

    if ptype == "remote_ollama":
        cur = entry.get("endpoints", [])
        if cur:
            print(f"  Current endpoint(s): {' '.join(cur)}")
        try:
            ep = input("Remote Ollama base URL (empty=keep): ").strip()
        except EOFError:
            return False
        if ep and ep not in entry["endpoints"]:
            entry["endpoints"] = [ep]
        if not entry["endpoints"]:
            print("  [!] No endpoint set, aborting.")
            db[pid] = saved_snapshot
            return False

    # --- step 2: keys + GATE (all must pass before models step) ---
    if ptype not in ("local_ollama",):
        pending_new, pending_rm = input_keys(pname, entry.get("keys", []))
        # candidate set = existing minus removals + new (dedup, preserve order)
        candidate = [k for k in entry.get("keys", []) if k not in pending_rm]
        if pending_rm:
            print(f"  [-] Dropped {len(pending_rm)} key(s).")
        for k in pending_new:
            if k not in candidate:
                candidate.append(k)
        if not candidate:
            print("  [!] No keys (existing or new). Aborting provider.")
            db[pid] = saved_snapshot
            return False
        if pid == "custom" or pid.startswith("custom_"):
            if not entry.get("base_url") and not provider.get("base_url"):
                try:
                    b = input("Base URL (e.g. https://api.provider.com/v1): ").strip().rstrip("/")
                except (EOFError, KeyboardInterrupt):
                    db[pid] = saved_snapshot
                    return False
                if not b:
                    print("  [!] Base URL required for custom providers.")
                    db[pid] = saved_snapshot
                    return False
                entry["base_url"] = b
            elif provider.get("base_url"):
                entry["base_url"] = provider["base_url"]
            endpoints = [entry["base_url"]]
        else:
            endpoints = entry.get("endpoints", [])
        while True:
            results, avail = validate_keys(pid, candidate, endpoints)
            bad = [r for r in results if not r[1]]
            if not bad:
                entry["keys"] = candidate
                print("  [+] All keys valid — proceeding to models.")
                break
            print(f"  [!] {len(bad)}/{len(results)} key(s) FAILED. Next step blocked until all OK.")
            print("  Options: [R]e-enter keys  [K]eep only valid  [S]ave anyway (not recommended)  [A]bort (discard changes)")
            try:
                ch = input("  Choice [R/K/S/A]: ").strip().lower()
            except EOFError:
                db[pid] = saved_snapshot
                return False
            if ch == "k":
                candidate = [k for (k, ok, _) in results if ok]
                if not candidate:
                    print("  [!] No valid keys left, aborting.")
                    db[pid] = saved_snapshot
                    return False
                entry["keys"] = candidate
                print("  [+] Kept valid keys only — proceeding to models.")
                break
            elif ch == "a":
                db[pid] = saved_snapshot
                print("  [-] Aborted, nothing saved.")
                return False
            elif ch == "s":
                entry["keys"] = candidate
                print("  [!] Saved anyway with failing keys — expect proxy 401s for this provider.")
                break
            else:  # re-enter
                pending_new, pending_rm = input_keys(pname + " (retry)", entry.get("keys", []))
                candidate = [k for k in entry.get("keys", []) if k not in pending_rm]
                for k in pending_new:
                    if k not in candidate:
                        candidate.append(k)
                if not candidate:
                    candidate = [k for (k, ok, _) in results if ok]
                if not candidate:
                    print("  [!] Still no keys, aborting.")
                    db[pid] = saved_snapshot
                    return False
        # live catalog for picker (rich labels); fallback to key-validation ids
        ep0 = (entry.get("endpoints", []) or [None])[0]
        if pid == "tokenrouter" and entry.get("keys"):
            _, _, _, tr_base = _tokenrouter_key(entry["keys"][0])
            if tr_base:
                entry["base_url"] = tr_base
                print(f"  [+] TokenRouter endpoint: {tr_base}")
                ep0 = tr_base
        if (pid == "custom" or pid.startswith("custom_")) and entry.get("base_url"):
            ep0 = entry["base_url"]
        catalog = fetch_catalog(pid, entry["keys"][0], ep0) if entry.get("keys") else None
        if not catalog and avail:
            catalog = [(m, m) for m in avail]
        if (pid == "custom" or pid.startswith("custom_")) and entry.get("base_url"):
            step_ep = [entry["base_url"]]
        else:
            step_ep = [entry["base_url"]] if pid == "tokenrouter" and entry.get("base_url") else entry.get("endpoints", [])
        return _models_step(db, provider, avail, catalog, entry.get("keys", []),
                            step_ep)
    return False


def _models_step(db, provider, avail, catalog, keys, endpoints):
    pid = provider["id"]
    entry = db[pid]
    if catalog:
        picked = pick_models(pid, provider["name"], entry.get("models", []), catalog)
        if not picked and entry.get("models"):
            picked = list(entry["models"])  # DONE with nothing new = keep
        candidate = []
        for m in list(entry.get("models", [])) + picked:
            if m not in candidate:
                candidate.append(m)
    else:
        merged = input_models_manual(pid, provider["name"], entry.get("models", []))
        if merged == []:
            entry["models"] = []
            save_db(db)
            print(f"\n[+] Cleared models for {provider['name']}.")
            return True
        candidate = []
        for m in list(entry.get("models", [])) + merged:
            if m not in candidate:
                candidate.append(m)
    if not candidate:
        print("  [!] No models selected.")
        return False
    # GATE: per-model direct test (first valid key) before saving
    test_key = (keys or [None])[0]
    test_ep = (endpoints or [None])[0] if endpoints else None
    if test_key or pid == "ollama_local":
        while True:
            results = test_models(pid, candidate, test_key, test_ep)
            fails = [r for r in results if r[1] == "FAIL"]
            waits = [r for r in results if r[1] == "RATELIMIT"]
            if waits:
                print(f"  [~] {len(waits)} throttled (429) but valid — kept: {' '.join(m for m, _, _ in waits)}")
            if not fails:
                print("  [+] All models passed (or throttled-but-valid) — saving.")
                break
            print(f"  [!] {len(fails)}/{len(results)} model(s) FAILED. Save blocked.")
            print("  Options: [K]eep passing only  [R]e-pick  [A]bort (discard model changes)")
            try:
                ch = input("  Choice [K/R/A]: ").strip().lower()
            except EOFError:
                return False
            if ch == "k":
                candidate = [m for (m, st, _) in results if st in ("OK", "RATELIMIT")]
                if not candidate:
                    print("  [!] Nothing passing left.")
                    return False
                break
            elif ch == "a":
                print("  [-] Aborted, models unchanged.")
                return False
            else:
                if catalog:
                    candidate = pick_models(pid, provider["name"], [], catalog)
                    if not candidate:
                        return False
                else:
                    candidate = input_models_manual(pid, provider["name"], [])
                    if not candidate:
                        return False
    else:
        check_models_against_available(candidate, avail)
    entry["models"] = candidate
    save_db(db)
    n = generate_yaml(db)
    print(f"\n[+] Saved {provider['name']}: {len(entry.get('keys', []))} key(s), "
          f"{len(entry.get('models', []))} model(s). Total routes: {n}")
    return True


# ---------- proxy smoke test (everything, via local gateway) ----------

def proxy_smoke_test():
    if not os.path.exists(YAML_FILE):
        print("[!] No config.yaml, skipping proxy test.")
        return
    with open(YAML_FILE) as f:
        cfg = yaml.safe_load(f) or {}
    model_list = cfg.get("model_list", [])
    seen, uniq = set(), []
    for item in model_list:
        a = item.get("model_name")
        if a not in seen:
            seen.add(a)
            uniq.append(item)
    print(f"\n[*] Proxy smoke test: {len(uniq)} unique aliases ({len(model_list)} routes) via {PROXY_URL}")
    passed, failed = 0, 0
    for i, item in enumerate(uniq, 1):
        alias = item.get("model_name")
        payload = json.dumps({"model": alias, "messages": [{"role": "user", "content": "hi"}],
                              "max_tokens": 1}).encode()
        req = urllib.request.Request(
            f"{PROXY_URL}/v1/chat/completions", data=payload,
            headers={"Authorization": f"Bearer {MASTER_KEY}", "Content-Type": "application/json",
                     "User-Agent": "litellm-wizard/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                body = json.loads(r.read().decode(errors="replace"))
                # accept 200 even if thinking model returns null content
                if r.status == 200 and "choices" in body:
                    print(f"  [{i:02d}/{len(uniq):02d}] [OK] {alias}")
                    passed += 1
                else:
                    print(f"  [{i:02d}/{len(uniq):02d}] [FAIL] {alias} -> unexpected body")
                    failed += 1
        except urllib.error.HTTPError as e:
            try:
                raw = e.read().decode(errors="replace")
                msg = json.loads(raw).get("error", {}).get("message", raw)[:120].replace("\n", " ")
            except Exception:
                msg = raw[:120] if "raw" in dir() else str(e)[:120]
            print(f"  [{i:02d}/{len(uniq):02d}] [FAIL] {alias} -> HTTP {e.code}: {msg}")
            failed += 1
        except Exception as e:
            print(f"  [{i:02d}/{len(uniq):02d}] [ERR] {alias} -> {str(e)[:120]}")
            failed += 1
        time.sleep(2.0)
    print(f"\n  === Proxy result: {passed} OK | {failed} FAIL | {len(uniq)} aliases ===")


def restart_proxy():
    try:
        subprocess.run(["systemctl", "--user", "restart", "litellm"], check=True)
        print("[+] Restarted litellm service.")
        time.sleep(6)
        return True
    except Exception as e:
        print(f"[!] Restart failed: {e}")
        return False


def _is_configured(entry):
    return bool(entry.get("keys") or entry.get("models") or entry.get("endpoints"))


def _status_line(num, p, e):
    models = ' '.join(e.get('models', [])[:4])
    more = f" +{len(e['models']) - 4} more" if len(e.get('models', [])) > 4 else ""
    return (f"  [{num}] {p['name']:<30} keys={len(e.get('keys', [])):<3} "
            f"models={len(e.get('models', [])):<3} {models}{more}")


def print_status(db, verbose=False):
    if verbose:
        print("\nAll providers:")
        for num in sorted(PROVIDERS, key=int):
            p = PROVIDERS[num]
            print(_status_line(num, p, db.get(p["id"], {})))
        for pid, label in _custom_entries(db):
            e = db.get(pid, {})
            print(f"  [C] {label + ' (custom)':<30} keys={len(e.get('keys', [])):<3} "
                  f"models={len(e.get('models', [])):<3} {' '.join(e.get('models', [])[:4])}")
        return
    print("\nConfigured:")
    any_cfg = False
    for num in sorted(PROVIDERS, key=int):
        p = PROVIDERS[num]
        if p["id"] == "custom":
            continue  # template, not a real endpoint
        e = db.get(p["id"], {})
        if _is_configured(e):
            print(_status_line(num, p, e))
            any_cfg = True
    for pid, label in _custom_entries(db):
        e = db.get(pid, {})
        if _is_configured(e):
            print(f"  [C] {label + ' (custom)':<30} keys={len(e.get('keys', [])):<3} "
                  f"models={len(e.get('models', [])):<3} {' '.join(e.get('models', [])[:4])}")
            any_cfg = True
    if not any_cfg:
        print("  (none yet — use: add <name>, e.g. add gemini)")


PROVIDER_ALIASES = {
    "gemini": ["gemini", "google"],
    "openrouter": ["openrouter", "open_router", "open router", "or"],
    "anthropic": ["anthropic", "claude"],
    "openai": ["openai", "gpt", "chatgpt", "o3"],
    "opencode_zen": ["opencode_zen", "opencode", "zen", "opencode zen"],
    "tokenrouter": ["tokenrouter", "token_router", "token router", "tr"],
    "zai": ["zai", "z.ai", "z ai", "glm", "zhipu", "zhipuai"],
    "ollama_cloud": ["ollama_cloud", "ollama cloud", "remote ollama", "ollama remote",
                     "hosted ollama", "ollama hosted"],
    "ollama_local": ["ollama_local", "ollama local", "local ollama", "local", "localhost"],
    "custom": ["custom", "custom provider", "custom endpoint", "other", "new provider"],
}


def _norm_name(s):
    return s.strip().lower().replace("_", " ").replace(".", " ").replace("-", " ")


def resolve_provider(text, db=None):
    """Fuzzy name -> (num, provider) | (None, [candidate nums]) | (None, []).

    Also matches user-created custom endpoints by label (returns ("C", dyn_dict)).
    """
    q = _norm_name(text)
    hits = []
    for num, p in PROVIDERS.items():
        names = [_norm_name(p["id"]), _norm_name(p["name"])] + PROVIDER_ALIASES.get(p["id"], [])
        if q in names or any(q == a for a in names):
            hits.append(num)
    if not hits:
        hits = [num for num, p in PROVIDERS.items()
                if any(q in a or a in q for a in
                       ([_norm_name(p["id"]), _norm_name(p["name"])] + PROVIDER_ALIASES.get(p["id"], [])))]
    if len(hits) == 1:
        return hits[0], PROVIDERS[hits[0]]
    if hits:
        return None, hits
    # fall through to custom endpoints saved in db
    if db:
        for pid, label in _custom_entries(db):
            if q in (_norm_name(pid), _norm_name(label),
                     _norm_name(label.replace("(custom)", ""))):
                return "C", _custom_provider_dict(pid, db)
    return None, []


def _custom_provider_dict(pid, db):
    entry = db.get(pid, {})
    label = entry.get("label") or pid
    return {"id": pid, "name": f"{label} (custom)", "prefix": "openai/",
            "base_url": entry.get("base_url", ""), "type": "custom_api"}


def print_unconfigured(db):
    print("  Not configured yet:")
    for num in sorted(PROVIDERS, key=int):
        p = PROVIDERS[num]
        if not _is_configured(db.get(p["id"], {})):
            print(f"    [{num}] {p['name']}")


def main():
    if "--version" in sys.argv or "-v" in sys.argv:
        print(__version__)
        return
    db = load_db()
    print(f"=== Universal LiteLLM Wizard v{__version__} (keys->test->models->loop->proxy test) ===")
    print("Keys are tested DIRECTLY after entry; models step unlocks only if all keys OK.")
    show_all = False
    while True:
        print_status(db, verbose=show_all)
        print("\n[#] number | add <name> (e.g. add google) | all | [T] proxy test | [Q] save+restart+quit")
        try:
            ch = input("Choice: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting (no final restart). DB already saved per-provider.")
            break
        low = ch.lower()
        if low in ("q", "0", "done", "quit", "exit"):
            save_db(db)
            n = generate_yaml(db)
            print(f"[+] Final config: {n} routes.")
            restart_proxy()
            print("[+] Bye. (Full proxy test available via [T].)")
            break
        if low == "t":
            proxy_smoke_test()
            continue
        if low in ("all", "list", "ls"):
            show_all = not show_all
            continue
        dyn = None  # dynamic custom-endpoint dict when resolved/created below
        if low == "add":
            print_unconfigured(db)
            try:
                name = input("Provider name/number to add: ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if name in PROVIDERS and name != "10":
                ch = name
            elif name == "10":
                dyn = create_custom_provider(db)
                if dyn is None:
                    continue
            else:
                num, res = resolve_provider(name, db)
                if num is None:
                    if res:
                        print("  Ambiguous — did you mean:")
                        for n in res:
                            print(f"    [{n}] {PROVIDERS[n]['name']}")
                    else:
                        print(f"  [!] No provider matches '{name}'.")
                        print_unconfigured(db)
                    continue
                if num == "C":
                    dyn = res
                else:
                    ch = num
        elif low.startswith("add "):
            num, res = resolve_provider(ch[4:], db)
            if num is None:
                if res:
                    print("  Ambiguous — did you mean:")
                    for n in res:
                        print(f"    [{n}] {PROVIDERS[n]['name']}")
                else:
                    print(f"  [!] No provider matches '{ch[4:]}'. Try 'add' or 'all'.")
                continue
            if num == "C":
                dyn = res
            else:
                ch = num
        elif ch.strip() == "10":
            dyn = create_custom_provider(db)
            if dyn is None:
                continue
        if dyn is not None:
            try:
                configure_provider(db, dyn)
                db = load_db()  # re-read (configure saves)
            except (KeyboardInterrupt, EOFError):
                print("\n[-] Provider step cancelled.")
                db = load_db()
            continue
        if ch not in PROVIDERS:
            print("Invalid choice. Try: <number>, add <name>, all, T, Q.")
            continue
        try:
            configure_provider(db, PROVIDERS[ch])
            db = load_db()  # re-read (configure saves)
        except (KeyboardInterrupt, EOFError):
            print("\n[-] Provider step cancelled.")
            db = load_db()


if __name__ == "__main__":
    main()
