# W5 관측 일간 요약

`GET /v1/service/exports/daily-summaries?days=14&location_id=`는 최근 N일(1~31일)의
morning [05,11), noon [11,17), evening [17,23) KST 요약을 제공한다. 기존 서비스
토큰, gzip, 약한 ETag와 cursor를 사용한다. 기본 설정은 OFF다.

## 원천과 집계 정책

원본 수집 TX에서 집계하지 않는다. 별도 publisher가 승인 generation만 키셋으로
읽고 작은 원자적 batch로 요약과 checkpoint를 함께 commit한다. #152의 bounded
재투영은4세대 batch를 deadline/진도·실패 검사와 함께 유한 반복하며, 한 호출의
최대256세대 예산과 마지막 한 번의 파생 정리를 사용한다. 각 batch의 checkpoint를
재개하므로 이 상한이 늦은 generation을 영구 누락시키지 않는다. 파일 IO 중에는 PG connection을 보유하지 않는다. 원본 fact·이력·
보존 정책은 변경하지 않는다. 실패·부분 성공 run의 미승인 자료는 요약에 섞이지 않는다.

KMA NOWCAST 실관측과 observed 자료를 포함하고 미래/예보 자료는 제외한다.
각 위치/시간/metric별 한 winner를 선택한다. 제공처 우선순위는 KMA→산림청→도로공사→
해양조사원→Open-Meteo→WeatherAPI→OpenWeatherMap→나머지 등록 제공처 순서이며,
같은 제공처에서는 늦은 관측/known_at, 완전 동률이면 내용 hash 순으로 결정한다.
실행 순서·재수집 횟수·제공처 수가 평균이나 합계를 늘리지 않는다.

- 기온 T1H/TEMP(deg_c), 습도 REH/HUMIDITY(%), 풍속 WSD/WIND_SPEED를 사용한다.
  km/h 풍속은 m/s로 바꾼다. 시간 winner의 기온 min/max/평균과 습도·풍속 평균이다.
- `sample_count`는 metric 행 수가 아닌 채택된 값이 하나 이상 있는 시간 bucket 수다.
  한 구간 최대6이며 `source_providers`에는 실제 winner 제공처만 포함한다.
- 강수 합계는 구간이 확인된 KMA 실황 RN1(mm)만 사용한다. 11시 RN1은 [10,11)의
  양이므로 morning에 속한다. 일 누적·기간 불명 PRECIP 및 다른 제공처 강수량을
  임의 합산하지 않는다. 강수 측정이 없으면 합계/발생 여부 모두 null이다.
- 합계와 `precip_occurred=false`(무강수)는 구간 6시간 모두의 RN1 winner가 있을 때만
  확정한다(2026-10-27). KMA 격자 순환 수집으로 일부 시간만 있으면 부분 합을 구간 합계로,
  몇 시간의 0을 무강수로 내보내지 않는다. 양의 RN1이나 `1mm 미만`이 한 시간이라도
  있으면 발생은 true다. 0035가 이미 저장된 부분 합계·부분 무강수 행을 null로 고쳤다.
- 기존 정규화가 `1mm 미만`을 숫자0+원문으로 보존한 경우 발생은 true로 유지하고,
  정확한 양을 단정하지 않도록 합계는 null이다. 원본 정규화 계약을 바꾸지 않는다.
- `sky_code`는 예약 필드이며 현재 항상 null이다. reducer는 SKY 관측 code 1/3/4의
  최빈값(동률이면 작은 code)을 쓰도록 되어 있지만 승인된 관측 원천 중 SKY 관측을 내는
  것이 아직 없다. 예보 SKY나 제공처별 서로 다른 WEATHER_CODE를 같은 code로 합치지 않는다.
- 관측이 없는 구간은 sample_count=0, source_providers=[], 모든 관측 지표 null이다.
  오늘 아직 끝나지 않은 구간의 partial은 요청 시각으로 계산해 오래된 true가 남지 않는다.

강수 시간 단위/문자 범주 근거:
https://apihub.kma.go.kr/apiList.do?seqApi=10 및
https://apihub.kma.go.kr/getAttachFile.do?fileName=%28250305%29%EC%82%B0%EC%95%85%EC%98%88%EB%B3%B4+API+%EC%84%9C%EB%B9%84%EC%8A%A4+%ED%99%9C%EC%9A%A9%EA%B0%80%EC%9D%B4%EB%93%9C_f.pdf

## 메모리·보존·페이지

REST는 scalar summary만 선택하며 weather_values/날짜 파티션/hourly_winners를 읽지
않는다. 한 위치의 31일×3구간만 유지하고, 페이지는 5,000항목/4MiB 상한이다.
위치/날짜 범위는 cursor 동안 고정하고 값은 각 요청의 최신 사전계산 값을 제공한다.
ETag에는 실제 페이지와 source 상태를 넣어 변경 중의 페이지를 304로 숨기지 않는다.
cursor 조건 변경은422, 15분 만료는410이며 첫 페이지부터 재시작한다.

한 HTTP 페이지의 summary·coverage·checkpoint·원천 상태는 같은 REPEATABLE READ
snapshot과 statement 5초 상한 안에서 읽는다. 페이지 사이 최신 값 허용은 한 페이지
안에서 서로 다른 commit의 위치 값을 섞어도 된다는 뜻이 아니다.

승인 위치×namespace coverage를 요약/checkpoint와 같은 TX로 저장한다. 선택 위치의
현재 grid/좌표에 맞는 coverage가 없으면503이고, 관측 없는 시간 bucket의 null과
구분한다. 선택한 승인 원천 중 가장 오래된 실제 조회 시각을 신선도에 사용하며
실패 상태도 선택 source_scope만 검사한다. 다른 위치의 성공/실패는 신선도를
좋게 만들거나 무관한 위치를 stale로 바꾸지 않는다. 전체 위치 조회는 하나라도
승인 coverage가 없으면 작은 성공 집합으로 축소해 내보내지 않는다.

`docs/examples/weather-daily-summaries.json`은 격리된 PG/실제 publisher와 TCP API에서
받은 응답 예시다. 토큰은 포함하지 않으며 관측이 없는 구간의 null/sample_count=0과
다음 cursor를 보여 준다. 각 페이지에서 source 상태와 실제 값을 다시 검증하므로
새 실패 상태를 이전 ETag의304로 숨기지 않는다.

summary/winner 상태는 최대31일 조회에 하루 cursor 유예를 더해 보존하며 오래된
파생 행만 SKIP LOCKED/최대500행씩 정리한다. 승인 generation의 publication 인덱스로
증분 처리하여 매번 전체 이력을 훑지 않는다. 동일 상태 replay는 JSON UPDATE를 생략한다.
checkpoint는 (publication 시각, generation_id) 완전 키셋이다. 기존0026의 동일 시각
generation도 한 tick의 상한에 걸렸다는 이유로 다음 tick에서 누락되지 않는다.

API·publisher는 동일 NVMe artifact 경로·권한을 사용해야 한다. 미처리 generation은
summary checkpoint 이전에 GC하면 안 된다. GC·전체 wall-clock/최대 RSS·모든 provider
승인 및 정지/재개 비용 측정은 운영 활성화 전 마감 항목이다. 자동 배포/ON은 하지 않는다.

## 구현 상태

최초 #148은 KMA 승인 관측 reducer/REST를 구현했고 #152가 bounded 재투영/publisher,
#163이 실제 다중 제공처 관측 선택·최신 generation coverage·새 SDK station 누락과
이전 완전 roster fallback을 보강했다. [현재 원천 eligibility](2026-10-23-daily-source-eligibility.md)를
함께 따른다. 예보 전용 KHOA는 관측으로 편입하지 않는다. 승인된 관측의 미계산은
이전 전체 roster stale, 이전 집합이 없으면503이다. 한 번도 승인되지 않은 관측
anchor는 2026-10-27부터 전체 목록을 stale로만 낮추고, 단일 위치 최초 조회의 필수 원천
미승인만503이다(청정넷 같은 선택 원천은 stale). source 구현 완료와 운영 비용·GC·
초기 투영/Map 수용은 별개다.

## 검증 범위

관측 순서/제공처 우선순위/재수집 중복/시간 경계/trace 강수/빈 구간을 순수 함수로
검증하고, 실제 PG에서 승인만 집계·실패 배제·원자 checkpoint·두 reducer의 CAS·
파일 손실 시 이전 요약 보존 및 동일 파일 복구 후 재개·보존 상한을 확인한다.
REST 회귀는 token 거부, gzip,304, 페이지와 조건 서명, 사전계산 scalar projection만
조회함을 검증한다. byte 상한은 production과 같은 분기를 작은 테스트 상한으로
통과시켜 slot 중간에서 재개해도 누락/중복이 없음을 확인한다.
