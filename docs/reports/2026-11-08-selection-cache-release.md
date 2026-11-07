# 2026-11-08 — 승인 상태 캐시 배포와 실제 한계

소유자가 최신 수집·실패 상태 반영 지연을 최대1분으로 명시 허용했다. API current/daily만60초·4항목·총16MiB(항목8MiB) 상태 캐시를 쓰고 RR 조회 시작 전부터 시간을 센다. hit로 연장하지 않고 만료·실패를 정상200/304로 숨기지 않는다. hit마다 불변 plan·세대 존재를 같은 RR에서 검산하고 파일·참조·source clock·인증/cursor·5초 SQL 제한을 유지한다. SDK 기본·forecast/location/status는 그대로다. JSON 원문 epoch 의미를 보존한 시도별 집계와 잠금 밖 조회/검증을 사용한다.

WSL/Docker PG18 집중134개씩, 예산 경계8개씩, plan 즉시 검사21개씩을 통과했다. 겹침을 합산하지 않는다. 실제3,306 scope의 같은 선택/시각/stale SHA를 대조한 구간 측정은 최초22.19초, 존재 검사를 포함한 재사용0.639/0.624초다. HTTP나 RSS/p95 수치가 아니다. API/보안 스키마 동일, current/daily 설명만 갱신됐고 웹/의존성/SDK/schema 변경은 없다.

## 실제 역할과 종료

제품0180e52308daac5f2f099dc7b459cfd0d84f2417, API 이미지sha256:8eb03b247a5078bde7f98ea611869cca9743170210b38a5ce64a6bb8a7f6d77c, CID96c8844147ba26cea2649802935f49e37437b36964636df948b1a8bc74b82268/실제Cmd12201이다. CS는cc0491e/이미지f7939f6b/CIDe6286b16, 웹은508a438/CIDb1d95fc7/12205를 유지했다. PG18.6/schema0038, Settings5초와 키 일치를 확인했다. 새 API 빌드 첫Invc2c60085eb324976bbe4b44c65c52d89는 실제done/Main0/cgroup부재/native PASS·13자식 종료0이다.

원cc0491e 배포 b6a5c15db1154611b52d3eb2bb4fe18a는 자연 drain과 API/CS 교체·27일정 복원·Manager healthy 뒤 타 runtime 동일성 AssertionError로 부모1을 남겼다. 실패 원문·493자식 종료0·cgroup부재를 보존하고 별도 읽기 전용 관측으로 실제3역할/환경·키·27상태/Manager를 검산했다. 어떤 타 runtime이 달라졌는지는 원문만으로 확정하지 않는다.

새 API 교체 부모75b3d8d7662f4e94b40135c87c6527ef는 교체·healthy/환경 검사 뒤 검산 명령이 isolated Python에서 API PYTHONPATH를 빠뜨려 ModuleNotFoundError로 종료했다. 서비스 모듈 경로를 명시한 별도 검산에서 실제 실행 app 소스 SHA1ca46f1b3f5d3da2dd4626a61e3ccbed31839174828d8fcdf8ee08d83f8c79d9·캐시 초기화/60초·Settings5초를 확인했다. 이미 반영된 API를 재생성하지 않고 정상G/R의 metadata 정산 첫Inv9b2ab84cb3c64f18a667c4c45d399b57를 실제done/Main0/cgroup부재·28자식 종료0으로 완료했다. 원 CLI1을0으로 바꾸지 않는다. 원27상태/웹/CS·타 서비스는 같으며 backup/restore는 사용자 생략 지시를 유지했다.

## 실제 HTTP

| 요청 | 실제 결과 | 시간 |
|---|---:|---:|
| health/version/미인증 current | 200/200/401 | 0.70/0.09/0.08초 |
| current 첫 소유 관측 | 503 EXPORT_TEMPORARILY_UNAVAILABLE | 75.74초 |
| daily 첫 관측 | 200, 10개, stale | 30.95초 |
| daily If-None-Match 요청 | 200, 10개, stale | 14.59초 |
| 최소62초 뒤 current | 200, 100개, stale | 48.02초 |
| 최소62초 뒤 daily | 200, 10개, stale | 17.31초 |

SQL 제한은5초 그대로이며 이번 관측 client 상한만90초다. 이전45초 Timeout UNKNOWN을 덮지 않는다. 재시도/수동 수집은 없으며 실제 source_collected_at2026-11-06T20:57:43.991746Z와 stale를 보존했다. first503의 세부 원인을 이 HTTP 상태만으로 단정하지 않는다. 부분 선택 구간의 개선을 전체 API 개선 배율이나 모든 요청200/304로 과장하지 않는다. 최초 current 지연과 전체1초 목표는 남는 과제다. PC·모바일 관측은 후속 절에 실제 결과로 정산한다. Git 밖 정본은 weather-transport-read-next-20261107/plan.json 및 weather-selection-release/live-http.json이다.

## 최종 n150 화면 수용

Firefox157 PC1440/모바일375에서 실제 지도 marker·타일, 현재/내일 상세, 10px/12px 부가정보,24시간 예보 그래프와 25개 표 머리칸(시각24+레이블1)을 확인했다. 최종 Xvfb/소프트웨어 렌더링 관측은 지도 오류 배너0·overflow0·logout200·소유 브라우저 자식 종료/비공개 자격 파일 삭제다. 실제 캡처를 열어 지도와 상세를 검토했다. 날씨 상태/미세먼지 등 원천 미제공 값은 그대로이며 성공으로 채우지 않는다.

첫 시스템 driver CLI 부재와 별도 headless 지도 표시 오류는 보존했다. 작업 전용 기존 Playwright 패키지와 Xvfb를 사용했으며 shared 설치/서비스·제품/UI·이미지 변경은 없다. 마지막 원장은 weather-selection-release/final-acceptance.json, SHA742424210a7b0eadfe591653851e0ac0bd00a2d0f75fe5992e5eebce46428ac6. 실제 API 배포·설정·일정·화면 수용과 최초 current503/남은 지연을 구분한다.
