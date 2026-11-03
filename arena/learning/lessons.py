"""Lesson book: one-sentence lessons from post-mortems, scored by whether the trades that used
them beat the benchmark.

score = (times_helpful + 1) / (times_used + 2), so a new lesson starts at a neutral 0.5 and only
moves with evidence. times_used counts closed trades whose opening decision had the lesson in
its prompt; times_helpful counts those that beat SPY over the same period.
"""
from arena.facts.extract import similarity
from arena.lookahead import to_utc


def lesson_score(helpful: int, used: int) -> float:
    return (helpful + 1) / (used + 2)


def _book_filters(team_id: int, shared: bool) -> dict:
    return {"team_id": ("is", None)} if shared else {"team_id": team_id}


def top_lessons(db, team_id: int, shared: bool, cutoff, n: int) -> list[dict]:
    """Active lessons written before the decision time, best first."""
    rows = db.select("lessons", {**_book_filters(team_id, shared), "active": True},
                     columns="id,text,score,times_used,times_helpful,created_at", order="id.asc")
    rows = [r for r in rows if to_utc(r["created_at"]) < cutoff]
    rows.sort(key=lambda r: (-r["score"], -r["id"]))
    return rows[:n]


def add_lesson(db, team_id: int, shared: bool, text: str, post_mortem_id: int, cfg: dict, now) -> dict:
    """Insert a lesson unless an active near-duplicate exists (then the old one is kept)."""
    existing = db.select("lessons", {**_book_filters(team_id, shared), "active": True},
                         columns="id,text", order="id.asc")
    for e in existing:
        if similarity(e["text"], text) >= cfg["duplicate_similarity"]:
            return {"merged_into": e["id"]}
    row = db.insert("lessons", [{"team_id": None if shared else team_id, "text": text,
                                 "post_mortem_id": post_mortem_id, "times_used": 0, "times_helpful": 0,
                                 "score": 0.5, "active": True, "created_at": now}], returning=True)[0]
    return {"added": row["id"]}


def score_lessons_for_trade(db, closed_trade: dict) -> list[int]:
    """Credit or debit every lesson that was in the prompt of the decision that opened the trade."""
    did = closed_trade.get("entry_decision_id")
    if not did:
        return []
    dec = db.select("decisions", {"id": did}, columns="lessons_used")
    ids = (dec[0]["lessons_used"] if dec else None) or []
    bench = closed_trade.get("benchmark_return")
    helped = bench is not None and closed_trade["pnl_pct"] > bench
    for lid in ids:
        rows = db.select("lessons", {"id": lid})
        if not rows:
            continue
        l = rows[0]
        used, helpful = (l["times_used"] or 0) + 1, (l["times_helpful"] or 0) + (1 if helped else 0)
        db.update("lessons", {"id": lid}, {"times_used": used, "times_helpful": helpful,
                                           "score": lesson_score(helpful, used)})
    return ids


def maintain(db, team_id: int, shared: bool, cfg: dict) -> dict:
    """Deactivate proven-bad lessons, merge near-duplicates, cap the book size. Nothing is deleted."""
    rows = db.select("lessons", {**_book_filters(team_id, shared), "active": True}, order="id.asc")
    off = set()
    for r in rows:
        if (r["times_used"] or 0) >= cfg["min_uses_to_judge"] and r["score"] < cfg["deactivate_below"]:
            off.add(r["id"])
    # Near-duplicates: keep the better-scored (then older) lesson, fold the other's record into it.
    live = [r for r in rows if r["id"] not in off]
    for i, a in enumerate(live):
        if a["id"] in off:
            continue
        for b in live[i + 1:]:
            if b["id"] in off or similarity(a["text"], b["text"]) < cfg["duplicate_similarity"]:
                continue
            keep, drop = (a, b) if (a["score"], -a["id"]) >= (b["score"], -b["id"]) else (b, a)
            used = (keep["times_used"] or 0) + (drop["times_used"] or 0)
            helpful = (keep["times_helpful"] or 0) + (drop["times_helpful"] or 0)
            keep.update(times_used=used, times_helpful=helpful, score=lesson_score(helpful, used))
            db.update("lessons", {"id": keep["id"]}, {"times_used": used, "times_helpful": helpful,
                                                      "score": keep["score"]})
            off.add(drop["id"])
            if drop is a:
                break
    remaining = sorted((r for r in rows if r["id"] not in off), key=lambda r: (-r["score"], -r["id"]))
    for r in remaining[cfg["max_active_per_team"]:]:
        off.add(r["id"])
    for lid in off:
        db.update("lessons", {"id": lid}, {"active": False})
    return {"deactivated": len(off)}
