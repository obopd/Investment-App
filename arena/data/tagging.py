"""Tags headlines with universe tickers using symbols ($AAPL, NASDAQ: AAPL) and company names."""
import re

_EXCHANGE = r"(?:NYSE|NASDAQ|Nasdaq|NasdaqGS|NasdaqGM|NYSE American)"


class TickerTagger:
    def __init__(self, stocks: list[dict]):
        self.patterns = []
        for s in stocks:
            t = re.escape(s["ticker"])
            parts = [rf"\${t}(?![A-Za-z])", rf"{_EXCHANGE}\s*:\s*{t}(?![A-Za-z])"]
            # Case-sensitive, whole-word company names; (?<!\w)/(?!\w) work with & and '.
            parts += [rf"(?<!\w){re.escape(a)}(?!\w)" for a in s.get("aliases", [])]
            self.patterns.append((s["ticker"], re.compile("|".join(parts))))

    def tag(self, *texts: str) -> list[str]:
        text = " ".join(t for t in texts if t)
        return [ticker for ticker, rx in self.patterns if rx.search(text)]
