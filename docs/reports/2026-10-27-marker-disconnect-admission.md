# 지도 마커의 실제 종류 경로와 대기 중 취소 정정

실제 기본 지도는 여섯 종류를 명시한다. `BaseHTTPMiddleware` 때문에 끊긴 요청도
입장 후 DB 조회를 시작하는 문제를 실제 ASGI로 재현했고, 순수 ASGI middleware로
바꿔 **입장 전에 끊긴 요청의 새 DB 작업**을 막는다. 이미 실행 중인 DB thread를
취소하거나 운영 25초 목표를 달성했다는 변경은 아니다.

## 앞선 분석의 오류와 보존 범위

PR180은 `marker_values_many(kinds=None)`의 후보 scalar 재JOIN을 제거했다. 당시
`kindFilter=undefined`이므로 기본 지도도 이 경로를 사용한다고 판단한 것은 오류였다.
web5098 `weather-map.tsx`는 `kindFilter ?? WEATHER_KINDS.map(...)`를 보내고,
API `_kind_kwargs`는 여섯 종류를 `None`으로 정규화하지 않는다. 실제 기본 지도는
`marker_values_many(kinds=[6종류])` → current/forecast/alert 세 그룹의
`_current_value_models_many`를 사용한다. 종류 생략 API는 여전히 별개의 유효한 경로다.

PR180의 실행 없는 EXPLAIN, scalar 비교, WSL1443 PASS·Docker1442 PASS/skip1은
그 무필터 경로의 검증으로 보존한다. 이를 실제 UI 경로 검증이나 속도 개선 증거로
확대하지 않는다. 이전 [보고서의 정정](2026-10-27-marker-candidate-scalars.md)을 참고한다.
원래 a9 실패·취소·새 외부 배포의 첫 실패 결과를 삭제하거나 빠른 재시도로 대체하지 않았다.

## 실제 실패와 SQL 계획의 범위

API·CS e393, web5098의 첫 실제 60지점 세 batch 중 두 응답은24.467/24.568초에
본문·DTO/ID 대응을 완료했고 세 번째는30.021초에504 header를 받았다. 정상 UI가
이어간 batch에서 총504 여섯 건이 관측됐다. 원본 수용기의 non200 경로는 완료 시각을
기록하지 않아70초 timeout까지 기다렸다. 이는 계측 한계이며 실제504와 구분한다.
이후 marker GET/provider/job/운영 변경을 추가하지 않았다.

저장된 실제 첫60ID·요청 시각·여섯 종류로 e393의 두 메서드 hash를 검산한 운영
EXPLAIN ONLY는 current/forecast에0034 부분 인덱스의 Index Only Scan·LIMIT1,
alert pointer에 기존 `ix_weather_current_values_alert_lookup` 사용과 추정9197행을
보였다. 인덱스 미사용으로 단정하지 않는다. 추정 행·cost는 실행 시간이나 실제 I/O가
아니며, 당시 빈 후보 capture 때문에 후속 fact 수화 SELECT는 포함되지 않았다.

별도 소유 PostgreSQL16에 동일60ID/시각·작은900 fact fixture를 넣어 실제6종류
조회 전체를 실행했다. current/forecast 각각360 candidate와360 full fact,
alert pointer·오래된 특보 valid-until 검사·120 full fact로 실제 SELECT7개와
최종840행을 확인했다. 원문 payload도 보존됐다. 각 SELECT의 EXPLAIN ONLY를
별도로 저장했다. x86/0.5CPU·작은 fixture의0.054초는 운영 DB 비용이나25초 목표의
증거가 아니다. repository·대표 선정·특보·예보·known/unknown·DTO·인덱스는 수정하지 않는다.

## 입장 대기와 취소를 구분한 재현

- API `CapacityLimiter(2)`와 UI 동시3개는 서로 다르다. 두 작업이 실행 중이면 정상
  세 번째 요청은 슬롯을 기다린다. 이 제한과 timeout, UI 병렬도는 유지한다.
- 기존 테스트는 `Request.is_disconnected`를 monkeypatch했다. 실제 ASGI
  `http.disconnect`를 주면 main5098은 종류 생략과 여섯 종류 모두200/DB 호출로
  진행했다. 두 슬롯 뒤에 대기한 실제6종류 취소 요청도200/DB 호출로 진행했다.
- 순수 ASGI middleware에서는 같은 요청이499로 끝나고 DB 호출0이다. 대기 시나리오는
  기존 두 DB 호출만 남고 취소된 세 번째는 실행하지 않는다. 정상 세 번째는 이후
  실행하며 최대 동시2·인증 worker 분리를 유지한다.
- 이미 DB thread에 들어간 뒤의 연결 끊김은 이번 수정이 중단하지 않는다. 두 작업이
  실행 중이면 슬롯도 계속2개를 점유하며 자연 종료 후 반환한다는 별도 회귀를 둔다.
  초기 취소 세 건이 실제 운영 DB를 얼마나 점유했는지는 기존 trace만으로 입증하지 못했다.

## 실제 Uvicorn/TCP 취소와 정리

자기 localhost 동적 포트의 실제 Uvicorn 서버와 별도 UUID 시험 DB를 만들었다.
앞 두 HTTP 요청은 실제 시험 DB의 SELECT1을 수행한 뒤 gate를 점유한다. 세 번째는
여섯 종류 GET을 TCP로 보낸 뒤 limiter 대기를 확인하고 연결을 닫았다. main5098
API 불변 overlay에서는 해제 후 DB 호출3회로 RED, 수정본은2회로 GREEN이다.
이는 실제 Uvicorn의 GET 연결 끊김 전달을 포함한다. 서버 thread 종료·client/context
정리·UUID DB 삭제·해당 포트의 연결 거절까지 확인했다. provider factory는 호출하지 않는다.
임의 ASGI 서버의 모든 body/receive 순서까지 확인했다고 확대하지 않는다.

## 최소 수정과 검증

PR178 `c6ef18e7c716037c5a03fdc0b6f6555e4e744e9d`의 ASGI middleware와 직접
disconnect 시험을 분리 재사용했다. export/W5/GC/DDL/Dagster/provider 수정은 가져오지
않는다. `receive`를 감싸지 않고 request ID·duration header·HTTP metric·CORS 순서와
비HTTP 통과를 유지한다. API 스키마·호출 인자·repository query는 바꾸지 않는다.

WSL의 실제 ASGI RED2건과 대기 뒤 취소 RED1건을 원본으로 보존했다. 수정 후 admission
7건 및 실제 TCP1건이 통과했다. 첫 API 하위 설정만 선택한 실행의 fixture/import
누락은 환경 오류이며 제품 RED로 세지 않는다. API 집중의 첫 실행은116 PASS/50 setup
ERROR였고, 모두 자기 PG의 max_connections30 거절이었다. 같은 시점 TCP 첫 실행도
수집 단계의 연결 거절로 테스트 전 실패했다. 왜 연결이30개에 도달했는지 엔진별
생명주기를 실측하지 않아 제품 누수로 단정하지 않는다. 기존 PG·로그를 보존하고
새 전용 PG에 기본100/512MiB·0.5CPU(1CPU 이하)를 명시한 순차 집중은167 PASS였다.
운영 PG·제품 pool/상한 변경은 없다. Ruff·diff 검사 뒤 전체 WSL·Docker·CI·독립 두 리뷰는
별도 게이트이며 완료 전 결과를 확대하지 않는다.

증거는 `/var/tmp/weather-marker-disconnect-qa-20261027/`에 저장했다. 실제 첫 실패는
`/var/tmp/weather-external-adoption-live-20261027-97dae70185/`, 운영 계획은
`/var/tmp/weather-kinds-actual-plan-ro-20261027-5ff6b26d.json`에 보존한다. 운영 새 SQL,
ANALYZE, marker GET, provider 호출, 수집/job, 이미지 빌드·교체는0이다. 소유 로컬 PG와
검증 자원만 사용하며 다른 collector·daemon·instigator·빌더·PG를 변경하지 않는다.

## CI 첫 실패와 fixture 적재 보완

정확962245d의 CI40505525047은 frontend 성공, Python1504 PASS/1 FAIL이었다.
실패는 `test_sixty_location_kind_batches_preserve_provider_and_alert_results`가
57,600행 fixture를 적재하면서 지점40~49의 projection9,600행 INSERT에 대한
`weather_values` FK 내부 `SELECT ... FOR KEY SHARE`가30초 statement timeout을
넘은 것이다. 해당 시험의 API/repository 조회 검증에는 아직 도달하지 않았다.
middleware가 원인이라는 증거는 없으며 원래 CI 원문과962 소스는 보존한다.

같은 원본 시험은 자기 PG100/512MiB에서19.31초에 통과해 CI timeout 자체는 로컬에
재현되지 않았다. 별도 원본 진단 실행도19.54초에 통과했다. projection 적재6회는
각9,600행/1.70~2.10초였고, 실행 중99개 활동 표본에 blocker는0이었다. 이는 그
로컬 실행의 관측이며 CI의 잠금 유무나30초 초과 원인을 확정한 결과는 아니다.

보완은 fixture의 projection 적재 단위만10지점에서1지점/960행으로 나눈다.
동일한60지점·각80slot·두 style·여섯 metric의57,600행과 FK, 같은 transaction,
JIT 설정과30초 적재 제한, 이후5초 조회 예산·계획/대표값·제공처·장기/종료 특보
assert는 모두 유지한다. 시험을 skip하거나 데이터를 줄이지 않는다. 같은 소유 PG의
보완 진단은19.51초에 통과했고60회 적재는각960행/0.12~0.29초였다.
총 시험 속도 개선이 아니라 개별 적재 statement의 FK 작업량 제한이다.

전체 WSL/Docker는 아직 시작하지 않았다. 별도 전체시험 PG16.14는 전용 named volume의
ext2/ext3 데이터 디렉터리(tmpfs 없음),2GiB·1CPU·max_connections100으로 준비했다.
같은 소유 명시 대역10.254.248.0/28의 route 충돌을 확인하고 nonce를 포함한 용도별 WSL/Docker
DB 두 개를 만들었다. 기존512MiB PG는 resize하지 않았으며 새 PG의 OOM 기록은 없다.
정확 새 HEAD의 CI와 전체 두 단계·독립 두 리뷰 및 운영25초 수용은 별도 미완 게이트다.
