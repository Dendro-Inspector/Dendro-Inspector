"""Persistent reservations for the dedicated AKS OpenAI inspection job."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dendro_inspector.providers.base import ProviderUnavailableError

LIMITS = {"gpt-5.4": 250_000, "gpt-5.4-mini": 2_500_000}
MAX_OUTPUT = 8192


@contextmanager
def transaction(path: Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(path, timeout=15)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS reservations "
                "(id INTEGER PRIMARY KEY, model TEXT NOT NULL, tokens INTEGER NOT NULL, "
                "finished_day TEXT)"
            )
            yield db
    finally:
        db.close()


def reserve(path: Path, model: str, tokens: int) -> int:
    if model not in LIMITS or tokens <= 0:
        raise ProviderUnavailableError("Unsupported model or invalid token reservation")
    with transaction(path) as db:
        # Pending calls count across midnight. A lost response keeps its full reservation.
        db.execute("DELETE FROM reservations WHERE finished_day < date('now')")
        used = db.execute(
            "SELECT coalesce(sum(tokens), 0) FROM reservations WHERE model = ?", (model,)
        ).fetchone()[0]
        if tokens + used > LIMITS[model]:
            raise ProviderUnavailableError(f"Daily token limit reached for {model}")
        row = db.execute("INSERT INTO reservations(model,tokens) VALUES (?,?)", (model, tokens))
        assert row.lastrowid is not None
        return row.lastrowid


async def capped_response(client: Any, path: Path, request: dict[str, Any]) -> Any:
    model = request["model"]
    if model not in LIMITS:
        raise ProviderUnavailableError(f"Model outside AKS allowlist: {model}")
    api = client.with_options(max_retries=0).responses
    count = await api.input_tokens.count(model=model, input=request["input"], text=request["text"])
    if type(count.input_tokens) is not int or count.input_tokens <= 0:
        raise ProviderUnavailableError("Missing input token count")
    reservation = reserve(path, model, count.input_tokens + MAX_OUTPUT)
    response = await api.create(**request, max_output_tokens=MAX_OUTPUT)
    actual = getattr(getattr(response, "usage", None), "total_tokens", None)
    if type(actual) is int and actual > 0:
        with transaction(path) as db:
            db.execute(
                "UPDATE reservations SET tokens=?, finished_day=date('now') WHERE id=?",
                (actual, reservation),
            )
    return response
