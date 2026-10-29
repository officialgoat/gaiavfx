"""Optional experiment budget; reserve before sending and retain uncertain charges."""

import json
import math
import os
import time
import uuid
from pathlib import Path

from filelock import FileLock

from gaiavfx.shared.errors import ApplicationError


class RequestBudget:
    def __init__(self):
        self.path = (
            Path(os.environ["GAIAVFX_ASSISTANT_BUDGET_LEDGER"])
            if os.environ.get("GAIAVFX_ASSISTANT_BUDGET_LEDGER")
            else None
        )
        self.limit = float(os.environ.get("GAIAVFX_ASSISTANT_BUDGET_USD", "10"))

    def reserve(self, connection, output):
        if self.path is None:
            return None
        prices = [connection.get(k) for k in ("input_cost_per_million", "output_cost_per_million")]
        if any(p is None for p in prices):
            raise ApplicationError(
                "Set verified input/output prices before using the experiment budget.", 422
            )
        maximum = (connection["context_tokens"] * prices[0] + output * prices[1]) / 1e6
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".lock"):
            ledger = json.loads(self.path.read_text()) if self.path.exists() else {"requests": []}
            total = sum(r["charged_usd"] for r in ledger["requests"])
            if total + maximum > self.limit:
                raise ApplicationError(
                    "The experiment API budget cannot cover another request. No request was sent.",
                    422,
                )
            ident = str(uuid.uuid4())
            ledger["requests"].append(
                {
                    "id": ident,
                    "model": connection["model"],
                    "started_at": time.time(),
                    "state": "reserved",
                    "reserved_usd": maximum,
                    "charged_usd": maximum,
                }
            )
            self._write(ledger)
            return ident

    def complete(self, ident, connection, usage, provider_cost=None, response_id=None):
        if ident is None:
            return
        basis = "provider_reported"
        if isinstance(usage.get("cost"), (float, int)):
            cost = float(usage["cost"])
            if provider_cost is not None:
                cost = max(cost, float(provider_cost))
        elif provider_cost is not None and float(provider_cost) > 0:
            cost = float(provider_cost)
        elif "prompt_tokens" in usage and "completion_tokens" in usage:
            basis = "configured_token_prices"
            cost = (
                usage["prompt_tokens"] * connection["input_cost_per_million"]
                + usage["completion_tokens"] * connection["output_cost_per_million"]
            ) / 1e6
        else:
            return  # Missing usage, cancellation and failed requests retain their reservation.
        if not math.isfinite(cost) or cost < 0:
            return
        with FileLock(str(self.path) + ".lock"):
            ledger = json.loads(self.path.read_text())
            item = next(r for r in ledger["requests"] if r["id"] == ident)
            item.update(
                state="accounted",
                charged_usd=cost,
                usage=usage,
                response_id=response_id,
                accounting_basis=basis,
                completed_at=time.time(),
            )
            self._write(ledger)

    def _write(self, ledger):
        ledger["limit_usd"] = self.limit
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        temporary.replace(self.path)
