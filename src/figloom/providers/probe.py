"""Bounded, read-only provider metadata probes. Never submit generation work."""
from __future__ import annotations

import json
from urllib.parse import urlparse

import httpx

from .client import ProviderError, normalize_endpoint_base


class ProbeError(RuntimeError):
    def __init__(self, message: str, code: str, status: int):
        super().__init__(message)
        self.code, self.status = code, status


def list_models(config: dict) -> list[dict[str, str]]:
    if not config.get('base_url'):
        raise ProbeError('Configure a provider endpoint before checking models.', 'invalid_request', 400)
    api = config.get('api') or 'chat_completions'
    try:
        base = normalize_endpoint_base(config['base_url'], api)
    except ProviderError:
        raise ProbeError('Invalid provider endpoint.', 'invalid_request', 400) from None
    parsed = urlparse(base)
    local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
    if not local and parsed.scheme != 'https' or api == 'ollama' and not local:
        raise ProbeError('External provider endpoints require HTTPS; Ollama requires loopback.', 'invalid_request', 400)
    url = base + ('/api/tags' if api == 'ollama' else '/models')
    key = config.get('api_key') or ''
    headers = {'Accept': 'application/json', **({'Authorization': 'Bearer ' + key} if key else {})}
    timeout = min(float(config.get('timeout') or 10), 10)
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(timeout, 5)), follow_redirects=False) as client:
            with client.stream('GET', url, headers=headers) as response:
                if response.status_code in (401, 403):
                    raise ProbeError('Provider rejected authentication. Check its API key.', 'provider_authentication_failed', 502)
                if response.status_code in (404, 405):
                    raise ProbeError('Provider model-list endpoint is unavailable. Check its base URL and API type.', 'provider_endpoint_not_found', 502)
                if response.status_code >= 500 or response.status_code in (408, 429):
                    raise ProbeError('Provider metadata service is unavailable. Try again later.', 'provider_unavailable', 503)
                if response.status_code != 200:
                    raise ProbeError('Provider did not return a usable model list.', 'provider_invalid_response', 502)
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2_000_000:
                        raise ProbeError('Provider model list exceeds the response limit.', 'provider_invalid_response', 502)
                    chunks.append(chunk)
        value = json.loads(b''.join(chunks))
        entries = value.get('models' if api == 'ollama' else 'data') if isinstance(value, dict) else None
        if not isinstance(entries, list) or len(entries) > 10_000:
            raise ValueError
        models = set()
        for entry in entries:
            model = (entry.get('name') or entry.get('model')) if api == 'ollama' and isinstance(entry, dict) else entry.get('id') if isinstance(entry, dict) else None
            if not isinstance(model, str) or not model or len(model) > 4000 or any(ord(c) < 32 for c in model) or key and key in model:
                raise ValueError
            models.add(model)
        return [{'id': model} for model in sorted(models)]
    except httpx.RequestError:
        raise ProbeError('Cannot reach provider metadata service. Check its endpoint and network.', 'provider_unavailable', 503) from None
    except (ValueError, TypeError):
        raise ProbeError('Provider returned an invalid model list.', 'provider_invalid_response', 502) from None
