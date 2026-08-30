"""airkorea 예외 계층."""

from __future__ import annotations


class AirKoreaError(Exception):
    """모든 airkorea 오류의 기본 예외."""


class AirKoreaAuthError(AirKoreaError):
    """인증이 실패했거나 서비스키가 없거나 올바르지 않습니다."""


class AirKoreaRequestError(AirKoreaError):
    """요청 파라미터가 올바르지 않거나 API가 요청을 거절했습니다."""


class AirKoreaNoDataError(AirKoreaError):
    """API가 데이터 없음 결과 코드(resultCode=03)를 반환했습니다.

    요청 자체는 유효하며 단순히 일치하는 결과가 없다는 뜻이므로
    `AirKoreaRequestError`(잘못된 요청)의 하위 클래스가 아닙니다.
    일부 엔드포인트/상황은 동일한 '결과 없음'을 예외 대신 빈 list로
    반환하므로, 호출부는 두 경우를 모두 처리해야 합니다.
    """


class AirKoreaRateLimitError(AirKoreaRequestError):
    """쿼터 또는 호출 제한 때문에 API가 요청을 거절했습니다."""


class AirKoreaNetworkError(AirKoreaError):
    """AirKorea 호출 중 네트워크 오류가 발생했습니다."""


class AirKoreaServerError(AirKoreaError):
    """AirKorea가 서버 측 또는 일시적 실패를 반환했습니다."""


class AirKoreaParseError(AirKoreaError):
    """AirKorea 응답을 기대한 구조로 파싱할 수 없습니다."""
