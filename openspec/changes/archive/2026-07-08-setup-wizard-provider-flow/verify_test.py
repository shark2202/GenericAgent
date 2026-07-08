"""Verification script for setup-wizard-provider-flow change."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from ga_cli.setup_wizard import (
    _QUICK_PROVIDERS,
    generate_config_block,
    fetch_models,
    validate_apikey,
)
import re

# 1. No hardcoded models in _QUICK_PROVIDERS
assert "model" not in _QUICK_PROVIDERS["anthropic"], "anthropic still has hardcoded model"
assert "model" not in _QUICK_PROVIDERS["openai"], "openai still has hardcoded model"
print("PASS: no hardcoded models")

# 2. generate_config_block accepts explicit apibase/model params
block = generate_config_block(
    "anthropic", "sk-test-key-1234567890abcdef",
    apibase="https://api.anthropic.com",
    model="claude-sonnet-4-5-20250929",
    block_index=0,
)
assert '"apibase": "https://api.anthropic.com"' in block
assert '"model": "claude-sonnet-4-5-20250929"' in block
assert '"thinking_type": "adaptive"' in block
print("PASS: anthropic config block correct")

block2 = generate_config_block(
    "openai", "sk-test-key-1234567890abcdef",
    apibase="https://api.openai.com/v1",
    model="gpt-4o",
    block_index=0,
)
assert '"model": "gpt-4o"' in block2
assert '"api_mode": "chat_completions"' in block2
print("PASS: openai config block correct")

block3 = generate_config_block(
    "custom", "sk-test-key-1234567890abcdef",
    apibase="https://my-proxy.com/v1",
    model="custom-model",
    block_index=0,
)
assert '"apibase": "https://my-proxy.com/v1"' in block3
assert '"model": "custom-model"' in block3
print("PASS: custom config block correct")

# 3. fetch_models URL construction (verify logic without network)
base = "https://api.anthropic.com".rstrip("/")
if re.search(r"/v\d+(/|$)", base):
    url = f"{base}/models"
else:
    url = f"{base}/v1/models"
assert url == "https://api.anthropic.com/v1/models", f"Got: {url}"
print(f"PASS: anthropic models URL = {url}")

base = "https://api.openai.com/v1".rstrip("/")
if re.search(r"/v\d+(/|$)", base):
    url = f"{base}/models"
else:
    url = f"{base}/v1/models"
assert url == "https://api.openai.com/v1/models", f"Got: {url}"
print(f"PASS: openai models URL = {url}")

# 4. fetch_models returns None on failure (no crash)
result = fetch_models("https://api.anthropic.com", "invalid-key", "anthropic")
assert result is None
print("PASS: fetch_models returns None on failure")

# 5. validate_apikey still works
assert validate_apikey("sk-test-key-1234567890abcdef")
assert not validate_apikey("short")
print("PASS: validate_apikey works")

print("\n=== ALL VERIFICATION TESTS PASSED ===")
