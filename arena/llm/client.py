"""Provider-agnostic AI client: picks the model for a role from config/models.yaml, checks the
budget, logs every call to llm_usage, validates JSON output and retries once if it is invalid."""
import json
import re
from typing import Any, Callable

from arena.llm.budget import AIUnavailable, BudgetExceeded, BudgetGuard
from arena.llm.providers import MockProvider, ProviderError

CHARS_PER_TOKEN = 3.5  # rough and slightly pessimistic, used only for the budget estimate


class InvalidOutput(ValueError):
    pass


def _parse_json(text: str) -> Any:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    return json.loads(text)


class LLMClient:
    def __init__(self, db, run_id, models_cfg: dict, guard: BudgetGuard, providers: dict,
                 practice: bool = False):
        self.db, self.run_id, self.cfg, self.guard = db, run_id, models_cfg, guard
        self.providers, self.practice = providers, practice
        self.failures: list[dict] = []
        self.calls = 0
        self.down: str | None = None   # set after a provider error; no more calls this run

    @property
    def available(self) -> bool:
        return self.practice or (bool(self.providers) and not self.down)

    def _model(self, role: str, model_key: str | None = None) -> dict:
        """model_key (a team's choice for this role) overrides the default in models.yaml."""
        return self.cfg["models"][model_key or self.cfg["roles"][role]]

    def _cost(self, m: dict, tin: int, tout: int) -> float:
        return tin / 1e6 * m["input_per_mtok"] + tout / 1e6 * m["output_per_mtok"]

    def _call(self, role, system, prompt, max_tokens, schema, mock, model_key=None):
        m = self._model(role, model_key)
        if self.down:
            raise AIUnavailable(self.down)
        if self.practice:
            provider = MockProvider()
            text, tin, tout, stop = provider.complete(model=m["model"], system=system, prompt=prompt,
                                                      max_tokens=max_tokens, schema=schema, mock=mock)
            cost, provider_name, model = 0.0, "mock", "practice-mock"
        else:
            provider = self.providers.get(m["provider"])
            if provider is None:
                raise ProviderError(f"no API key for provider '{m['provider']}'")
            worst = self._cost(m, int(len(system + prompt) / CHARS_PER_TOKEN) + 50, max_tokens)
            self.guard.check(worst)
            text, tin, tout, stop = provider.complete(model=m["model"], system=system, prompt=prompt,
                                                      max_tokens=max_tokens, schema=schema,
                                                      effort=m.get("effort"))
            cost, provider_name, model = self._cost(m, tin, tout), provider.name, m["model"]
        self.guard.record(cost)
        self.calls += 1
        self.db.insert("llm_usage", [{"run_id": self.run_id, "provider": provider_name, "model": model,
                                      "role": role, "input_tokens": tin, "output_tokens": tout,
                                      "cost_usd": round(cost, 6)}])
        if stop == "refusal":
            raise InvalidOutput("model declined the request")
        if stop == "max_tokens":
            raise InvalidOutput("output was cut off (max_tokens)")
        return text

    def json(self, role: str, system: str, prompt: str, schema: dict,
             validate: Callable[[Any], Any], max_tokens: int = 2000, mock: Callable | None = None,
             model_key: str | None = None):
        """Returns validate(parsed_json), or None after two invalid attempts (logged).
        BudgetExceeded propagates so the caller can stop making AI calls."""
        attempt_prompt = prompt
        for attempt in (1, 2):
            try:
                text = self._call(role, system, attempt_prompt, max_tokens, schema, mock, model_key)
                return validate(_parse_json(text))
            except BudgetExceeded:
                raise
            except ProviderError as e:
                return self._provider_down(role, e)
            except (InvalidOutput, ValueError, TypeError, KeyError) as e:
                if attempt == 2:
                    self.failures.append({"role": role, "error": f"invalid output twice: {str(e)[:200]}"})
                    return None
                attempt_prompt = (prompt + "\n\nYour previous reply was invalid: "
                                  f"{str(e)[:300]}\nReply again with valid JSON only.")
        return None

    def text(self, role: str, system: str, prompt: str, max_tokens: int = 800,
             mock: Callable | None = None, model_key: str | None = None) -> str | None:
        try:
            out = self._call(role, system, prompt, max_tokens, None, mock, model_key).strip()
            return out or None
        except BudgetExceeded:
            raise
        except ProviderError as e:
            return self._provider_down(role, e)
        except InvalidOutput as e:
            self.failures.append({"role": role, "error": str(e)})
            return None

    def _provider_down(self, role, e):
        """A provider error (no credit, bad key, outage) is not the model's fault: stop all AI
        calls for this run and let the caller retry the same work on the next run."""
        self.failures.append({"role": role, "error": str(e)})
        self.down = f"AI provider unavailable: {str(e)[:200]}"
        raise AIUnavailable(self.down)


def make_llm(db, run_id, models_cfg: dict, guard: BudgetGuard, dry_run: bool) -> LLMClient:
    from arena.llm.providers import AnthropicProvider
    practice = dry_run or models_cfg.get("mode", "practice") != "live"
    providers = {}
    if not practice:
        p = AnthropicProvider.from_env(models_cfg["providers"]["anthropic"]["api_key_env"])
        if p:
            providers["anthropic"] = p
    return LLMClient(db, run_id, models_cfg, guard, providers, practice=practice)
