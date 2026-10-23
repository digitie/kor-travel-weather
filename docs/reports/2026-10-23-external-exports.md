# 외부 제공처 현재·예보 export

기존 Weather 소유 adapter의 HTTP와 정규화를 분리한다. Open-Meteo, WeatherAPI,
OpenWeatherMap의 `normalize_response`는 저장된 redacted raw와 요청 metadata를 받아
기존 정규화 코드를 네트워크·실제 자격 증명 없이 다시 실행한다. 캡처된 values의
hash만 재계산하는 순환 증거를 사용하지 않는다. 기존 수집 시 fact·raw 형태는 유지한다.

후속 승인 계약은 요청 위치/좌표, 필수 metric, 명시 원천 시각, 예보 최소 기간,
배열 길이/결측/비정상 급감과 실제 capture의 독립 재정규화 일치를 검증한다.
발표 revision을 제공하지 않는 외부 응답은 **latest-retrieval** 정책이며 최신 발표
원천을 보장한다고 표시하지 않는다. 선택 집합을 HTTP 전에 고정하고 새 실패가
확인되면 이전 승인 집합을 stale로 유지한다.

현재는 네트워크 없는 원천 재정규화 seam 단계다. 전체 provider/expected cohort,
실제 수집·REST 통합과 두 리뷰/CI는 계속 구현한다. 기존 기본 OFF와 운영 설정은
유지하며 all-provider API 완료나 운영 배포 완료를 뜻하지 않는다.
