# 마커 요청 예산과 특보 고정 predicate 후속

## 현재 운영과 해결되지 않은 문제

Root의 R4 API 단독 교체는 2026-10-27 19:49:40 UTC에 actual exit0으로 끝났다. API는
`2107839d3302f6cb938d14380193200421fdf58e`/image `sha256:1b3d208b08f6347fb199397c8a2d7190bd7806a0410a516fd6b51fd276577230`이다.
CS는 e39362a, web은5098d2d이며 공용 daemon과 일정21 RUNNING/2 STOPPED를 교체하지 않았다.
90초 healthcheck 이전에 끝나던75초 대기는180초로 보완했지만, 배포 성공과 마커 성능 수용은 구분한다.

새 실제 Chromium cold60×3은 **19701ms200 / 30020ms504 / 21016ms200**으로 실패했다.
원장 `/var/tmp/weather-live-after-210-20261028-075bfac385/result.json`은 보존한다.
모바일·상세 overlay gate는 실행하지 않았고 로그아웃 뒤 쿠키 잔여0은 별도로 확인했다.
아래7파일 수정은 이 실패 이후의 미배포 후속이며 운영 효과·25초 목표 달성을 주장하지 않는다.

사용자는 export 목표를 일시중지했고 새 Dagster 이미지도 허용했다. 그러나 e393→5098의
Python/Dagster/build/migration 입력은 같고 기존 개선이 이미 운영 중이므로 중복 CS 교체 근거는 없다.
publisher STOPPED·Map `weather_state_*`(locations 제외)와 PinVi weather flag OFF를 유지한다.
이 변경에서 수집·export 재개, GC 모드, flags, CS/공용 daemon을 수정하지 않는다.

## 사용자의 지도 요청에 맞춘 최소 변경

- 브라우저 마커 동시 요청을 API limiter와 같은2개로 맞춘다. 같은 필터 범위의 viewport 이동은
  진행 중 chunk를 이어받고 옛 대기 목록은 새로 시작하지 않는다. chunk60·종류6·제공처·DTO는 그대로다.
- 실패·누락 ID는 자동 무한 재시도하지 않고 재조회 버튼에서 다시 허용한다. 성공 cache와 상세
  refresh는 분리한다. 상세 latest/forecast는 실제로 열렸을 때만 요청하고 닫기·선택 변경에서 취소한다.
- 특보 고정5항목 ALERT/%alert%/%warning%만 `literal_execute`로 지정해 generic plan도 기존
  `ix_weather_current_values_alert_lookup` 조건을 증명할 수 있게 한다. 사용자 location/provider/time,
  상한·정렬·payload의 station/action/title·부분 응답 의미와 API limiter2는 그대로다.

실제6종류는 현재/예보/특보 세 그룹이다. PR180의 `kinds=None` 최적화를 기본지도 경로로 추정했던
오류는 유지해서 정정한다. 이번 generic 경계가 cold20초의 주원인인지는 미확인이다. 모든 old 특보
pointer의 유효성 검산, 복합 키 fact 전체 ORM·JSON 수화와 입장 대기는 별도 비용으로 남는다.
이전 EXPLAIN cost·합성 fixture 시간을 실제 운영 latency로 바꾸지 않는다.

## 검증과 남은 게이트

| 범위 | 실제 결과 |
| --- | --- |
| Backend 집중 WSL/PG18 | 56 PASS, actual0,33.51초 |
| Backend 집중 Docker/PG18 | 56 PASS, actual0·독립 wait0,36.72초 |
| Frontend WSL 타입·린트·전체 시험 | actual0,21파일265 PASS, 기존 린트 경고1 |
| Frontend Docker 타입·린트·전체 시험 | Node22.23.1-alpine/npm12.0.1, actual0·wait0,21파일265 PASS |
| Frontend WSL 생산 빌드 | Node22.22.2/npm11.19.1, actual0 |
| 정확 통합 HEAD의 Python 전체1/2차 | 준비 중, 아직 미실행 |
| CI·최종 source 리뷰·배포 뒤 실제 UI | 별도 게이트, 운영 효과 미검증 |

PG18의 custom/generic 실제 계획4개 모두 alert 부분 인덱스를 썼고 old 미래만료 fact60개를
`value_id+known_at`으로60회 검산하며 다른 partition 실행0을 확인했다. 제공처·동률·최근 명시만료·
해제·오래된 미래만료·NULL과 기존 마커/API 회귀를 유지한다. 시험의 PREPARE는 기록 SQL의
DBAPI `%%` escaping도 실제 드라이버와 같이 처리한다.

첫 fixture의 dataset/source 충돌과 잘못된 PREPARE escaping으로 각각55 PASS/1 FAIL이었던
두 원본은 삭제하지 않았다. 최종 source는 수정된 fixture·assert 그대로 WSL→Docker를 통과했다.
상한·timeout·assert를 완화하지 않았다. 기존119·72 whole 결과를 이번 전체 검증으로 재사용하지 않는다.

Backend 증거는 `C:/Users/digit/.codex/tmp/weather-alert-literal-20261028-74cd86ea2d/backend-handoff-manifest.json`
(SHA256 `8e368cd2be17fb339e308e1bf4b00c7c4b13ce67dbc8fd57a3c68c955882e8fe`)에 연결했다.
소유 disk PG/nonceDB/container/volume/network는 정상 종료·삭제했고 다른 자원은 변경하지 않았다.
Frontend 증거는 `/var/tmp/weather-marker-budget-frontend-20261028-r2`와
`C:/Users/digit/.codex/tmp/weather-frontend-second-20261028-d11d5b2ea5/actual-results.json`이다.
Docker는 배포 Dockerfile의 Node/npm과 같은 값으로 별도 복사본·고정 lock/vendor에 설치했고,
검사 container는 network none이다. 생산 키·build arg·운영/API/provider 호출은 사용하지 않았다.
