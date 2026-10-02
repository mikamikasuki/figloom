import pytest

from figloom.budget import BudgetGuard, summary
from figloom.store import Store, StoreError


def setup(tmp_path, *, local=False, proxy=False):
    store = Store(tmp_path)
    settings = {"text": {"base_url": "http://127.0.0.1:11434" if local or proxy else "https://example.org/v1",
                         "api": "ollama" if local else "chat_completions", "model": "configured",
                         "input_price_per_million": 1, "output_price_per_million": 2}, "image": {}, "budget_usd": 1}
    store.save_settings(settings)
    return store, settings, BudgetGuard(store, settings)


def test_reservation_real_usage_settlement_and_unknown_outcome(tmp_path):
    store, settings, guard = setup(tmp_path)
    token = guard({"phase": "before", "model": "configured", "input_bytes": 100, "max_output_tokens": 100})
    assert summary(store)["reserved"] == pytest.approx(.002348)
    guard({"phase": "after", "reservation": token, "usage": {"input_tokens": 10, "output_tokens": 20}})
    assert summary(store)["spent"] == pytest.approx(.00005)
    token = guard({"phase": "before", "model": "configured", "input_bytes": 100, "max_output_tokens": 100})
    guard({"phase": "error", "reservation": token, "ambiguous": True})
    assert summary(store)["spent"] == pytest.approx(.002398)
    assert summary(store)["reserved"] == 0


def test_atomic_budget_and_model_config_change(tmp_path):
    store, settings, guard = setup(tmp_path)
    settings["budget_usd"] = .0024
    store.save_settings(settings)
    guard({"phase": "before", "model": "configured", "input_bytes": 100, "max_output_tokens": 100})
    with pytest.raises(StoreError, match="remaining"):
        guard({"phase": "before", "model": "configured", "input_bytes": 100, "max_output_tokens": 100})
    updated = {**settings, "text": {**settings["text"], "model": "changed"}}
    store.save_settings(updated)
    with pytest.raises(StoreError, match="changed"):
        guard({"phase": "before", "model": "configured", "input_bytes": 100, "max_output_tokens": 100})


def test_only_local_ollama_is_free_and_cancel_blocks_request(tmp_path):
    store, _, guard = setup(tmp_path / "local", local=True)
    token = guard({"phase": "before", "model": "configured", "api": "ollama", "input_bytes": 100, "max_output_tokens": 100})
    guard({"phase": "after", "reservation": token, "usage": {}})
    assert summary(store)["spent"] == 0
    proxy, _, proxy_guard = setup(tmp_path / "proxy", proxy=True)
    proxy_guard({"phase": "before", "model": "configured", "input_bytes": 100, "max_output_tokens": 100})
    assert summary(proxy)["reserved"] > 0
    guard.cancelled = lambda: True
    with pytest.raises(StoreError, match="cancelled"):
        guard({"phase": "before", "model": "configured", "api": "ollama", "input_bytes": 100, "max_output_tokens": 100})
