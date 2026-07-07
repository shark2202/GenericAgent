"""ga setup — Interactive mykey.jsonc configuration wizard."""
import os
import sys
import getpass


_QUICK_PROVIDERS = {
    "anthropic": {
        "key_prefix": "native_claude_config",
        "apibase": "https://api.anthropic.com",
        "model": "claude-opus-4-7",
        "extras": {"thinking_type": "adaptive"},
    },
    "openai": {
        "key_prefix": "native_oai_config",
        "apibase": "https://api.openai.com/v1",
        "model": "gpt-5.5",
        "extras": {"api_mode": "chat_completions"},
    },
}

_ADVANCED_FIELDS = [
    ("proxy", "Proxy URL", ""),
    ("connect_timeout", "Connect timeout (seconds)", "10"),
    ("read_timeout", "Read timeout (seconds)", "120"),
    ("max_tokens", "Max tokens", "8192"),
    ("temperature", "Temperature", "1.0"),
    ("context_win", "Context window size", "24000"),
]


def validate_apikey(key):
    return bool(key) and len(key.strip()) >= 20


def detect_existing(path):
    """Detect existing mykey.jsonc and return user choice. Returns 'create', 'overwrite', 'exit', or 'append'."""
    if not os.path.exists(path):
        return "create"

    print(f"\n⚠️  {path} already exists.")
    print("  [o] Overwrite — replace entirely")
    print("  [a] Append — add new config block at end")
    print("  [x] Exit — do nothing")

    while True:
        choice = input("  Choice [o/a/x]: ").strip().lower()
        if choice in ("o", "overwrite"):
            return "overwrite"
        elif choice in ("a", "append"):
            return "append"
        elif choice in ("x", "exit", "q", "quit"):
            return "exit"
        print("  Invalid choice. Please enter o, a, or x.")


def _scan_next_index(path, prefix):
    """Find the next available index for a config block with given prefix."""
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return 0
    import re
    existing = re.findall(rf'"{re.escape(prefix)}(\d+)"', content)
    if not existing:
        return 0
    return max(int(n) for n in existing) + 1


def generate_config_block(provider, apikey, block_index=0, **advanced):
    """Generate a JSONC config block string. Returns the block text (without outer braces)."""
    if provider == "custom":
        prefix = "native_custom_config"
        apibase = advanced.pop("apibase", "")
        model = advanced.pop("model", "")
        extras = {}
    elif provider in _QUICK_PROVIDERS:
        info = _QUICK_PROVIDERS[provider]
        prefix = info["key_prefix"]
        apibase = info["apibase"]
        model = info["model"]
        extras = dict(info.get("extras", {}))
    else:
        raise ValueError(f"Unknown provider: {provider}")

    lines = []
    lines.append(f'  "{prefix}{block_index}": {{')
    lines.append(f'    "name": "{provider}",')
    lines.append(f'    "apikey": "{apikey}",')
    lines.append(f'    "apibase": "{apibase}",')
    lines.append(f'    "model": "{model}",')

    for k, v in extras.items():
        if isinstance(v, bool):
            lines.append(f'    "{k}": {str(v).lower()},')
        elif isinstance(v, (int, float)):
            lines.append(f'    "{k}": {v},')
        else:
            lines.append(f'    "{k}": "{v}",')

    # Advanced fields
    field_map = {
        "proxy": "proxy",
        "connect_timeout": "connect_timeout",
        "read_timeout": "read_timeout",
        "max_tokens": "max_tokens",
        "temperature": "temperature",
        "context_win": "context_win",
    }
    for key, field_name in field_map.items():
        val = advanced.get(key)
        if val is not None and val != "":
            try:
                num = int(val)
                lines.append(f'    "{field_name}": {num},')
            except ValueError:
                try:
                    num = float(val)
                    lines.append(f'    "{field_name}": {num},')
                except ValueError:
                    lines.append(f'    "{field_name}": "{val}",')

    # Remove trailing comma from last line
    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]

    lines.append("  }")
    return "\n".join(lines)


def _ask_advanced():
    """Prompt for advanced fields. Returns dict of set values."""
    print("\n── Advanced Options (press Enter to skip) ──")
    config = {}
    for key, label, default in _ADVANCED_FIELDS:
        val = input(f"  {label} [{default}]: ").strip()
        if val:
            config[key] = val
        elif default:
            config[key] = default
    return config


def run_setup_wizard():
    """Main entry point for ga setup."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_path = os.path.join(project_root, "mykey.jsonc")

    # Step 1: Check existing file
    choice = detect_existing(target_path)
    if choice == "exit":
        print("Exiting without changes.")
        return

    # Step 2: Quick setup
    print("\n── Quick Setup ──")

    apikey = ""
    while not validate_apikey(apikey):
        apikey = getpass.getpass("  API Key: ").strip()
        if not validate_apikey(apikey):
            print("  API Key must be at least 20 characters. Please try again.")

    print("\n  Providers:")
    print("    [1] Anthropic (Claude)")
    print("    [2] OpenAI (GPT)")
    print("    [3] Custom")
    provider_choice = input("  Select [1/2/3]: ").strip()

    provider_map = {"1": "anthropic", "2": "openai", "3": "custom"}
    provider = provider_map.get(provider_choice)
    if not provider:
        print(f"  Invalid choice '{provider_choice}', defaulting to Anthropic.")
        provider = "anthropic"

    advanced = {}
    if provider == "custom":
        advanced["apibase"] = input("  API Base URL: ").strip()
        advanced["model"] = input("  Model name: ").strip()

    # Step 3: Generate config block
    if choice == "append":
        block_index = _scan_next_index(target_path, _QUICK_PROVIDERS.get(provider, {}).get("key_prefix", "native_custom_config"))
    else:
        block_index = 0

    block = generate_config_block(provider, apikey, block_index=block_index, **advanced)

    # Step 4: Advanced mode
    adv_choice = input("\n  Configure advanced options? [y/N]: ").strip().lower()
    if adv_choice in ("y", "yes"):
        adv_config = _ask_advanced()
        provider_for_index = provider if provider != "custom" else "custom"
        prefix = _QUICK_PROVIDERS.get(provider, {"key_prefix": "native_custom_config"})["key_prefix"]
        idx = block_index if choice == "append" else 0
        block = generate_config_block(provider, apikey, block_index=idx, **adv_config)

    # Step 5: Write file
    if choice == "append" and os.path.exists(target_path):
        with open(target_path, "r", encoding="utf-8") as f:
            existing = f.read().rstrip()
        if existing.endswith("}"):
            existing = existing[:-1].rstrip()
            if not existing.endswith(","):
                existing += ","
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(existing + "\n" + block + "\n}\n")
    else:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write("{\n" + block + "\n}\n")

    print(f"\n✅ Configuration saved to {target_path}")
    print("   Run 'ga' or 'python agentmain.py' to start.")


if __name__ == "__main__":
    run_setup_wizard()
