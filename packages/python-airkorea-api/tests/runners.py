from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from airkorea import AirKoreaClient
from airkorea.debug import jsonable
from tests.conftest import FakeResponse, FakeSession

Runner = Callable[[AirKoreaClient, Mapping[str, Any]], Any]

_METHOD_NAMES = (
    "station_measurements",
    "latest_station_measurement",
    "sido_measurements",
    "unhealthy_stations",
    "forecast_notices",
    "weekly_forecasts",
    "stations",
    "nearby_stations",
    "tm_coordinates",
    "measurement_near",
    "sido_average_stats",
    "city_average_stats",
    "station_daily_stats",
    "station_monthly_stats",
    "ozone_advisories",
    "yellow_dust_advisories",
    "dust_alarms",
    "traffic_stats",
    "high_pm25_forecasts",
    "cai_measurements",
    "background_concentrations",
    "english_measurements",
    "english_stations",
    "call",
)


def replay_case(case: Mapping[str, Any]) -> Any:
    """fixture의 저장 응답을 fake session에 넣어 public method를 재실행합니다."""

    session = FakeSession([_fake_response(response) for response in _fixture_responses(case)])
    client = AirKoreaClient(service_key="fixture-key", session=session, retries=0)
    function_name = str(case["function"])
    try:
        runner = RUNNERS[function_name]
    except KeyError as exc:
        known = ", ".join(sorted(RUNNERS))
        raise ValueError(f"unknown fixture function {function_name!r}; known: {known}") from exc
    return jsonable(runner(client, _input_data(case)))


def _method_runner(function_name: str) -> Runner:
    def run(client: AirKoreaClient, input_data: Mapping[str, Any]) -> Any:
        args, kwargs = _args_kwargs(input_data)
        method = getattr(client, function_name)
        return method(*args, **kwargs)

    return run


def _args_kwargs(input_data: Mapping[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    if "args" in input_data or "kwargs" in input_data:
        raw_args = input_data.get("args", [])
        raw_kwargs = input_data.get("kwargs", {})
        if not isinstance(raw_args, list):
            raise ValueError("fixture input.args must be a list")
        if not isinstance(raw_kwargs, Mapping):
            raise ValueError("fixture input.kwargs must be an object")
        return raw_args, dict(raw_kwargs)
    return [], dict(input_data)


def _input_data(case: Mapping[str, Any]) -> Mapping[str, Any]:
    input_data = case.get("input", {})
    if not isinstance(input_data, Mapping):
        raise ValueError("fixture input must be an object")
    return input_data


def _fixture_responses(case: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    responses = case.get("responses")
    if responses is not None:
        valid_response_list = isinstance(responses, list) and all(
            isinstance(item, Mapping) for item in responses
        )
        if not valid_response_list:
            raise ValueError("fixture responses must be a list of objects")
        return responses

    response = case.get("response", {})
    if not isinstance(response, Mapping):
        raise ValueError("fixture response must be an object")
    return [response]


def _fake_response(response: Mapping[str, Any]) -> FakeResponse:
    status_code = int(response.get("status_code", 200))
    return FakeResponse(json_data=_payload(response), status_code=status_code)


def _payload(response: Mapping[str, Any]) -> Mapping[str, Any]:
    body = response.get("body", {})
    if isinstance(body, Mapping) and "response" in body:
        return body
    header = response.get("header", {"resultCode": "00", "resultMsg": "NORMAL_CODE"})
    return {
        "response": {
            "header": header,
            "body": body,
        }
    }


RUNNERS: dict[str, Runner] = {name: _method_runner(name) for name in _METHOD_NAMES}
