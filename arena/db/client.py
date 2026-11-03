"""Database access.

SupabaseDB talks to Supabase's REST API (PostgREST) directly with `requests`, which keeps
dependencies small. MemoryDB has the same interface and is used by tests and --dry-run, so the
whole pipeline can run with no accounts and no cost.
"""
import os
from datetime import date, datetime
from itertools import count

import requests

from arena.http import TIMEOUT

CHUNK = 500  # rows per write request


class DBError(RuntimeError):
    pass


def _jsonable(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _as_instant(s: str):
    if len(s) > 10 and s[4] == "-" and "T" in s:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
    return s


def _clean(row: dict) -> dict:
    return {k: _jsonable(v) for k, v in row.items()}


class SupabaseDB:
    def __init__(self, url: str, key: str, session: requests.Session | None = None):
        # Accept the project URL with or without the "/rest/v1" API path people often copy.
        url = url.strip().rstrip("/")
        if url.endswith("/rest/v1"):
            url = url[: -len("/rest/v1")]
        self.base = url + "/rest/v1"
        self.s = session or requests.Session()
        self.s.headers["apikey"] = key
        # Legacy service_role keys are JWTs and go in Authorization; new sb_secret_ keys don't.
        if key.startswith("eyJ"):
            self.s.headers["Authorization"] = f"Bearer {key}"

    @classmethod
    def from_env(cls) -> "SupabaseDB":
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not url or not key:
            raise DBError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set (GitHub Secrets).")
        return cls(url, key)

    def _check(self, r: requests.Response):
        if r.status_code >= 400:
            hint = ""
            if r.status_code == 404 or "does not exist" in r.text or "schema cache" in r.text:
                hint = " (Did you run arena/db/schema.sql in the Supabase SQL Editor?)"
            raise DBError(f"Supabase {r.request.method} {r.url.split('?')[0]} -> {r.status_code}: {r.text[:300]}{hint}")
        return r

    @staticmethod
    def _filters(filters: dict | None) -> dict:
        """{"col": v} = equals; {"col": ("gte", v)}; ("in", [..]); ("is", None)."""
        params = {}
        for col, val in (filters or {}).items():
            if isinstance(val, tuple):
                op, v = val
                if op == "in":
                    params[col] = "in.(" + ",".join(str(_jsonable(x)) for x in v) + ")"
                elif op == "is":
                    params[col] = "is.null" if v is None else f"is.{str(v).lower()}"
                else:
                    params[col] = f"{op}.{_jsonable(v)}"
            else:
                params[col] = f"eq.{_jsonable(val)}"
        return params

    PAGE = 1000  # Supabase returns at most 1000 rows per request by default

    def select(self, table, filters=None, columns="*", order=None, limit=None) -> list[dict]:
        """Without a limit, pages through every matching row (so sums are never truncated)."""
        params = {"select": columns, **self._filters(filters)}
        if order:
            params["order"] = order              # e.g. "date.desc"
        if limit:
            params["limit"] = str(limit)
            r = self._check(self.s.get(f"{self.base}/{table}", params=params, timeout=TIMEOUT))
            return r.json()
        rows, offset = [], 0
        while True:
            page = dict(params, limit=str(self.PAGE), offset=str(offset))
            r = self._check(self.s.get(f"{self.base}/{table}", params=page, timeout=TIMEOUT))
            batch = r.json()
            rows.extend(batch)
            if len(batch) < self.PAGE:
                return rows
            if not order:
                # Offset paging without a unique sort order can skip or repeat rows.
                raise DBError(f"{table}: more than {self.PAGE} rows; pass an order to page safely")
            offset += self.PAGE

    def _write(self, table, rows, prefer: list[str], params=None, returning=False) -> list[dict]:
        out = []
        prefer = prefer + ["return=representation" if returning else "return=minimal"]
        for i in range(0, len(rows), CHUNK):
            chunk = [_clean(r) for r in rows[i:i + CHUNK]]
            r = self._check(self.s.post(f"{self.base}/{table}", json=chunk, params=params or {},
                                        headers={"Prefer": ",".join(prefer)}, timeout=60))
            if returning:
                out.extend(r.json())
        return out

    def insert(self, table, rows: list[dict], returning=False) -> list[dict]:
        return self._write(table, rows, [], returning=returning)

    def upsert(self, table, rows: list[dict], on_conflict: str, ignore_duplicates=False,
               returning=False) -> list[dict]:
        """With ignore_duplicates=True, `returning` gives only the rows that were new."""
        res = "resolution=ignore-duplicates" if ignore_duplicates else "resolution=merge-duplicates"
        return self._write(table, rows, [res], params={"on_conflict": on_conflict}, returning=returning)

    def update(self, table, filters: dict, values: dict) -> None:
        self._check(self.s.patch(f"{self.base}/{table}", params=self._filters(filters),
                                 json=_clean(values), headers={"Prefer": "return=minimal"},
                                 timeout=TIMEOUT))


    def delete(self, table, filters: dict) -> None:
        if not filters:
            raise DBError("refusing to delete without a filter")
        self._check(self.s.delete(f"{self.base}/{table}", params=self._filters(filters),
                                  headers={"Prefer": "return=minimal"}, timeout=TIMEOUT))


class MemoryDB:
    """In-memory stand-in for SupabaseDB with the same methods."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self._ids = count(1)

    def _t(self, table):
        return self.tables.setdefault(table, [])

    @staticmethod
    def _match(row, filters):
        for col, val in (filters or {}).items():
            cur = _jsonable(row.get(col))
            if isinstance(val, tuple):
                op, v = val
                if op == "in":
                    if cur not in [_jsonable(x) for x in v]:
                        return False
                    continue
                if op == "is":
                    if cur is not v:
                        return False
                    continue
                if cur is None:
                    return False
                v = _jsonable(v)
                if op in ("gte", "gt", "lte", "lt") and isinstance(cur, str) and isinstance(v, str):
                    # Compare timestamps as instants, not text ("...Z" vs "+00:00").
                    cur, v = _as_instant(cur), _as_instant(v)
                ok = {"gte": cur >= v, "gt": cur > v, "lte": cur <= v, "lt": cur < v, "neq": cur != v}[op]
                if not ok:
                    return False
            elif cur != _jsonable(val):
                return False
        return True

    def select(self, table, filters=None, columns="*", order=None, limit=None):
        rows = [dict(r) for r in self._t(table) if self._match(r, filters)]
        if order:
            # Apply "a.desc,b.asc" right-to-left so the first column wins (stable sort).
            for part in reversed(order.split(",")):
                col, _, direction = part.strip().partition(".")
                rows.sort(key=lambda r: (r.get(col) is None, _jsonable(r.get(col))),
                          reverse=direction == "desc")
        if limit:
            rows = rows[:limit]
        if columns != "*":
            cols = [c.strip() for c in columns.split(",")]
            rows = [{c: r.get(c) for c in cols} for r in rows]
        return rows

    def _with_id(self, row):
        row = _clean(row)
        row.setdefault("id", next(self._ids))
        return row

    def insert(self, table, rows, returning=False):
        new = [self._with_id(r) for r in rows]
        self._t(table).extend(new)
        return [dict(r) for r in new] if returning else []

    def upsert(self, table, rows, on_conflict, ignore_duplicates=False, returning=False):
        keys = [k.strip() for k in on_conflict.split(",")]
        t = self._t(table)
        index = {tuple(r.get(k) for k in keys): r for r in t}
        out = []
        for row in rows:
            row = _clean(row)
            k = tuple(row.get(c) for c in keys)
            if k in index:
                if ignore_duplicates:
                    continue
                index[k].update(row)
                out.append(dict(index[k]))
            else:
                row = self._with_id(row)
                t.append(row)
                index[k] = row
                out.append(dict(row))
        return out if returning else []

    def update(self, table, filters, values):
        for r in self._t(table):
            if self._match(r, filters):
                r.update(_clean(values))

    def delete(self, table, filters):
        if not filters:
            raise DBError("refusing to delete without a filter")
        self.tables[table] = [r for r in self._t(table) if not self._match(r, filters)]
