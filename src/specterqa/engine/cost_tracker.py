"""SpecterQA Cost Tracker — Tracks AI token costs and enforces budget limits.

Bundled in specterqa-ios — sourced from specterqa.engine (upstream unpublished).

Monitors per-call costs (model, tokens in/out, USD), enforces per-run caps
from product config, warns at configurable thresholds, and hard-stops if
the budget is exceeded.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from specterqa.engine.models import PRICING

logger = logging.getLogger("specterqa.engine.cost_tracker")


# ---------------------------------------------------------------------------
# Internal pricing lookup
# ---------------------------------------------------------------------------


def _build_model_pricing() -> dict[str, tuple[float, float]]:
    """Convert PRICING dict to (input, output) tuple lookup."""
    result: dict[str, tuple[float, float]] = {}
    for model_id, prices in PRICING.items():
        result[model_id] = (prices["input"], prices["output"])
    return result


MODEL_PRICING: dict[str, tuple[float, float]] = _build_model_pricing()
# Local Ollama models -- zero cost
MODEL_PRICING["ollama:llava:13b"] = (0.0, 0.0)

_FALLBACK_MODEL = "claude-sonnet-4-6"


@dataclasses.dataclass
class APICall:
    """Record of a single API call."""

    timestamp: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    purpose: str  # e.g. "screenshot_interpretation", "simple_action"


@dataclasses.dataclass
class CostSummary:
    """Aggregated cost summary for a run."""

    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    calls_by_model: dict[str, int]
    cost_by_model: dict[str, float]
    budget_limit_usd: float
    budget_remaining_usd: float
    budget_exceeded: bool
    warning_issued: bool
    call_count: int


class CostTracker:
    """Tracks AI token costs for a single run and enforces budget limits."""

    COST_LEDGER_FILENAME = "costs.jsonl"

    def __init__(
        self,
        per_run_usd: float = 10.0,
        per_day_usd: float = 0.0,
        per_month_usd: float = 0.0,
        warn_at_pct: int = 80,
        system_ledger_path: Path | None = None,
        initiative_id: str | None = None,
    ) -> None:
        self._per_run_usd = per_run_usd
        self._per_day_usd = per_day_usd
        self._per_month_usd = per_month_usd
        self._warn_at_pct = warn_at_pct
        self._system_ledger_path = system_ledger_path
        self._initiative_id = initiative_id
        self._calls: list[APICall] = []
        self._total_cost: float = 0.0
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._warning_issued: bool = False
        self._budget_exceeded: bool = False

    def record_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        purpose: str = "",
    ) -> APICall:
        """Record an API call and return the call record.

        Raises BudgetExceededError if the per-run cap is exceeded.
        """
        cost = self._calculate_cost(model, input_tokens, output_tokens)
        call = APICall(
            timestamp=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6),
            purpose=purpose,
        )
        self._calls.append(call)
        self._total_cost += cost
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens

        if not self._warning_issued and self._per_run_usd > 0:
            pct_used = (self._total_cost / self._per_run_usd) * 100
            if pct_used >= self._warn_at_pct:
                self._warning_issued = True

        if self._per_run_usd > 0 and self._total_cost > self._per_run_usd:
            self._budget_exceeded = True
            raise BudgetExceededError(
                f"Run budget exceeded: ${self._total_cost:.4f} > ${self._per_run_usd:.2f} limit"
            )

        return call

    @property
    def warning_issued(self) -> bool:
        return self._warning_issued

    @property
    def budget_exceeded(self) -> bool:
        return self._budget_exceeded

    @property
    def total_cost(self) -> float:
        return round(self._total_cost, 6)

    @property
    def calls(self) -> list[APICall]:
        return list(self._calls)

    def get_summary(self) -> CostSummary:
        """Return aggregated cost summary."""
        calls_by_model: dict[str, int] = {}
        cost_by_model: dict[str, float] = {}
        for call in self._calls:
            calls_by_model[call.model] = calls_by_model.get(call.model, 0) + 1
            cost_by_model[call.model] = cost_by_model.get(call.model, 0.0) + call.cost_usd
        cost_by_model = {k: round(v, 6) for k, v in cost_by_model.items()}

        remaining = max(0.0, self._per_run_usd - self._total_cost)
        return CostSummary(
            total_cost_usd=round(self._total_cost, 6),
            total_input_tokens=self._total_input_tokens,
            total_output_tokens=self._total_output_tokens,
            calls_by_model=calls_by_model,
            cost_by_model=cost_by_model,
            budget_limit_usd=self._per_run_usd,
            budget_remaining_usd=round(remaining, 6),
            budget_exceeded=self._budget_exceeded,
            warning_issued=self._warning_issued,
            call_count=len(self._calls),
        )

    @staticmethod
    def _calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate USD cost for a single API call."""
        pricing = MODEL_PRICING.get(model)
        if pricing is None:
            pricing = MODEL_PRICING.get(_FALLBACK_MODEL, (3.00, 15.00))
        input_price, output_price = pricing
        return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price

    @staticmethod
    def _ledger_path(base_dir: Path) -> Path:
        return base_dir / CostTracker.COST_LEDGER_FILENAME

    @staticmethod
    def check_cumulative_budget(
        base_dir: Path,
        per_day_usd: float = 0.0,
        per_month_usd: float = 0.0,
    ) -> dict[str, Any]:
        """Check whether daily/monthly cumulative cost limits are exceeded."""
        ledger = CostTracker._ledger_path(base_dir)
        now = dt.datetime.now(dt.timezone.utc)
        today_str = now.strftime("%Y-%m-%d")
        month_prefix = now.strftime("%Y-%m")

        daily_spent = 0.0
        monthly_spent = 0.0

        if ledger.exists():
            with ledger.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("timestamp", "")
                    cost = float(entry.get("cost_usd", 0.0))
                    if ts[:10] == today_str:
                        daily_spent += cost
                    if ts[:7] == month_prefix:
                        monthly_spent += cost

        return {
            "daily_ok": per_day_usd <= 0.0 or daily_spent < per_day_usd,
            "monthly_ok": per_month_usd <= 0.0 or monthly_spent < per_month_usd,
            "daily_spent": round(daily_spent, 6),
            "monthly_spent": round(monthly_spent, 6),
            "daily_limit": per_day_usd,
            "monthly_limit": per_month_usd,
        }

    @staticmethod
    def record_run_cost(
        base_dir: Path,
        run_id: str,
        product: str,
        cost_usd: float,
        level: str = "",
    ) -> Path:
        """Append a cost entry to the persistent JSONL ledger."""
        ledger = CostTracker._ledger_path(base_dir)
        ledger.parent.mkdir(parents=True, exist_ok=True)

        prev_hash = CostTracker._last_hash(ledger)
        entry = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "run_id": run_id,
            "product": product,
            "level": level,
            "cost_usd": round(cost_usd, 6),
            "previous_hash": prev_hash,
        }
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
        return ledger

    @staticmethod
    def _last_hash(ledger_path: Path) -> str | None:
        if not ledger_path.exists():
            return None
        try:
            last_line = ""
            with ledger_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped:
                        last_line = stripped
            if not last_line:
                return None
            return "sha256:" + hashlib.sha256(last_line.encode("utf-8")).hexdigest()
        except Exception:
            return None

    @staticmethod
    def flush_to_system_ledger(
        system_ledger_path: Path | None,
        calls: list[APICall],
        run_id: str,
        scenario_id: str,
        product: str,
        initiative_id: str | None = None,
        department: str = "SpecterQA",
    ) -> int:
        """Write API calls to an external system-wide cost ledger."""
        if system_ledger_path is None:
            return 0

        billable = [c for c in calls if c.cost_usd > 0]
        if not billable:
            return 0

        system_ledger_path.parent.mkdir(parents=True, exist_ok=True)
        prev_hash = CostTracker._last_hash(system_ledger_path)
        written = 0

        try:
            with system_ledger_path.open("a", encoding="utf-8") as fh:
                for call in billable:
                    entry: dict[str, Any] = {
                        "timestamp": call.timestamp,
                        "department": department,
                        "model_id": call.model,
                        "tokens_in": call.input_tokens,
                        "tokens_out": call.output_tokens,
                        "cost_usd": call.cost_usd,
                        "run_id": run_id,
                        "scenario_id": scenario_id,
                        "product": product,
                        "previous_hash": prev_hash,
                    }
                    if initiative_id is not None:
                        entry["initiative_id"] = initiative_id
                    line = json.dumps(entry, separators=(",", ":"))
                    fh.write(line + "\n")
                    prev_hash = "sha256:" + hashlib.sha256(line.encode("utf-8")).hexdigest()
                    written += 1
        except Exception as exc:
            logger.error("Failed to flush costs to system ledger: %s", exc)

        return written


class BudgetExceededError(Exception):
    """Raised when a run exceeds its per-run cost budget."""
    pass


class CumulativeBudgetExceededError(Exception):
    """Raised when cumulative daily or monthly costs exceed their caps."""
    pass
