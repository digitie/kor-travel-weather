# Weather 현재 인수인계

## 2026-10-21 회수 sensor tick 예산·정체 경보 — 머지 대기

브랜치 `feat/recovery-sensor-budget-stall`. Common pin `e746e4b`, `weather_sync_recovery`에
`budget_seconds=40`·`on_stall`, 정체 경보 로그 `weather_sync_recovery.stalled`와 gauge
`ktw_sync_recovery_stalled`·규칙 `KorTravelWeatherSyncRecoveryStalled`. 자세한 것은
`docs/journal.md` 같은 날짜 절과 `docs/observability.md`. 배포(odroid, codex 관리)는 스키마 변경이 없어 migrate가 필요 없고, code-server 이미지(sensor가 도는
곳; `/opt/kor-travel-odroid/compose.yml`의 `kor-travel-weather-dagster-code-server` image 태그)와
같은 소스의 API 이미지를 새 main으로 재빌드·교체하면 된다. 공용 plane의 sensor gRPC timeout 60초 안에
예산 40초가 들어간다. odroid에는 weather Prometheus가 없어 새 규칙은 아직 평가되지 않는다 — 그동안
경보는 Dagster sensor 로그와 code-server `:14103`의 gauge로만 보인다. transport override 파일은 무관하다. 실패 중 sensor를 오래 멈췄다가
다시 켤 때는 재기동 전에 sensor cursor를 지운다(common runbook §19). daemon OFF는 유지한다.

## 2026-10-18 PR #118 비특보 예보 배치 메모리 개선 — 배포·UI 수용

제품 a8949778은 다음 위치가 상한을 넘기기 전에 기존 묶음을 게시하고,
격자·중기 예보의 staging·게시 상한을2500으로 분리했다. 특보 staging과
다른 provider의 공용 게시 상한은5000을 유지하며 한 위치는 나누지 않는다.
격리 PostgreSQL 전체667개·Ruff·최종 Python/frontend CI·독립 소스 리뷰2건이 통과했다.

Odroid의 고정 합성 입력(792fact×10위치)에서 기준/후보 각3회 비교를 두 측정군으로
수행했다. PostgreSQL을 포함한 측정군은 worker RSS 중앙값 약14.99% 감소,
합산 CPU 약1.29% 감소, 처리 시간 약2.02% 증가였다. CPU는 작은 표본에서 동등
수준으로 판단한다. SQL은107→125이며 전체 provider·전체 job·호스트의 개선율은 아니다.
실측은6421a4b에 상수2500을 주입한 결과이고, 실제 a8949778 기본값은 별도로
rollback+alias 및 commit 후 응답 오류의 복구2건을 통과했다.
각각2376+5544,4752+3168건으로 총7920건을 복구하고 정상 기준의 원천·fact·current
의미 digest 일치와 다음 재수집0건을 확인했다.

[상세 측정·원시 증거·복구 및 한계](reviews/collector-bounded-stage-20261018.md)를 따른다.
이번 제품의 ARM64 API/CS native 이미지 빌드와 각 이미지의 설치 소스67파일 검증이
통과했다. 운영 전환 f5cc3880에서 API/CS READY·기존 큐와 env 파일 보존·버전 외 runtime
환경 동일·daemon OFF를 확인했다. 배포 후 WSL Chromium1243 live UI는36/36 PASS,
exit0이며 Weather 세 역할의 실행 상태가 유지됐다.
[배포·UI 수용 증거](reviews/collector-bounded-stage-deployment-20261018.json)를 따른다.
PR #118의 배포 수용 기록이며 daemon OFF를 유지한다. 전체 수집·24시간 OOM/swap·자동 복구·Track1/2의 미검증
운영 수용을 완료로 바꾸지 않는다. 아래 PR #117의 RSS 미입증 문장은 당시
카탈로그 조회 범위의 기록이며 이번 격자 저장 실측과 구분한다.

## 2026-10-18 PR117 수집 카탈로그 개선 현재 수용

제품8c503fa8의 두 독립 소스 리뷰·exact-head CI·전체 Python663·frontend214/type-check·
Ruff·ARM64 API/CS native build를 통과했다. 실제 배포065661ca PASS 뒤
WSL Chromium1243 live UI36 PASS/exit0, role lifecycle 불변·daemon OFF를 확인했다.
[제품·이미지·원시 측정 및 수용 근거](collector-catalog-cpu-20261018.md)를 따른다.
ARM 고정 fixture CPU 중앙값은 약12.2%, Python 할당 peak는 약16.0% 감소했다.
RSS의 실질적 개선, 전체 provider 수집 성능, 장시간 안정성, Kakao 실제 fallback과
주소 전체 sweep, Track 1/2 미검증 운영 수용의 완료를 주장하지 않는다.
이 수용 기록은 PR #117에 포함하며 아래 pending 문장들은 해당 작성 시점의 이력이다.

## 2026-10-18 운영 배포·제한된 실제 수용 완료

Weather #115 배포와 migration0021을 완료했다. 기존 UI36(WSL)·새 주소 UI10(n150),
실제 제공자 단일 대상 갱신 및 원천 위치1,450행 보존을 확인했다.
[운영 결과와 미검증 범위](reviews/2026-10-18-address-live-deployment.md)를 따른다.
Daemon OFF를 유지하며 아래의 운영 미실행 문장은 각 작성 시점의 이력이다.


## 2026-10-18 영속 주소 예약 후속 검토 완료

최종 제품7f549f1의 두 독립 SOURCE PASS·CI와 UI/지연 재시도 결과를
[후속 종결 기록](reviews/2026-10-18-native-address-final.md)에 보존했다.
Common41은 머지됐으며 공용 호스트·소비자 운영 배포는 후속이다. daemon OFF를 유지한다.
아래는 각 구현 시점의 이력이며 현재 결과를 소급 적용하지 않는다.


## 2026-10-18 영속 예약의 잠금 연결 장애 보강

현재 Common pin은 a68d0495acc38906c31b95b1d0db163bd8f96f5b다.
동일 요청 ID와 NOT_STARTED 제출 복구를 유지하며, PostgreSQL 잠금 연결이 끊긴 뒤
늦은 제출이 STARTED를 QUEUED로 되돌리지 않도록 native run 저장도 같은 연결에 묶는다.
이전 23e4775의 동시 제출 문제와 2704830의 잠금 연결 소실 반례는 이력으로 보존한다.
새 pin에서 실제 native 지연 요청·재시도 시험은 앱별 1건 통과했다. 전체 Python·UI
회귀와 두 독립 최종 리뷰는 진행 중이며, 이 기록은 운영 수용 완료가 아니다.
공용 webserver를 먼저 배포하고 소비자 Admin을 갱신한다. Daemon OFF를 유지한다.


## 2026-10-18 주소 확인 집계 후속 종결

Common40·Weather113은 두 독립 FULL SOURCE PASS/CI 후 머지했다. 제품2bd779의 전체661·CI661,
새 pin의 loopback UI13과 원천 보존을 [최종 원문·수용 범위](reviews/2026-10-18-address-summary-final.md)에
보존했다. 운영 migration/provider/배포·수집 CPU/RSS와 Transport 별도 gate는 후속이다.
아래 구현 당시 대기 문장은 당시 이력이며 Daemon OFF를 유지한다.

## 2026-10-18 현재 원천의 주소 확인 시각 후속

Common40의 522525aed3db934767c1c9e742b3bfc68e91679c로 pin을 갱신한다. 비활성·좌표 이동 전의
파생 결과가 현재 최근 정상 확인 시각에 남는 공통 집계를 수정한다. Weather 실제 모델의
비활성/경도 이동/위도 이동 반례는 기존 pin에서 3 FAIL→새 pin의 주소 회귀 4 PASS다.
원천 주소·격자·관리자 metadata·과거 sidecar를 보존한다. 새 CI·두 독립 검토·UI가 후속이며
이전111/112의 PASS를 새 pin 검증으로 세지 않는다. Track1·2 전체 종료 뒤 수집 CPU/RSS를 개선한다.
Daemon OFF를 유지하며 운영 배포·provider 실호출은 이번 검증에서 실행하지 않았다.

## 2026-10-18 주소 갱신 구현·로컬 검증

VWorld 우선·Kakao fallback, 위치별 달력 6개월 자동 갱신과 Admin 수동 실행을 구현했다. 두 앱은 같은 Common Python 엔진을 고정 commit으로 사용한다. [설계·설정 및 검증 범위](reverse-address-refresh.md)를 참조한다. 원천 주소·좌표는 보존한다.

로컬 실브라우저 최종 재검증은 앱별 13개(합계 26개) 통과했고 실제 인증·상태 API·Dagster 큐 예약·중복 차단·모바일/키보드·오류 복구를 포함한다. 제공자는 주입 fixture다. Python 전체 검증은 Common 509개, Weather 658개, Transport 838개 통과·환경 조건 10개 skip이다. Common39·Weather111은 두 독립 리뷰/CI 통과 후 머지했다. [최종 리뷰 기록](reviews/2026-10-18-address-refresh-final.md)을 참조한다. Transport 정식 gate는 별도이며 운영 migration·실제 제공자 실호출·배포·운영 UI 수용은 미실행이다. Daemon은 중지 상태를 유지한다.

2026-10-17 main39aafe0/109까지 MERGED. 이 문서 PR 생성 전 OPEN PR0 시점의 정산이다. [감사](audits/2026-10-17-remaining-work.md)와 [남은 작업](tasks.md)을 읽는다.

root 11:58:07 UTC RO는 API8d2777b·CS8cdb78d healthy/restart0/OOMfalse·점검90/90초, UI기존 noHC, 전체77/16running·공유 daemon OFF를 확인했다. label을 전체 module 검증으로 바꾸지 않는다.109 UI36PASS, native retention run8d87945 SUCCESS/complete_snapshot·8필드 Observation1개는 완료된 한정 수용이다.

다음은 지역 freshness·정식 NVMe source/build 경로·consumer/관측 증거의 RO 정산이다. Manager 정본의 옛 probe와 운영 안전 override 차이는 공통 소유자가 후속 통합한다. 현재 운영 kill-probe라고 주장하지 않는다.

이미 있는 adapter/sampling/recovery/retention을 재구현하지 않는다. daemon 자동 복구/스케줄 start·새 SQL/DDL·초기정리 반복0. 별도 수집 재개 승인 뒤 장시간 자원·단일 retry 소유권·정기 처리량을 수용한다.

이전 백로그와 당시 상태는 deploy/n150.md에 그대로 남기며 vendored provider 문서는 편집하지 않는다. 이번 docs4개 commit은 사용자 승인 [skip ci]이고 full suite/새 build·live UI0, 독립2문서 리뷰 후 root merge한다.
