# Weather 남은 작업 정산 — 2026-10-17

## 현재 결론과 범위

이 문서 PR 생성 전 감사 조회에서 main은39aafe0d8a2c6f8035bb1e89e6f5a17c94a7d34e, 열린 PR은0개였다. [PR109](https://github.com/digitie/kor-travel-weather/pull/109) 최종8cdb78d와 [PR108](https://github.com/digitie/kor-travel-weather/pull/108) 최종783f372는 MERGED이며 각각 Python/frontend CI와109 merge main CI가 성공했다.

KREX/KRForest adapter, bounded chunk/잠금, Common 실행기·복구, quota sampling, 점검 주기·bytecode,6시간 단기예보 정리와109 진행 이벤트는 이미 구현·머지됐다. 재구현할 일이 아니다. 남은 핵심은 **지역 수집 최신성·실제 복구 평가·장시간 자원 사용·consumer/정식 source 경로 수용**이다. 증거 미확인은 미배포 확정과 다르다.

2026-10-17 11:58:07 UTC root 공개 RO는77컨테이너/16running, 실행 중14healthy+2noHC·unhealthy0·daemon OFF를 확인했다. 이 작성자는 운영/SDK/DB/Docker/provider에 접속하지 않았다. 현재 사용자 지시에 따라 daemon을 자동으로 켜거나 복구하지 않는다. stopped daemon에 남은 과거 health는 실행 중 장애 수에 합산하지 않는다.

## 이미 완료한 한정 범위

| 항목 | 코드·머지 | 배포·수용과 한계 |
| --- | --- | --- |
| 지역 수집 원인 보강 | [#78](https://github.com/digitie/kor-travel-weather/pull/78) KREX WAF client/upstream_blocked, [#87](https://github.com/digitie/kor-travel-weather/pull/87) 산악 비기온 유효값 gate·streaming chunk·공유 anchor lock MERGED | #87 두 독립 제품 FULL과 배포 source 보존은 [stack closure](../reviews/2026-10-17-weather-stack-closure.md)16–31행. 최신 휴게소·산악 관측의 실제 누락 해소/신선도 완료 증거와는 다르다. |
| 수집 CPU·메모리 경로 | [#88](https://github.com/digitie/kor-travel-weather/pull/88) graph 재사용/분산예약, [#98](https://github.com/digitie/kor-travel-weather/pull/98) 실행기/복구, [#107](https://github.com/digitie/kor-travel-weather/pull/107) 배치 복사·해시·flush, #108 source-record redaction MERGED | 소스/격리 회귀·실제 배포 증거와 동일 workload 전체 수집 CPU/RSS 수용을 구분한다. |
| sampling·건강 점검 | spatial sampling은 이미 존재. [#102](https://github.com/digitie/kor-travel-weather/pull/102) Common HTTP/gRPC·stdlib checked-hash MERGED | 새 RO에서 실제 API90초/CS90초, UI는 기존 noHC. source Compose API30초와 Manager 실제 override90초는 다른 값의 자리다. |
| 과거6시간/미래 무제한 | [#103](https://github.com/digitie/kor-travel-weather/pull/103) valid_at exact predicate/복합키/미래 보호·0020, #109 무진행 deferred/진행 이벤트 MERGED | [초기 정리](../reviews/2026-10-17-weather-short-forecast-initial-purge.md)의55,987 fact 삭제·미래8표본 보존 완료. 최신 native run8d87945a-d022-4495-a4b2-4f4b27126d6b는 SUCCESS/complete_snapshot·batch1/matched0/remaining=false·영속8필드 Observation1개이며 공유 web 재조회로 확인됐다. 해당 snapshot 완료이며 영구 만료0 보장이 아니다. |
| 최신 API/CS와 UI | #109 배포 뒤 CS 전용 관측 이벤트 보완 | CS139f/이미지71ff/source8cdb78d, API d48c/이미지2d0/source8d2777b 유지. 배포 뒤 같은 기준 UI36/36 PASS·daemon OFF. label 차이만으로 API 전체 재배포가 필수라고 하지 않는다. 앞선 엄격 module proof와 새 RO label은 별도 근거이며 label만으로 전체 파일 동등성을 증명하지 않는다. |

Weather root/독립 Dagster 패키지의 Common pin은 c48595880aaad4367802ad71af2525a8a5e17c6a다. 공유 Dagster host의 Common92d pool 소비는 다른 설치 표면이다. 상위 Common 미머지를 이유로 Weather pin을 임의 변경하지 않는다.

## 우선순위·잔여 단계·최소 다음 행동

P는 진행 우선순위이며 새 제품 결함 심각도 판정이 아니다.

| ID/우선 | 남은 것과 상태 | 최소 다음 행동 | 선행·정본 |
| --- | --- | --- | --- |
| W-OPS-01 / P1 | 지역 데이터의 **운영 최신성 수용 미확인**. adapter 자체는 완료 | root가 기존 native run/앱 sync 이력의 terminal 상태·fetched/usable·좌표없음·disabled·commit 수·관측 시각을 provider별 RO 대조. disabled/credential 부재/upstream blocked/빈 응답/게시 실패를 나눈 뒤 실제 결함일 때만 최소 수정 | daemon OFF, 수동 수집 별도 승인. [산악 gate](https://github.com/digitie/kor-travel-weather/blob/39aafe0d8a2c6f8035bb1e89e6f5a17c94a7d34e/src/kortravelweather/providers/krforest.py#L162), [KREX latest](https://github.com/digitie/kor-travel-weather/blob/39aafe0d8a2c6f8035bb1e89e6f5a17c94a7d34e/src/kortravelweather/providers/krex.py#L235), [disabled skip](https://github.com/digitie/kor-travel-weather/blob/39aafe0d8a2c6f8035bb1e89e6f5a17c94a7d34e/packages/kor-travel-weather-dagster/src/kortravelweather_dagster/regional_sources.py#L71) |
| W-OPS-02 / P1 | **정식 source/consumer 경로 정산**. 이미지/UI는 이미 동작 | 새 RO의 native_source_worktrees.weather.worktree_exists=false는 해당 probe 경로 부재만 뜻한다. root가 대체 NVMe canonical build-context/소스 archive·HEAD를 확인하고 필요한 동기화만 수행. PinVi ETL/trip weather·scrape/federate 기존 효과 증거 정산 | 현재 image 성공과 미래 rebuild 가능성은 별개. source 소실·consumer 장애로 단정하지 않음. [host](../../deploy/README.md)45행, [consumer](../integration-map.md)31–48행, [관측](../observability.md)143–164행. 타 repo 구현은 해당 소유 |
| W-DEP-01 / P1 | **Manager 정본/운영 override drift**, 공통 소유 항목 | root의 최신 소스 확인상 Manager main에는 Weather API urllib probe와 CS legacy probe가 남는다. 현재 안전 override를 보존하는 최신 main 통합이 필요. #486/#488을 전부 superseded로 닫거나 구 head를 그대로 머지하지 않음 | [Manager API](https://github.com/digitie/kor-travel-docker-manager/blob/e84f3ce9dfd339056dd422a9b91adddc33c4d6ca/docker-compose.yml#L2847), [CS](https://github.com/digitie/kor-travel-docker-manager/blob/e84f3ce9dfd339056dd422a9b91adddc33c4d6ca/docker-compose.yml#L2987), [legacy anchor](https://github.com/digitie/kor-travel-docker-manager/blob/e84f3ce9dfd339056dd422a9b91adddc33c4d6ca/docker-compose.yml#L97), [#486](https://github.com/digitie/kor-travel-docker-manager/pull/486)/[#488](https://github.com/digitie/kor-travel-docker-manager/pull/488). Weather Compose 자체는 이미 Common CLI. 현재 운영이 kill-probe라고 주장하지 않음 |
| W-OPS-03 / P2 | 실행기/예약·복구 **실제 상태/평가 수용 미확인** | native retry 설정·현재18job 태그·Common/native 단일 소유자·선택 센서 등록/상태/첫 평가·중단재개 증거 확인. 살아 있는 worker/UNKNOWN의 상태 조작·retry 이중 발급 금지 | [선택 등록](https://github.com/digitie/kor-travel-weather/blob/39aafe0d8a2c6f8035bb1e89e6f5a17c94a7d34e/packages/kor-travel-weather-dagster/src/kortravelweather_dagster/recovery.py#L15), [운영 gate](../weather-recovery-executor-adoption.md)58–82행. OFF에서 자동 평가 중단은 정책상 예상, 활성화 금지 |
| W-OPS-04 / P2 | 정기6시간 정리 **지속 수용**. 초기삭제/native/이벤트는 완료 | 별도 승인된 스케줄 재개 때 backlog 처리량·deferred/remaining·미래/경계 보존·실패 진행 관측.0020/대량 purge를 체크용 재실행하지 않음 | [10분 schedule](https://github.com/digitie/kor-travel-weather/blob/39aafe0d8a2c6f8035bb1e89e6f5a17c94a7d34e/packages/kor-travel-weather-dagster/src/kortravelweather_dagster/definitions.py#L1391), [미래 보호](../shortterm-retention.md)84–107행, [관측 경계](../shortterm-retention-progress.md)20–33행. queue3 보존은 삭제/자동 실행 승인이 아님 |
| W-OPS-05 / P2 | CPU/RSS·sampling/HC·T-WEA-02 **장시간 자원/효과 수용** | 동일 provider/config/수집량의 CPU·RSS/PSS·wall·DB/row 수 및3h/12h 포함24h OOM/swap 정산. 실제 mem/swap 값·sample 대상/제외 효과 확인 후 조정 | [T-WEA-02](../../deploy/n150.md)758–766행, [sampling](https://github.com/digitie/kor-travel-weather/blob/39aafe0d8a2c6f8035bb1e89e6f5a17c94a7d34e/src/kortravelweather/providers/sampling.py#L75), [실측 범위](../weather-recovery-executor-adoption.md)92–95행. OFF/부하 미통제 idle 표본은 인과 개선 증거 아님 |
| W-DOC-01 / P2 | 옛 절차·원장 정산 | 이번 audit/tasks/resume/journal로 시점 구분. 실제 경로/효과 증거가 확보되면 해당 현행 절차만 후속 최소 갱신. 과거 실패/미실행 기록 보존 | 아래 목록. 코드·config·tests 추가 변경0 |

### CPU·메모리 표본의 한계

기존 root60초 관측은 host busy18.726%(iowait 포함), Weather CS6.952%/API1.060%/UI0.000912% **one-core** CPU다. 해당 cgroup memory.current는 CS210,534,400B/API97,087,488B/UI205,598,720B다. workload 통제=false이며 peak RSS/PSS나 종료 run 부하가 아니다. 이번 감사가 새로 측정하지 않았고11:58 RO도 CPU/RSS 신규 측정0이다. 서비스 수가 다른 옛8서비스 창과 단순 전후 절감률로 계산하지 않는다.

## 낡은 체크·조건부 후보를 새 코드 잔여로 세지 않을 것

- [deploy/n150.md](../../deploy/n150.md)744–754행의2026-10-13 eMMC/Docker/NTP 상태, [deploy/README.md](../../deploy/README.md)45–68행의 현재 n150/이전 미구현은 당시 계획이다. 최신 Odroid runtime/UI/HC와 시점이 다르다. NVMe·OS/boot·방화벽 전체 수용을 이 감사가 새로 증명한 것도 아니다.
- T-WEA-02(758–766행)는24h 실제 수집창 수용이 남은 부분. 옛1.5g·cgroup v1·fresh DB 전제를 현재 강제 적용하는 코드 지시가 아니다. 3h/12h/OOM/swap 목표와 현 정책 정합은 소유자 확인이 필요하다.
- T-WEA-03(767–770행)은 base가 amd64-only일 때 ARG PYTHON_BASE를 검토하는 조건부 후보. 현재 실제 ARM 이미지 실행 증거가 있으므로 새 ARG 개발이 필수라고 단정하지 않는다. 지정 base manifest-list 별도 확인 기록은 이번 자료에서 찾지 못했다.
- T-WEA-04(771–773행)는 bulk catalog 명령의 소유자 결정 후보. 기존 단건/정상 배포가 수용되면 만들지 않는 계약이다.
- [stack closure](../reviews/2026-10-17-weather-stack-closure.md)41–48행의106 merge 대기, [runtime cadence](../weather-runtime-cadence.md)52행의 검토/배포/live 대기는 역사다. 현재106~109는 MERGED이며 job별 부하/복구 수용만 따로 남긴다.
- #98 도입 문서의17job 수는 당시 기준이다. 현재 definitions.py의 _COLLECTION_JOBS는9개 개별 항목과 외부9개, 총18개다. 새 단기예보 정리 job이 포함된 source 수이며 실제 운영 등록수의 새 측정을 대신하지 않는다.
- [integration-map](../integration-map.md)54–63행의 다른 프로젝트 proposed/미구현은 당시 교차 참조. Weather에 Geo token 감지·재지오코딩 코드를 추가하지 않는다. Track1 소비자 수용은 각 소유자 감사로 분리한다.
- [docker runbook](../runbooks/docker-app.md)52행의 migration0008은 현재0020보다 오래됐다.67–69행의 부분 게시 부재도 [현재 부분 commit 계약](../runbooks/dagster-recovery.md)33–46행과 구분해 현행 절차를 정산해야 한다.
- [standalone deploy.sh](https://github.com/digitie/kor-travel-weather/blob/39aafe0d8a2c6f8035bb1e89e6f5a17c94a7d34e/deploy/deploy.sh#L54)는 /tmp 고정 로그를 쓴다. 현재 root NVMe 단일역할 틀과 다르므로 현행 Manager 운영 경로로 무검증 재사용하지 않는다. explicit NVMe 지원의 제품화 필요는 canonical 경로 확정 뒤 작은 별도 범위로 판단한다.

## 최소 다음 순서

1. 문서 PR은 사용자 승인으로 [skip ci]. 링크/diff/사실 검토·독립2문서 리뷰 뒤 root가 머지한다. full suite·이미지 build·새 live UI를 문서 때문에 재실행하지 않는다.
2. 기존 수집 이력·정식 source 경로·consumer/관측 증거의 RO 정산부터 한다. Manager source/runtime drift는 공통 소유자 후속 통합으로 넘긴다.
3. daemon OFF 유지. 수집/스케줄/복구 재개는 소유자 별도 결정이다. 승인 후 지역 freshness·장시간 자원·단일 retry 소유권부터 한정 관측하고 확인된 결함만 새 feature PR로 고친다.
4. 체크박스를 채우려고 과거 purge/DDL/host 초기화/이미 끝난 UI를 반복하지 않는다. PinVi·Manager·Common 단계는 그 소유자에게 전달한다.

## 공개 증거 출처

운영 원문·설정·payload를 복사하지 않고 root 공개 safe 요약을 대조했다. 저장소 밖 승인 증거 보관소의 이름과 SHA256이다.

| 근거 | SHA256 |
| --- | --- |
| remaining-three-projects-native-read-root-r1-20261017.safe.json | 41858f29ba250fc3f112b825bce93e9009944d2b92623dd1845c1444027a35cf |
| track-weather109-progress-postdeploy-ui36-root-r3-20261017.root-bound.safe.json | 290ac6935007fe8883e7c46b568c5fcc1b6c494cac26940bbf843329c54a6959 |
| weather109-event-image-module-proof-root-r1-20261017.safe.json | 9692aaf97d0571367f10e333c494df4955dde89a643d9d1ac0dc095bcac1c4f6 |
| weather109-actual-native-retention-root-2edde532-5a8e-47a7-82ba-116f4770bbb0.safe.json | 9fdfabc7bc7f902a21757853b217b545703417f8908e6f11a868dfb89d9e87c6 |
| weather109-shared-web-event-confirmation-root-r1-20261017.safe.json | 60389a329b8ccb4005fb958933b5fd2a0c88d981d43b6809fc728a03abc5bf2e |
| weather109-idle-postmerge-cpu60-root-r1-20261017.safe.json | 3e213bbe28f780a0e68d30d95ab71ec0df03a40b0dc7ffd3b4e86a216d08bf7c |

109 native 실행은 daemon restore=false·queue dispatch acceptance=false, queue count3/ID hash 전후 동일·전체 inventory 불변이었다. 실운영 SQL 예외의 고의 주입은 아니며 ERROR/WARN 진행 이벤트의 SQL 예외 뒤 재개는 별도 격리 SDK/SQLite 회귀 범위다. 이번 source 감사의 미확인 항목을 실패/미배포로 확정하지 않는다.
