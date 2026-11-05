"""Budget guard: checked before every AI call, using the worst case (full max_tokens output)."""
from datetime import datetime, timezone


class BudgetExceeded(RuntimeError):
    pass


class AIUnavailable(BudgetExceeded):
    """The provider refused service (no credit, bad key, outage). Callers stop making AI calls
    for this run exactly as for the budget cap, and nothing is marked as processed."""


def month_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


class BudgetGuard:
    def __init__(self, monthly_cap: float, per_run_cap: float, month_spent: float = 0.0):
        self.monthly_cap, self.per_run_cap = monthly_cap, per_run_cap
        self.month_spent, self.run_spent = month_spent, 0.0
        self.skipped_calls = 0

    @classmethod
    def from_db(cls, db, cfg: dict, now: datetime) -> "BudgetGuard":
        rows = db.select("llm_usage", {"created_at": ("gte", month_start(now))}, columns="cost_usd", order="id.asc")
        spent = sum(float(r["cost_usd"] or 0) for r in rows)
        return cls(float(cfg["monthly_cap_usd"]), float(cfg["per_run_cap_usd"]), spent)

    @property
    def exhausted(self) -> bool:
        return self.month_spent >= self.monthly_cap or self.run_spent >= self.per_run_cap

    def check(self, worst_case_cost: float) -> None:
        if (self.month_spent + worst_case_cost > self.monthly_cap
                or self.run_spent + worst_case_cost > self.per_run_cap):
            self.skipped_calls += 1
            raise BudgetExceeded(
                f"AI call skipped: ${worst_case_cost:.4f} could exceed the cap "
                f"(month ${self.month_spent:.2f}/{self.monthly_cap:.2f}, "
                f"run ${self.run_spent:.2f}/{self.per_run_cap:.2f})")

    def record(self, cost: float) -> None:
        self.month_spent += cost
        self.run_spent += cost

    def summary(self) -> dict:
        return {"month_spent_usd": round(self.month_spent, 4), "monthly_cap_usd": self.monthly_cap,
                "run_spent_usd": round(self.run_spent, 4), "per_run_cap_usd": self.per_run_cap,
                "skipped_calls": self.skipped_calls}
