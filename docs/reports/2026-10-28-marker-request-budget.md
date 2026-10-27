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
| Python 전체 WSL | 1d2 source,1518 PASS/actual0,792.23초 |
| Python 전체 Docker | test-only a707 source,1517 PASS/1 SKIP/actual0·독립 wait0,751.78초 |
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


## 전체 QA 원본과 시험 입력 보완

- 원1d2 WSL전체는1491 PASS/27 FAIL이었다. QA 실행기의 main guard 누락으로 spawn이 pytest 인자를
  출력 경로로 다시 읽었고, PYTHONPYCACHEPREFIX가 명시 bytecode 목적지 계약과 충돌했다. 제품·시험
  조건을 바꾸지 않고 실행기만 보완한 동일27건은27 PASS였고 이후1d2 전체1518 PASS를 회수했다.
- 첫 Docker전체1d2는1515 PASS/2 FAIL/1 SKIP였다. 기존 중기 fixture가06:01 KST에 아직 공개되지 않은
  06시 회차를 선택했다. SDK 공개지연10분을 준수하도록 `latest_mid_fcst_time(now)`만 사용했다.
  추가 SDK 단위시험을 만들지 않고 기존 land/temperature 두 항목을06:01·18:01 고정 시각에서 각각
  원본2 FAIL→보완2 PASS, 현재 시각2 PASS로 검산했다. normalize·head·inventory assert는 그대로다.
- 최종a707645개 Git blob/mode를 검산했고1d2 대비 시험 한 파일 외644개가 같다. WSL전체는1d2,
  Docker전체는a707의 증거로 구분한다. Docker1 SKIP는 컨테이너에 Docker CLI가 없는 기존 배포 계약
  검사이며1518건 전부를 통과했다고 표현하지 않는다. 새 WSL전체를 다시 실행한 것으로도 기록하지 않는다.
- 모든 foreground CLI·독립 waiter·시험 fixture가 끝났고 clients0·nonceDB0을 확인했다. 소유 disk PG18은
  exit0으로 정상 종료하고 컨테이너·볼륨·네트워크를 삭제했다. 전체 실행 중 ownPG 체크포인트 대기를
  별도 보존했으며 이를 운영 지도 지연 원인으로 귀인하지 않는다. 다른 서비스/프로세스 변경0이다.

최종 원장 `C:/Users/digit/.codex/tmp/weather-budget-final-a707-20261028-r1/final-qa-manifest.json`
(SHA256 `2fe739ae92e7558e21315773b6a716df861ae4cbf654dc1921644aa856dab603`)이
전체 로그·JUnit·실제 종료·cleanup과 이전 실패·고정시각 반례 및 frontend/backend 집중 증거를 연결한다.
고정시각 원장은 `C:/Users/digit/.codex/tmp/weather-mid-clock-fixture-20261028-r1/repair-final-manifest.json`
(SHA256 `42fc85391cd6e43071f5538ee1e678e966594c63e7cbb6e283eec3e52efee3a6`)이다.

21:33 UTC 정산 시점 새 R6 도구는 준비 상태이고 실제 실행 입력은False/None·배포0이다. PR187의
정확 CI·적대적 리뷰·코드 머지와 최종 운영 입력 동결은 Root가 별도로 판단한다. 현 운영 API210/
CS e393/web5098과 이전504 실패를 새 코드의 배포 완료나 속도 수용으로 바꾸지 않는다.
