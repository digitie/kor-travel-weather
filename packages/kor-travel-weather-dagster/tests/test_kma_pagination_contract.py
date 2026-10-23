from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from kma import KmaClient, KmaParseError, PaginationReceipt, ResponseMetadata
from kortravelweather_dagster.kma_weather import (
    KMA_ULTRA_SHORT_NOWCAST,
    WeatherTarget,
    _metadata_dict,
    response_source_key,
    stage_grid,
)

from kortravelweather.models import WeatherLocation


def test_real_provider_pagination_reaches_staged_lineage() -> None:
    async def check():
        calls = []

        def respond(request):
            calls.append(request)
            params = request.url.params
            row = {
                "baseDate": params["base_date"],
                "baseTime": params["base_time"],
                "nx": 60,
                "ny": 127,
                "category": "T1H",
                "obsrValue": "18.4",
            }
            return httpx.Response(
                200,
                json={
                    "response": {
                        "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                        "body": {
                            "pageNo": 1,
                            "numOfRows": 1000,
                            "totalCount": 1,
                            "items": {"item": [row]},
                        },
                    }
                },
            )

        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(respond)) as session,
            KmaClient("private-key", session=session, retries=0) as client,
        ):
            staged = await stage_grid(
                client=client,
                target=WeatherTarget(
                    WeatherLocation(
                        location_id="seoul",
                        name="서울",
                        latitude=37.5,
                        longitude=127,
                        nx=60,
                        ny=127,
                    )
                ),
                base_datasets=frozenset({KMA_ULTRA_SHORT_NOWCAST}),
            )
        assert len(calls) == 1 and len(staged) == 1
        source = staged[0].source_record
        receipt = source["payload"]["response_metadata"]["pagination"]
        assert receipt == {
            "expected_total_count": 1,
            "received_item_count": 1,
            "page_count": 1,
            "complete": True,
        }
        assert "private-key" not in str(source)
        assert len(staged[0].values) == 1

    asyncio.run(check())


def test_real_provider_partial_page_cannot_return_staged_success() -> None:
    async def check():
        def respond(request):
            return httpx.Response(
                200,
                json={
                    "response": {
                        "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                        "body": {
                            "pageNo": 1,
                            "numOfRows": 1000,
                            "totalCount": 2,
                            "items": {"item": [{"category": "T1H", "obsrValue": "18.4"}]},
                        },
                    }
                },
            )

        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(respond)) as session,
            KmaClient("private-key", session=session, retries=0) as client,
        ):
            with pytest.raises(KmaParseError):
                await stage_grid(
                    client=client,
                    target=WeatherTarget(
                        WeatherLocation(
                            location_id="seoul",
                            name="서울",
                            latitude=37.5,
                            longitude=127,
                            nx=60,
                            ny=127,
                        )
                    ),
                    base_datasets=frozenset({KMA_ULTRA_SHORT_NOWCAST}),
                )

    asyncio.run(check())


def test_receipt_identity_ignores_fetch_clock_but_distinguishes_proof() -> None:
    receipt = PaginationReceipt(
        expected_total_count=1, received_item_count=1, page_count=1, complete=True
    )
    metadata = ResponseMetadata(
        provider="kma",
        service_name="village",
        endpoint="now",
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        pagination=receipt,
    )
    rows = [SimpleNamespace(raw={"category": "T1H"}, metadata=metadata)]
    before = response_source_key("d", "x", rows)
    rows[0].metadata = metadata.model_copy(
        update={"collected_at": datetime(2026, 2, 1, tzinfo=UTC)}
    )
    assert response_source_key("d", "x", rows) == before
    rows[0].metadata = metadata.model_copy(update={"pagination": None})
    assert response_source_key("d", "x", rows) != before
    assert "pagination" not in _metadata_dict({"pagination": {"complete": "secret"}})
