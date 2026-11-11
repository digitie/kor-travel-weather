from unittest.mock import Mock

import kma
import pytest
from kortravelweather_dagster.resources import KmaClientResource


@pytest.mark.parametrize(
    ("factory_name", "client_name"),
    [("create_client", "KmaClient"), ("create_data_client", "DataGoKrClient")],
)
def test_kma_clients_use_configured_request_limit(monkeypatch, factory_name, client_name):
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATA_GO_KR_SERVICE_KEY", "fixture-key")
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_KMA_MAX_RPS", "10")
    client = Mock()
    monkeypatch.setattr(kma, client_name, client)

    getattr(KmaClientResource(), factory_name)()

    assert client.call_args.kwargs["max_rps"] == 10.0
