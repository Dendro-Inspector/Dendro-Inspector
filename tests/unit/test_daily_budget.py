import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from dendro_inspector.providers.base import ProviderUnavailableError
from dendro_inspector.providers.daily_budget import (
    LIMITS,
    MAX_OUTPUT,
    capped_response,
    reserve,
    transaction,
)


def test_concurrent_reservations_share_the_limit(tmp_path):
    path = tmp_path / "usage.db"

    def attempt(_):
        try:
            reserve(path, "gpt-5.4", 250_000)
        except ProviderUnavailableError:
            return False
        return True

    with ThreadPoolExecutor(4) as pool:
        assert sum(pool.map(attempt, range(4))) == 1
    reserve(path, "gpt-5.4-mini", LIMITS["gpt-5.4-mini"])
    with pytest.raises(ProviderUnavailableError):
        reserve(path, "gpt-5.4-mini", 1)


def test_expired_usage_releases_only_finished_reservations(tmp_path):
    path = tmp_path / "usage.db"
    finished = reserve(path, "gpt-5.4", 100_000)
    reserve(path, "gpt-5.4", 150_000)
    with transaction(path) as db:
        db.execute("UPDATE reservations SET finished_day='2000-01-01' WHERE id=?", (finished,))
    reserve(path, "gpt-5.4", 100_000)
    with pytest.raises(ProviderUnavailableError):
        reserve(path, "gpt-5.4", 1)
    with pytest.raises(ProviderUnavailableError):
        reserve(path, "other", 1)


def test_generation_reserves_before_sending_and_charges_actual_usage(tmp_path):
    path = tmp_path / "usage.db"
    model = "gpt-5.4"
    req = {"model": model, "input": "test", "text": {}}
    api = Mock()
    api.input_tokens.count = AsyncMock(return_value=SimpleNamespace(input_tokens=123))
    api.create = AsyncMock(return_value=SimpleNamespace(usage=SimpleNamespace(total_tokens=321)))
    client = Mock()
    client.with_options.return_value.responses = api
    asyncio.run(capped_response(client, path, req))
    client.with_options.assert_called_with(max_retries=0)
    api.create.assert_awaited_once_with(**req, max_output_tokens=MAX_OUTPUT)
    with transaction(path) as db:
        assert db.execute("SELECT tokens FROM reservations").fetchone() == (321,)
    reserve(path, model, 250_000 - 321)
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(capped_response(client, path, req))
    assert api.create.await_count == 1


@pytest.mark.parametrize("measurement", [None, 0, -1, True])
def test_invalid_count_stops_generation(tmp_path, measurement):
    api = Mock()
    api.input_tokens.count = AsyncMock(return_value=SimpleNamespace(input_tokens=measurement))
    api.create = AsyncMock()
    client = Mock()
    client.with_options.return_value.responses = api
    request = {"model": "gpt-5.4-mini", "input": "test", "text": {}}
    with pytest.raises(ProviderUnavailableError, match="input token count"):
        asyncio.run(capped_response(client, tmp_path / "usage.db", request))
    api.create.assert_not_awaited()


def test_lost_response_keeps_full_reservation_after_reopening(tmp_path):
    path = tmp_path / "usage.db"
    api = Mock()
    api.input_tokens.count = AsyncMock(return_value=SimpleNamespace(input_tokens=100))
    api.create = AsyncMock(side_effect=TimeoutError)
    client = Mock()
    client.with_options.return_value.responses = api
    model = "gpt-5.4"
    request = {"model": model, "input": "test", "text": {}}
    with pytest.raises(TimeoutError):
        asyncio.run(capped_response(client, path, request))
    with transaction(path) as db:
        row = db.execute(
            "SELECT tokens, finished_day = date('now', '+1 day') FROM reservations"
        ).fetchone()
    assert row == (100 + MAX_OUTPUT, 1)
    reserve(path, model, LIMITS[model] - 100 - MAX_OUTPUT)
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(capped_response(client, path, request))
    api.create.assert_awaited_once()


def test_lost_reservation_is_released_after_tomorrow(tmp_path):
    path = tmp_path / "usage.db"
    model = "gpt-5.4"
    lost = reserve(path, model, LIMITS[model])
    with pytest.raises(ProviderUnavailableError):
        reserve(path, model, 1)
    with transaction(path) as db:
        db.execute(
            "UPDATE reservations SET finished_day = date('now', '-1 day') WHERE id=?", (lost,)
        )
    reserve(path, model, LIMITS[model])


def test_legacy_pending_rows_stop_counting_after_tomorrow(tmp_path):
    path = tmp_path / "usage.db"
    with transaction(path) as db:
        db.execute("INSERT INTO reservations(model,tokens) VALUES ('gpt-5.4', 250000)")
    with transaction(path) as db:
        assert db.execute(
            "SELECT finished_day = date('now', '+1 day') FROM reservations"
        ).fetchone() == (1,)
