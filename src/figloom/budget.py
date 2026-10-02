"""Durable reservations for each real model request, including uncertain outcomes."""
from __future__ import annotations

import json
import math
from decimal import Decimal, ROUND_CEILING
from typing import Callable
from urllib.parse import urlparse

from .store import Store, StoreError, identifier, now


def finite_number(value, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise StoreError(f"{field} must be a finite {'positive' if positive else 'nonnegative'} number.")
    return float(value)


def upward(value: Decimal) -> float:
    return float((value * 1_000_000).to_integral_value(rounding=ROUND_CEILING) / 1_000_000)


def pricing(config: dict) -> dict:
    saved = config.get("pricing", {})
    return {"input_per_million": saved.get("input_per_million", config.get("input_price_per_million")),
            "output_per_million": saved.get("output_per_million", config.get("output_price_per_million")),
            "cached_input_per_million": saved.get("cached_input_per_million", config.get("cached_input_price_per_million", saved.get("input_per_million", config.get("input_price_per_million"))))}


def summary(store: Store) -> dict:
    with store.transaction() as db:
        settings = json.loads(db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0])
        rows = db.execute("SELECT status,reserved,charged FROM spending").fetchall()
        spent = sum(row["charged"] for row in rows if row["status"] != "reserved")
        reserved = sum(row["reserved"] for row in rows if row["status"] == "reserved")
        limit = settings.get("budget_usd", 0.0)
        return {"spent": round(spent, 6), "reserved": round(reserved, 6),
                "available": round(max(0.0, limit - spent - reserved), 6), "budget_usd": limit}


class BudgetGuard:
    def __init__(self, store: Store, settings: dict, *, cancelled: Callable[[], bool] | None = None):
        self.store, self.settings, self.cancelled = store, settings, cancelled or (lambda: False)

    def __call__(self, event: dict):
        phase = event.get("phase")
        if phase == "before":
            if self.cancelled():
                raise StoreError("Job was cancelled before the next model request.", 409)
            channel = "image" if event.get("api") == "images" else "text"
            config = self.settings.get(channel) or {}
            endpoint = urlparse(config.get("base_url", ""))
            local = channel == "text" and config.get("api") == "ollama" and endpoint.hostname in {"localhost", "127.0.0.1", "::1"}
            with self.store.transaction() as db:
                current = json.loads(db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0])
                if current.get(channel, {}) != config:
                    raise StoreError("Model settings changed. Start a new job with the current settings.", 409)
                if event.get("model") != config.get("model"):
                    raise StoreError("The request model differs from the configured model.")
                if local:
                    cost = 0.0
                elif channel == "image":
                    cost = finite_number(config.get("max_request_usd"), "image.max_request_usd", positive=True)
                else:
                    prices = pricing(config)
                    inp = finite_number(prices["input_per_million"], "Input token price", positive=True)
                    out = finite_number(prices["output_per_million"], "Output token price", positive=True)
                    cached = finite_number(prices["cached_input_per_million"], "Cached input token price")
                    if cached > inp:
                        raise StoreError("Cached token price cannot exceed the input price.")
                    if event.get("pricing") and any(event["pricing"].get(k) != prices[k] for k in ("input_per_million", "output_per_million")):
                        raise StoreError("The request uses different token prices from the saved settings.")
                    maximum = event.get("max_output_tokens")
                    if type(maximum) is not int or maximum <= 0:
                        raise StoreError("A model request needs a bounded output token limit.")
                    input_bound = int(event.get("input_bytes") or 0) + 2048
                    cost = upward((Decimal(input_bound) * Decimal(str(inp)) + Decimal(maximum) * Decimal(str(out))) / 1_000_000)
                rows = db.execute("SELECT status,reserved,charged FROM spending").fetchall()
                used = sum(row["reserved"] if row["status"] == "reserved" else row["charged"] for row in rows)
                if used + cost > current.get("budget_usd", 0) + 1e-12:
                    raise StoreError(f"The remaining model budget cannot reserve this request (${cost:.4f}).", 402)
                reservation = identifier()
                # Operation metadata deliberately contains no endpoint credentials or prompts.
                operation = json.dumps({"channel": channel, "local": local, "prices": pricing(config) if channel == "text" and not local else {}, "model": event.get("model")})
                db.execute("INSERT INTO spending VALUES (?,?,?,?,?,?,?)", (reservation, "reserved", cost, 0, operation, now(), now()))
                return reservation
        if phase not in {"after", "error"}:
            raise StoreError("Unknown model budget event.")
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM spending WHERE id=?", (event.get("reservation"),)).fetchone()
            if row is None:
                raise StoreError("Unknown model budget reservation.")
            if row["status"] not in {"reserved", "uncertain"}:
                return row["id"]
            operation = json.loads(row["operation"])
            if phase == "error":
                status = "uncertain" if event.get("ambiguous", True) else "rejected"
                charge = row["reserved"] if status == "uncertain" else 0.0
            elif operation["local"]:
                status, charge = "settled", 0.0
            else:
                usage = event.get("usage") or {}
                if operation["channel"] == "image" or usage.get("input_tokens") is None or usage.get("output_tokens") is None:
                    status, charge = "uncertain", row["reserved"]
                else:
                    prices = operation["prices"]
                    inp = finite_number(usage["input_tokens"], "Reported input tokens")
                    out = finite_number(usage["output_tokens"], "Reported output tokens")
                    cached = finite_number(usage.get("cached_input_tokens", 0) or 0, "Reported cached tokens")
                    if cached > inp:
                        status, charge = "uncertain", row["reserved"]
                    else:
                        charge = upward((Decimal(str(inp - cached)) * Decimal(str(prices["input_per_million"])) + Decimal(str(cached)) * Decimal(str(prices["cached_input_per_million"])) + Decimal(str(out)) * Decimal(str(prices["output_per_million"]))) / Decimal(1_000_000))
                        status = "settled"
            db.execute("UPDATE spending SET status=?,charged=?,updated_at=? WHERE id=?", (status, charge, now(), row["id"]))
            return row["id"]
