"""Article -> fact cards: short, checkable claims with a direction and a horizon.

Each article is processed once (articles.facts_extracted_at). Near-identical claims about the
same ticker are merged into one card, but every source that made the claim is recorded in
fact_card_sources (that is what corroboration and trust scoring use later).
"""
import re
from datetime import timedelta

from pydantic import BaseModel, Field, ValidationError, field_validator

from arena.llm.budget import BudgetExceeded

DIRECTIONS = ("positive", "negative", "neutral")
CLAIM_TYPES = ("earnings", "guidance", "product", "legal", "macro", "rumor", "other")

SYSTEM = (
    "You turn financial news headlines into short, checkable fact cards for a research "
    "simulation. Only use what the text says; never add outside knowledge. A claim must be "
    "about one company (ticker) or the macro economy (ticker null). Skip marketing, how-to "
    "and lifestyle posts entirely. direction = expected effect on that stock's price relative "
    "to the market. horizon_days = 5 for news the market reacts to quickly, 20 for slower "
    "effects. confidence = how clearly the text supports the claim (0-1)."
)

SCHEMA = {
    "type": "object",
    "properties": {"cards": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "article_id": {"type": "integer"},
            "ticker": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "claim": {"type": "string"},
            "direction": {"type": "string", "enum": list(DIRECTIONS)},
            "claim_type": {"type": "string", "enum": list(CLAIM_TYPES)},
            "horizon_days": {"type": "integer", "enum": [5, 20]},
            "confidence": {"type": "number"},
        },
        "required": ["article_id", "ticker", "claim", "direction", "claim_type", "horizon_days", "confidence"],
        "additionalProperties": False,
    }}},
    "required": ["cards"],
    "additionalProperties": False,
}


class Card(BaseModel):
    article_id: int
    ticker: str | None
    claim: str = Field(min_length=5, max_length=300)
    direction: str
    claim_type: str
    horizon_days: int
    confidence: float = Field(ge=0, le=1)

    @field_validator("direction")
    @classmethod
    def _dir(cls, v):
        if v not in DIRECTIONS:
            raise ValueError(f"bad direction {v}")
        return v

    @field_validator("claim_type")
    @classmethod
    def _type(cls, v):
        if v not in CLAIM_TYPES:
            raise ValueError(f"bad claim_type {v}")
        return v

    @field_validator("horizon_days")
    @classmethod
    def _h(cls, v):
        if v not in (5, 20):
            raise ValueError("horizon_days must be 5 or 20")
        return v


def make_validator(article_ids: set[int], universe: set[str]):
    def validate(data):
        try:
            cards = [Card(**c) for c in data["cards"]]
        except (ValidationError, TypeError) as e:
            raise ValueError(str(e)[:300]) from e
        out = []
        for c in cards:
            if c.article_id not in article_ids:
                raise ValueError(f"article_id {c.article_id} was not in the input")
            if c.ticker is not None:
                c.ticker = c.ticker.upper()
                if c.ticker not in universe:
                    continue  # claims about companies outside the universe are dropped
            elif c.claim_type != "macro":
                continue
            out.append(c)
        return out
    return validate


# ---------- Practice-mode stand-in ----------
_POS = re.compile(r"\b(beat|beats|raise[sd]?|record|surge[sd]?|jump[sd]?|upgrade[sd]?|approval|wins?)\b", re.I)
_NEG = re.compile(r"\b(miss(es|ed)?|cut[s]?|lawsuit|probe|fall[s]?|drop[s]?|downgrade[sd]?|recall|plunge[sd]?|delay)\b", re.I)


def mock_cards(articles: list[dict]) -> dict:
    cards = []
    for a in articles:
        for t in a.get("tickers", [])[:1]:
            text = a["title"]
            direction = "positive" if _POS.search(text) else "negative" if _NEG.search(text) else "neutral"
            ctype = "earnings" if re.search(r"earnings|results|quarter", text, re.I) else "other"
            cards.append({"article_id": a["id"], "ticker": t, "claim": f"[practice] {text}"[:300],
                          "direction": direction, "claim_type": ctype, "horizon_days": 5,
                          "confidence": 0.5})
    return {"cards": cards}


# ---------- Deduplication ----------
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "is", "its", "will", "with", "by", "practice"}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def similarity(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def find_duplicate(card: Card, existing: list[dict], threshold: float) -> dict | None:
    for e in existing:
        if e["ticker"] == card.ticker and e["direction"] == card.direction \
                and similarity(e["claim"], card.claim) >= threshold:
            return e
    return None


def _prompt(batch: list[dict], source_names: dict[int, str]) -> str:
    lines = ["Articles (id | source | tickers mentioned | title | snippet):"]
    for a in batch:
        snippet = (a.get("snippet") or "")[:300]
        lines.append(f"{a['id']} | {source_names.get(a['source_id'], '?')} | "
                     f"{','.join(a.get('tickers') or []) or '-'} | {a['title']} | {snippet}")
    lines.append("\nReturn JSON {\"cards\": [...]}. Zero, one or more cards per article.")
    return "\n".join(lines)


def extract_fact_cards(db, llm, universe: set[str], cfg: dict, now) -> dict:
    summary = {"articles_processed": 0, "cards_new": 0, "cards_merged": 0, "skipped_budget": False}
    since = now - timedelta(days=cfg.get("max_age_days", 3))
    # Practice and live keep separate "done" markers, so going live re-reads recent articles
    # with the real model instead of inheriting placeholder cards.
    marker = "practice_extracted_at" if llm.practice else "facts_extracted_at"
    pending = db.select("articles", {marker: ("is", None), "published_at": ("gte", since)},
                        order="published_at.asc,id.asc")
    # Only articles that can produce a usable claim: tagged companies, filings, or the Fed.
    pending = [a for a in pending if a.get("tickers") or a.get("kind") == "filing"
               or "federalreserve" in (a.get("url") or "")][: cfg.get("max_articles_per_run", 150)]
    if not pending:
        return summary

    sources = {s["id"]: s["name"] for s in db.select("sources", columns="id,name")}
    window = now - timedelta(days=cfg.get("dedupe_window_days", 7))
    existing = db.select("fact_cards", {"created_at": ("gte", window), "practice": llm.practice},
                         columns="id,ticker,claim,direction", order="id.asc")
    threshold = cfg.get("dedupe_similarity", 0.8)
    by_id = {a["id"]: a for a in pending}
    size = cfg.get("batch_size", 10)

    for i in range(0, len(pending), size):
        batch = pending[i:i + size]
        ids = {a["id"] for a in batch}
        try:
            cards = llm.json("fact_cards", SYSTEM, _prompt(batch, sources), SCHEMA,
                             make_validator(ids, universe), max_tokens=4000,
                             mock=lambda b=batch: mock_cards(b))
        except BudgetExceeded:
            summary["skipped_budget"] = True
            break
        for c in cards or []:
            art = by_id[c.article_id]
            dup = find_duplicate(c, existing, threshold)
            if dup:
                card_id = dup["id"]
                summary["cards_merged"] += 1
            else:
                row = db.insert("fact_cards", [{
                    "article_id": c.article_id, "ticker": c.ticker, "claim": c.claim,
                    "direction": c.direction, "claim_type": c.claim_type,
                    "horizon_days": c.horizon_days, "confidence": c.confidence,
                    "published_at": art["published_at"], "practice": llm.practice,
                    "created_at": now}], returning=True)[0]
                card_id = row["id"]
                existing.append({"id": card_id, "ticker": c.ticker, "claim": c.claim, "direction": c.direction})
                summary["cards_new"] += 1
            db.upsert("fact_card_sources", [{"fact_card_id": card_id, "article_id": c.article_id,
                                             "source_id": art["source_id"]}],
                      on_conflict="fact_card_id,article_id", ignore_duplicates=True)
        # Mark processed even when no cards came out (or output was invalid twice): never pay twice.
        db.update("articles", {"id": ("in", sorted(ids))}, {marker: now})
        summary["articles_processed"] += len(batch)
    return summary
