"""SDK의 정상 빈 응답은 opt-in에서 한 번 재확인하고 미확인은 승인하지 않는다."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

from test_export_sdk import fixture

from kortravelweather.export_sdk_collection import SdkCollectionContext
from kortravelweather.export_sdk_pages import BeachPageAssembly
from kortravelweather.models import KST
from kortravelweather.providers import khoa, krex


def test_khoa_empty_first_page_is_rechecked_once_with_same_page_context(monkeypatch):
    import test_export_sdk

    monkeypatch.setattr(test_export_sdk, "AT", datetime.now(KST))
    _, _, place, _ = fixture("khoa")
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def abeach_index(self, **kwargs):
            calls.append(kwargs)
            present = len(calls) > 1
            return SimpleNamespace(
                items=[place] if present else [],
                total_count=len(place.raw["rows"]) if present else 0,
                page_no=1, num_of_rows=kwargs["num_of_rows"], has_next_page=False,
            )

    monkeypatch.setattr(khoa, "KhoaClient", Client)
    assembly = BeachPageAssembly()
    result = khoa.fetch_beach_index(api_key="fixture", export_assembly=assembly)
    assert len(result) == 1 and assembly.receipt.complete
    assert calls == [{"page_no": 1, "num_of_rows": 100}] * 2


def test_krex_empty_after_sdk_lookback_rechecks_current_once_and_validates_page(monkeypatch):
    import test_export_sdk

    monkeypatch.setattr(test_export_sdk, "AT", datetime.now(KST))
    _, _, record, _ = fixture("krex")
    calls = []

    class Client:
        restarea = None

        def __init__(self):
            self.restarea = self

        async def latest_weather(self, *, lookback_hours):
            calls.append(lookback_hours)
            rows = [record] if len(calls) > 1 else []
            return SimpleNamespace(
                items=rows, raw={"list": [row.raw for row in rows]}, total_count=len(rows),
                page_no=1, num_of_rows=1000,
            )

    context = SdkCollectionContext(collector=None)
    result = asyncio.run(krex.fetch_restarea_weather(
        Client(), lookback_hours=24, export_page_observer=context.observe_krex_page,
    ))
    assert result == [record] and calls == [24, 0] and context.complete
