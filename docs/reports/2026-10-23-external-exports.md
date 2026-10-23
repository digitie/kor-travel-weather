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

## 실제 승인·수집·REST

기본3 제공처의 current/forecast는 실제 `run_external_weather_sync`에 명시
`export_artifact_root`를 전달했을 때만 승인 경로를 실행한다. HTTP 전 provider/dataset별
실제 quota/sampling 선택 위치를 작은 cohort에 보존하고 최신 시작 run만 갱신한다.
각 위치의 원본·capture는 기존 단일 원천2500개 fact 상한 안에서 한 번 저장한다.
export 증거 실패는 기존 native raw/fact 저장을 막지 않으며 native 성공·파일 검증·
소유권 확인 뒤 별도 publisher가 head를 승격한다. 기본은 OFF다.

- 현재 자료는 명시 관측 시각이 실제 조회 전3시간 이내이고 기온/습도/풍속이 모두
  유효해야 한다. 원천 요청 좌표는 사전 선언과 같고 응답 grid/인근 지점은0.5도
  이내여야 한다. 필수 정상값의 물리 범위도 검사한다.
- Open-Meteo 기본7일 시간 배열과 요청한 모든 변수 길이, WeatherAPI 요청2일×24시간,
  OpenWeatherMap 기본5일×3시간40건을 검증한다. 역순/중복/가까운 미래 공백/필수
  기온·습도·풍속 결측을 거절한다. 직전 승인 대비 절반 미만 급감도 재확인이 필요하다.
- REST current는 설정/요청 provider 중 실제 관측 dataset이 있는 제공처의 전체 cohort를
  요구한다. KHOA처럼 예보만 있는 제공처를 현재 관측 누락으로 오인하지 않는다.
- `forecasts?style=external`은 외부 제공처의 승인 예보만 위치×provider 배열로 반환한다.
  원래 known_at을 보존하며 since/next_since는 동일한 publication watermark 계약이다.
  KMA 초단기/단기 style은 KMA scope를 선택하므로 다른 외부 provider 설정이 있어도
  해당 KMA 전체 원천이 준비되면 제공할 수 있다.
- 선택 cohort 변경·실패·미승인은 이전 승인 vector를 stale로 유지한다. 제공처 필터와
  cursor는 결합되어 조건을 바꾸면422이며, 최초 승인 전에는503이다.
- cohort의 최신 native 실행 성공과 각 위치 head의 해당 run 승인도 함께 확인한다.
  attempt 생성/claim 이전 실패는 이전 owner를 바꾸지 않으므로 owner만으로 건강함을
  판단하지 않는다. prepare 잠금 시간 초과 뒤 HTTP 실패도 current/forecast 기존 페이지와
  cursor를 stale로 표시하고 이전 healthy ETag의304를 차단한다.

원천 계약 근거: [Open-Meteo](https://open-meteo.com/en/docs),
[WeatherAPI](https://www.weatherapi.com/docs/),
[OpenWeatherMap5일](https://openweathermap.org/api/forecast5).

## W1 독립 reference

`locations`는 활성 catalog를 날씨 head와 독립해 전부 제공한다. 주소는 기존 resolver의
정상/재시도 오류 상태와 정확 coordinate_key/ASCII 법정동10자리 조건을 유지한다.
임의 metadata JSON은 읽지 않는다. 현재 catalog의 정확 원천 조회 시각 sidecar는 없으므로
updated_at을 대신 넣지 않고 source_collected_at=null/source_status=stale로 명시한다.
updated_at은 내부 catalog revision으로만 사용한다. 빈 catalog/미확인 절반 미만 급감은
이전 reference를 유지한다. 명시 비활성화로 확인된 축소는 반영하고, cursor 중 catalog가
변해도 처음의 reference와15분 TTL/4MiB 정책을 유지한다.

## 남은 범위와 검증

현재 기본3 provider current/external과 W1 독립 reference를 구현했다. AirKorea·산림청·
도로공사 관측/KHOA 예보/선택 외부 제공처와 KMA mid의 승인·선택 집합 연결은 후속이다.
기본 다중 provider에서 미지원 expected 원천이 있으면503이며 작은 부분집합을 전체
성공으로 표시하지 않는다. 모든 provider 완료나 운영 배포 완료를 뜻하지 않는다.

집중49개에서 원천 독립 재정규화/실제6개 native 승격·HTTP전 cohort/실제 current·external
REST와 이전 KMA frozen API·W1 reference 독립/주소/4MiB·마이그레이션을 확인했다.
별도41개에서 기존 KMA head와 외부 proof를 확인했다. 최종 CI/두 리뷰/실제 TCP·UI 및
공유 NVMe 경로/GC pin/전체 wall·RSS·정지 재개 비용 gate는 아직 진행 중이다.
