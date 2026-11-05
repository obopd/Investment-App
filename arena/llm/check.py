"""One tiny AI call to check that the API key and credit work (costs a fraction of a cent).

    python -m arena.llm.check
"""
import sys

from arena import config
from arena.llm.providers import AnthropicProvider, ProviderError


def main() -> int:
    cfg = config.load("models")
    provider = AnthropicProvider.from_env(cfg["providers"]["anthropic"]["api_key_env"])
    if provider is None:
        print("FAILED: ANTHROPIC_API_KEY is not set in GitHub Secrets.")
        return 1
    m = cfg["models"][cfg["roles"]["fact_cards"]]
    try:
        text, tin, tout, _ = provider.complete(model=m["model"], system="Reply with one word.",
                                               prompt="Say OK.", max_tokens=200, effort="low")
    except ProviderError as e:
        msg = str(e)
        print("FAILED:", msg[:400])
        if "credit balance is too low" in msg:
            print("\nThe key works, but its organization has no credit. Create the key in the same "
                  "organization that shows your $10 balance (platform.claude.com -> Billing).")
        elif "authentication" in msg.lower() or "401" in msg:
            print("\nThe key was rejected. Copy it again and update the GitHub secret ANTHROPIC_API_KEY.")
        return 1
    cost = tin / 1e6 * m["input_per_mtok"] + tout / 1e6 * m["output_per_mtok"]
    print(f"OK: {m['model']} answered {text.strip()!r} ({tin} in / {tout} out tokens, ${cost:.5f}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
