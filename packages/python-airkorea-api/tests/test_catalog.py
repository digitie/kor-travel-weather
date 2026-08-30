from __future__ import annotations

import pytest

from airkorea import (
    api_catalog,
    api_catalog_dicts,
    api_catalog_for_debug_input,
    api_catalog_for_method,
    get_api_catalog_entry,
)
from airkorea.client import SUPPORTED_ENDPOINTS


def test_api_catalog_covers_supported_endpoints_with_human_readable_dataset_names() -> None:
    catalog = api_catalog()
    supported = {
        (service_name, endpoint)
        for service_name, endpoints in SUPPORTED_ENDPOINTS.items()
        for endpoint in endpoints
    }

    assert {(entry.service_name, entry.endpoint) for entry in catalog} == supported
    assert all(entry.dataset_name.startswith("한국환경공단_") for entry in catalog)
    assert all(
        entry.service_key_url.startswith("https://www.data.go.kr/data/")
        for entry in catalog
    )


def test_api_catalog_dicts_are_streamlit_friendly() -> None:
    rows = api_catalog_dicts()

    assert rows[0]["dataset_name"]
    assert rows[0]["service_key_url"] == rows[0]["portal_url"]
    assert isinstance(rows[0]["method_names"], list)


def test_catalog_lookup_for_method_and_call_input() -> None:
    entries = api_catalog_for_method("station_measurements")

    assert entries[0].dataset_name == "한국환경공단_에어코리아_대기오염정보"
    assert entries[0].endpoint == "getMsrstnAcctoRltmMesureDnsty"

    call_entries = api_catalog_for_debug_input(
        "call",
        {
            "service_name": "MsrstnInfoInqireSvc",
            "endpoint": "getMsrstnList",
        },
    )

    assert call_entries[0].dataset_name == "한국환경공단_에어코리아_측정소정보"
    assert call_entries[0].service_key_param == "serviceKey"


def test_user_support_catalog_uses_capital_service_key_param() -> None:
    entry = get_api_catalog_entry("usersportsvc", "getSvckeyDalyStats")

    assert entry.dataset_name == "한국환경공단_에어코리아_사용자 지원"
    assert entry.service_key_param == "ServiceKey"


def test_catalog_lookup_rejects_unknown_endpoint() -> None:
    with pytest.raises(ValueError):
        get_api_catalog_entry("MsrstnInfoInqireSvc", "missing")
