"""일시 네트워크 오류(DNS·연결·timeout)만 같은 run 안에서 다시 시도한다.

provider 라이브러리는 자기 요청을 몇 초 간격으로 이미 재시도한다. 그 재시도가 모두 끝난
뒤에도 남는 오류는 분 단위로 이어지는 장애다 — 2026-11-22 20:30Z 지역 수집의
``KrexConnectionError: [Errno -5] No address associated with hostname``(odroid DNS)이
그랬다. 그때 step 재시도가 없어 run이 실패했고, 다음 기회는 12시간 뒤였다.

여기서는 그 다음 층으로 Dagster op 재시도(``RetryRequested``)를 분 단위 지수 backoff와
jitter로 요청한다. 대상은 일시 네트워크 오류뿐이다. 인증·쿼터·파싱·차단(WAF) 같은
결정적 오류는 다시 물어도 같으므로 그대로 실패한다.
"""

from __future__ import annotations

import functools
import random
import socket
from collections.abc import Callable
from typing import Any, TypeVar

from dagster import AssetExecutionContext, Failure, RetryRequested

_F = TypeVar("_F", bound=Callable[..., Any])

#: 같은 run 안에서 더 시도하는 횟수(첫 시도 제외).
TRANSIENT_RETRY_MAX = 3
#: 첫 재시도 대기의 상한(초). 이후 시도마다 두 배, ``TRANSIENT_RETRY_CAP_SECONDS``에서 멈춘다.
TRANSIENT_RETRY_BASE_SECONDS = 60.0
TRANSIENT_RETRY_CAP_SECONDS = 600.0
#: 원인 사슬을 따라가는 최대 깊이(순환·과도한 사슬 방지).
_MAX_CHAIN = 16


def backoff_seconds(attempt: int, *, rng: Callable[[], float] = random.random) -> float:
    """``attempt``번째(0부터) 재시도 전 대기. 상한의 절반 + 나머지 절반의 무작위(equal jitter).

    같은 DNS 장애로 함께 실패한 step들이 같은 순간에 다시 몰리지 않게 흩는다.
    """
    if attempt < 0:
        raise ValueError("재시도 번호는 0 이상이어야 합니다.")
    ceiling = min(TRANSIENT_RETRY_CAP_SECONDS, TRANSIENT_RETRY_BASE_SECONDS * 2**attempt)
    return ceiling / 2 + rng() * ceiling / 2


def _provider_network_types() -> tuple[type[BaseException], ...]:
    """provider 라이브러리가 transport 오류를 감싼 타입. 없으면 건너뛴다."""
    found: list[type[BaseException]] = []
    for module, name in (
        ("httpx", "TransportError"),
        ("krex.exceptions", "KrexNetworkError"),
        ("airkorea.exceptions", "AirKoreaNetworkError"),
    ):
        try:
            found.append(getattr(__import__(module, fromlist=[name]), name))
        except (ImportError, AttributeError):
            continue
    return tuple(found)


_NETWORK_TYPES: tuple[type[BaseException], ...] = (
    socket.gaierror,
    ConnectionError,
    TimeoutError,
    *_provider_network_types(),
)


def _is_network(exc: BaseException) -> bool:
    if isinstance(exc, _NETWORK_TYPES):
        return True
    # python-krforest-api는 httpx 오류를 ``failure_kind="network"``로 감싼다(원인은 버린다).
    if getattr(exc, "failure_kind", None) in {"network", "timeout"}:
        return True
    # python-khoa-api는 transport 오류를 HTTP 상태 없이 ``retryable=True``로 감싼다.
    return (
        type(exc).__module__.startswith("khoa")
        and getattr(exc, "retryable", None) is True
        and getattr(exc, "status_code", None) is None
    )


def is_transient_network_error(exc: BaseException) -> bool:
    """DNS·연결·timeout 계열이면 True. 의도적으로 올린 ``Failure``(차단 등)는 False다."""
    if isinstance(exc, (Failure, RetryRequested)):
        return False
    from kortravelweather.providers.krex import UpstreamBlocked

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < _MAX_CHAIN:
        seen.add(id(current))
        if isinstance(current, UpstreamBlocked):
            return False
        if _is_network(current):
            return True
        current = current.__cause__ or current.__context__
    return False


def retries_transient_network(compute: _F) -> _F:
    """자산 함수의 일시 네트워크 오류를 backoff·jitter가 있는 op 재시도로 바꾼다.

    ``@asset`` 바로 아래에 둔다. 재시도마다 자산이 처음부터 다시 돌며, 실패한 시도의
    수집 기록은 이미 ``failed``로 닫혀 있어 승격되지 않는다. 마지막 시도의 오류는 그대로
    올라가 step·run이 실패한다(그 뒤는 schedule의 보충 tick이 맡는다).
    ``tests/test_transient_retry.py``가 지역 자산 네 개에 붙어 있는지 확인한다.
    """

    @functools.wraps(compute)
    def wrapper(context: AssetExecutionContext, *args: Any, **kwargs: Any) -> Any:
        try:
            return compute(context, *args, **kwargs)
        except Exception as exc:
            if not is_transient_network_error(exc):
                raise
            attempt = int(getattr(context, "retry_number", 0) or 0)
            if attempt >= TRANSIENT_RETRY_MAX:
                context.log.error(
                    f"일시 네트워크 오류가 재시도 {TRANSIENT_RETRY_MAX}회 뒤에도 남았습니다: "
                    f"{type(exc).__name__}"
                )
                raise
            wait = backoff_seconds(attempt)
            context.log.warning(
                f"일시 네트워크 오류({type(exc).__name__})로 {wait:.0f}초 뒤 다시 시도합니다 "
                f"({attempt + 1}/{TRANSIENT_RETRY_MAX})"
            )
            raise RetryRequested(
                max_retries=TRANSIENT_RETRY_MAX, seconds_to_wait=wait
            ) from exc

    wrapper.retries_transient_network = True  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]
