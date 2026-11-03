"""AI providers behind one small interface, so others (OpenAI, Google) can be added later.

complete() returns (text, input_tokens, output_tokens, stop_reason).
"""
import json
import os


class ProviderError(RuntimeError):
    pass


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, max_retries=2)
        self.client = client

    @classmethod
    def from_env(cls, env_name: str = "ANTHROPIC_API_KEY") -> "AnthropicProvider | None":
        key = os.environ.get(env_name, "").strip()
        return cls(key) if key else None

    def complete(self, *, model: str, system: str, prompt: str, max_tokens: int,
                 schema: dict | None = None, effort: str | None = None):
        output_config = {}
        if effort:
            output_config["effort"] = effort
        if schema:
            # Structured outputs: the reply's text block is guaranteed to be JSON for `schema`.
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs = {"model": model, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": prompt}]}
        if output_config:
            kwargs["output_config"] = output_config
        try:
            r = self.client.messages.create(**kwargs)
        except Exception as e:  # SDK already retried 429/5xx/connection errors
            raise ProviderError(f"{type(e).__name__}: {str(e)[:200]}") from e
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        return text, r.usage.input_tokens, r.usage.output_tokens, r.stop_reason


class MockProvider:
    """Practice mode: returns whatever the caller's mock function builds. Costs nothing."""
    name = "mock"

    def complete(self, *, model, system, prompt, max_tokens, schema=None, effort=None, mock=None):
        out = mock() if mock else ""
        text = out if isinstance(out, str) else json.dumps(out)
        return text, len(system + prompt) // 4, len(text) // 4, "end_turn"
