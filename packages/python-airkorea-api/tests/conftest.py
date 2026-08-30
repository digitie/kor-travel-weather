from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Call:
    url: str
    params: Mapping[str, Any]
    timeout: float


class FakeResponse:
    def __init__(
        self,
        *,
        json_data: Any | None = None,
        text: str = "",
        status_code: int = 200,
        json_error: bool = False,
    ) -> None:
        self._json_data = json_data
        self.text = text
        self.status_code = status_code
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error:
            raise ValueError("not json")
        return self._json_data


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[Call] = []

    @property
    def last_call(self) -> Call:
        return self.calls[-1]

    def get(self, url: str, *, params: Mapping[str, Any], timeout: float) -> FakeResponse:
        self.calls.append(Call(url=url, params=dict(params), timeout=timeout))
        if not self._responses:
            raise AssertionError("FakeSession has no queued responses")
        return self._responses.pop(0)


class AsyncFakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[Call] = []

    @property
    def last_call(self) -> Call:
        return self.calls[-1]

    async def get(self, url: str, *, params: Mapping[str, Any], timeout: float) -> FakeResponse:
        self.calls.append(Call(url=url, params=dict(params), timeout=timeout))
        if not self._responses:
            raise AssertionError("AsyncFakeSession has no queued responses")
        return self._responses.pop(0)


def payload(items: Any, *, body_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"items": items}
    if body_extra:
        body.update(body_extra)
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_CODE"},
            "body": body,
        }
    }


def error_payload(code: str, message: str) -> dict[str, Any]:
    return {
        "response": {
            "header": {"resultCode": code, "resultMsg": message},
            "body": {},
        }
    }
