from unittest.mock import Mock

import kma
import pytest
from kortravelweather_dagster.resources import KmaClientResource

from kortravelweather.settings import WeatherSettings


@pytest.mark.parametrize(
    ("factory_name", "client_name"),
    [("create_client", "KmaClient"), ("create_data_client", "DataGoKrClient")],
)
@pytest.mark.parametrize("max_rps", [10.0, 20.0])
def test_kma_clients_use_configured_request_limit(
    monkeypatch, factory_name, client_name, max_rps
):
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATA_GO_KR_SERVICE_KEY", "fixture-key")
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_KMA_MAX_RPS", str(max_rps))
    client = Mock()
    monkeypatch.setattr(kma, client_name, client)

    getattr(KmaClientResource(), factory_name)()

    assert client.call_args.kwargs["max_rps"] == max_rps


def test_kma_default_request_limit_is_twenty_per_second(monkeypatch):
    monkeypatch.delenv("KOR_TRAVEL_WEATHER_KMA_MAX_RPS", raising=False)
    assert WeatherSettings(_env_file=None).kma_max_rps == 20.0
