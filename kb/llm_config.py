"""Runtime LLM provider configuration and OpenAI-compatible chat calls."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path

import storage

storage.ensure_directories()
storage.load_local_env()
CONFIG_FILE = storage.LLM_CONFIG_FILE
DIR = storage.DATA_ROOT

DEFAULT_PROVIDERS = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "api_url": "https://api.deepseek.com/v1/chat/completions",
        "api_key": "",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "model": "deepseek-v4-flash",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "api_url": "https://api.openai.com/v1/chat/completions",
        "api_key": "",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
        "model": "gpt-4o-mini",
    },
    {
        "id": "siliconflow",
        "name": "SiliconFlow",
        "api_url": "https://api.siliconflow.cn/v1/chat/completions",
        "api_key": "",
        "models": ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1"],
        "model": "deepseek-ai/DeepSeek-V3",
    },
    {
        "id": "moonshot",
        "name": "Moonshot",
        "api_url": "https://api.moonshot.cn/v1/chat/completions",
        "api_key": "",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "model": "moonshot-v1-32k",
    },
    {
        "id": "custom",
        "name": "Custom",
        "api_url": "",
        "api_key": "",
        "models": ["custom-model"],
        "model": "custom-model",
    },
]


def load_env_file() -> None:
    """Load local.env without overriding variables already set by the process."""
    storage.load_local_env()


def _safe_provider_id(value: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip()).strip("_")
    return value or fallback


def _normalize_models(models, selected_model="") -> list[str]:
    if isinstance(models, str):
        raw = re.split(r"[\n,]+", models)
    elif isinstance(models, list):
        raw = models
    else:
        raw = []
    result = []
    for item in raw:
        item = str(item or "").strip()
        if item and item not in result:
            result.append(item)
    selected_model = str(selected_model or "").strip()
    if selected_model and selected_model not in result:
        result.insert(0, selected_model)
    return result or ["custom-model"]


def _normalize_provider(provider, fallback_id="custom") -> dict:
    provider = provider if isinstance(provider, dict) else {}
    provider_id = _safe_provider_id(provider.get("id"), fallback_id)
    name = str(provider.get("name") or provider_id).strip() or provider_id
    api_url = str(provider.get("api_url") or "").strip()
    api_key = str(provider.get("api_key") or "").strip()
    model = str(provider.get("model") or "").strip()
    models = _normalize_models(provider.get("models"), model)
    if not model:
        model = models[0]
    protocol = str(provider.get("protocol") or "openai").strip().lower()
    if protocol not in ("openai", "anthropic"):
        protocol = "openai"
    # long_context_models is a list of model names that should be sent
    # with the 1M-context window hint (where the API supports it,
    # e.g. moonshot v1, kimi-k2). Stored as a separate list rather than
    # nesting into the model strings to keep the format simple.
    raw_lc = provider.get("long_context_models")
    if isinstance(raw_lc, list):
        long_context_models = [str(m).strip() for m in raw_lc if str(m).strip()]
    else:
        long_context_models = []
    return {
        "id": provider_id,
        "name": name,
        "api_url": api_url,
        "api_key": api_key,
        "models": models,
        "model": model,
        "protocol": protocol,
        "long_context_models": long_context_models,
    }


def _env_provider() -> dict | None:
    load_env_file()
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    api_url = os.environ.get(
        "LLM_API_URL", "https://api.deepseek.com/v1/chat/completions"
    ).strip()
    model = os.environ.get("LLM_MODEL", "deepseek-v4-flash").strip()
    if not api_key:
        return None
    return _normalize_provider(
        {
            "id": "local_env",
            "name": "default",
            "api_url": api_url,
            "api_key": api_key,
            "models": [model],
            "model": model,
        },
        "local_env",
    )


def _default_config() -> dict:
    providers = [deepcopy(p) for p in DEFAULT_PROVIDERS]
    active = providers[0]["id"]
    env_provider = _env_provider()
    if env_provider:
        providers = [env_provider] + [
            p for p in providers if p["id"] != env_provider["id"]
        ]
        active = env_provider["id"]
    return {"active_provider": active, "providers": providers}


def load_llm_config() -> dict:
    """Load config from disk and merge it with provider templates."""
    cfg = _default_config()
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        if isinstance(raw, dict):
            configured = []
            seen = set()
            for idx, provider in enumerate(raw.get("providers") or []):
                normalized = _normalize_provider(provider, f"provider_{idx + 1}")
                if normalized["id"] in seen:
                    normalized["id"] = _safe_provider_id(
                        f"{normalized['id']}_{idx + 1}", f"provider_{idx + 1}"
                    )
                seen.add(normalized["id"])
                configured.append(normalized)
            if configured:
                cfg["providers"] = configured
            active = str(raw.get("active_provider") or "").strip()
            if active and any(p["id"] == active for p in cfg["providers"]):
                cfg["active_provider"] = active
            elif cfg["providers"]:
                cfg["active_provider"] = cfg["providers"][0]["id"]
    # Migration: rename any stale "local.env" labels to "default" so
    # the auto-generated local_env provider shows the right display
    # name. Older installs saved name='local.env' under the hood.
    migrated = False
    for p in cfg.get("providers", []):
        if p.get("id") == "local_env" and p.get("name") == "local.env":
            p["name"] = "default"
            migrated = True
    if migrated and CONFIG_FILE.exists():
        try:
            CONFIG_FILE.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
    return cfg


def _mask_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "configured"
    return f"{api_key[:4]}...{api_key[-4:]}"


def public_llm_config() -> dict:
    """Return the LLM config for the frontend.

    The API key is returned in full so the settings UI can show and
    edit it (same trade-off as the env keys: localhost-only endpoint,
    desktop app). The frontend can choose to mask in its own input
    via type='password'.
    """
    cfg = load_llm_config()
    providers = []
    for provider in cfg["providers"]:
        # Backfill any missing schema fields so the UI never sees null.
        item = {k: v for k, v in provider.items() if k != "api_key"}
        item["api_key"] = provider.get("api_key", "")
        item["api_key_set"] = bool(provider.get("api_key"))
        item["api_key_hint"] = _mask_key(provider.get("api_key", ""))
        item["protocol"] = provider.get("protocol") or "openai"
        item["long_context_models"] = list(provider.get("long_context_models") or [])
        providers.append(item)
    return {"active_provider": cfg.get("active_provider"), "providers": providers}


def save_llm_config_from_public(data) -> dict:
    """Save config from the UI. Empty API key fields preserve existing keys."""
    data = data if isinstance(data, dict) else {}
    current = load_llm_config()
    old_keys = {p["id"]: p.get("api_key", "") for p in current.get("providers", [])}

    active = _safe_provider_id(data.get("active_provider"), "")
    providers = []
    seen = set()
    for idx, provider in enumerate(data.get("providers") or []):
        if not isinstance(provider, dict):
            continue
        normalized = _normalize_provider(provider, f"provider_{idx + 1}")
        if normalized["id"] in seen:
            normalized["id"] = _safe_provider_id(
                f"{normalized['id']}_{idx + 1}", f"provider_{idx + 1}"
            )
        seen.add(normalized["id"])

        if provider.get("api_key"):
            normalized["api_key"] = str(provider.get("api_key")).strip()
        elif provider.get("keep_key", True):
            normalized["api_key"] = old_keys.get(normalized["id"], "")
        else:
            normalized["api_key"] = ""
        providers.append(normalized)

    if not providers:
        providers = _default_config()["providers"]
    if not active or not any(p["id"] == active for p in providers):
        active = providers[0]["id"]

    cfg = {"active_provider": active, "providers": providers}
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return public_llm_config()


def resolve_llm_settings(provider_id: str = "", model: str = "") -> dict:
    cfg = load_llm_config()
    providers = cfg.get("providers") or []
    if not providers:
        raise ValueError("No LLM providers configured")

    requested_id = str(provider_id or cfg.get("active_provider") or "").strip()
    provider = next((p for p in providers if p["id"] == requested_id), None)
    if provider is None:
        provider = providers[0]

    selected_model = str(model or provider.get("model") or "").strip()
    if not selected_model:
        selected_model = (provider.get("models") or [""])[0]

    return {
        "provider_id": provider["id"],
        "provider_name": provider["name"],
        "api_url": provider.get("api_url", ""),
        "api_key": provider.get("api_key", ""),
        "model": selected_model,
        "protocol": provider.get("protocol", "openai"),
        "long_context": selected_model in (provider.get("long_context_models") or []),
    }


def call_chat_completion(
    messages,
    *,
    provider_id: str = "",
    model: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    timeout: int = 120,
    stream: bool | None = None,
) -> dict:
    settings = resolve_llm_settings(provider_id=provider_id, model=model)
    if not settings["api_url"]:
        raise ValueError(f"LLM provider {settings['provider_name']} has no API URL")
    if not settings["api_key"]:
        raise ValueError(f"LLM provider {settings['provider_name']} has no API key")

    protocol = settings.get("protocol", "openai")

    if protocol == "anthropic":
        # Anthropic-compatible endpoint. We use the Messages-style body
        # (system prompt extracted, model in body, max_tokens not
        # optional). API key is sent via x-api-key, plus
        # anthropic-version header.
        system_prompts = [m["content"] for m in messages if m.get("role") == "system"]
        chat_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in messages if m.get("role") in ("user", "assistant")
        ]
        body = {
            "model": settings["model"],
            "messages": chat_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompts:
            body["system"] = "\n\n".join(system_prompts)
        if stream is not None:
            body["stream"] = stream
        headers = {
            "Content-Type": "application/json",
            "x-api-key": settings["api_key"],
            "anthropic-version": "2023-06-01",
        }
    else:
        # OpenAI-compatible (default). Includes system messages inline.
        body = {
            "model": settings["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stream is not None:
            body["stream"] = stream
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings['api_key']}",
        }

    req = urllib.request.Request(
        settings["api_url"],
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())
