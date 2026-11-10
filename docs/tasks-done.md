## 2026-11-10 — 최신 main 이미지 반영

- [x] **W-LATEST-IMAGE-20261110** — PR217까지 포함한 API·수집기·웹 이미지 실제 반영, 원27 복원·Manager·부모0/자식 종료·HTTP 수용 완료. 화면은 보고서의 실제 관측 범위만 인정한다. 기존 조회/원천 한계와 모바일 실패 이력은 별도다. [근거](reports/2026-11-10-latest-main-image-release.md).

## 2026-11-09 — 강제 중지 승인 후 최신 API·수집기 배포 완료

- [x] **W-COLD-REBASE-DEPLOY-20261109** — PR209 통합·최신 API/수집기 이미지·원 일정 복원·Manager·실제 부모 종료 수용 완료. 최초 조회 성능 미달은 W-EXPORT-SLOW-20261108로 유지한다.

사용자의 “강제 중지하고 올려” 지시에 따라 Weather 실행·큐4건을 공식 강제 취소했고, 실제 terminal3CANCELED/1FAILURE·old worker0을 확인했다. app 실패 이력과 이미 저장된499,968건은 그대로이며 canonical 회수 변경도0이었다. 수집기를 제품8723afc/이미지68c093c9·CIDb1626cea로 교체했다. 같은 API f3f7a4a8/CID810b4885와 웹·다른 서비스·설정/키는 보존했다.

API·수집기 healthy, source SHA·실효 설정/키·DB0039, 원 Weather27 일정 복원, Manager healthy, 최초 Invocation8a75bb1f2b2949898fde4003ecc087a5 실제0/done·Main0·cgroup부재·소유 자식 종료까지 확인했다. 반영 뒤 HTTP health/version200과 새 수집기의 CommonCLI-I live gRPC0도 확인했다. 자연 대기 부모의 사용자 취소1·첫 SAFE 정책 거부1은 각각 원문으로 보존했다. 반복 빌드·migration·백업/복원·원본 삭제는 없었다.

제품은 최신2d5282f와 실행 AST가 같고, 해당 head의 Python/frontend CI42529909792는 모두 SUCCESS다. WSL/Docker PG18각127개 및 GC후속각58개 통과를 보존한다(겹침 합산 없음). serving current 최초52.435초·daily13.114초·상태 만료 뒤current23.840초이므로 최초1초·cold/p95 달성으로 쓰지 않는다. [최종 근거·한계](reports/2026-11-09-cold-export-read.md).

## 2026-11-08 — 완료 페이지 재사용 최종 API 실제 수용

- [x] **W-AGGRESSIVE-READ-20261108** — 0039 삭제 epoch·완료 페이지 재사용·짧은 pin·PG16/18 호환 검증, 최신 API 이미지/Manager/원47상태/실부모 종료와 HTTP200/304·인증401을 수용했다. 전체 재구성 지연은 W-EXPORT-SLOW-20261108로 유지한다. [근거와 한계](reports/2026-11-08-aggressive-export-read.md).

## 2026-11-08 — REMAINS-20261108

- [x] 실제 첫 배포 수용 실패를 재현하고 승인 상태4키집합/목록 배열·지역 대표 입력/명시 접기·빈 portal 마커 측정 오류를 후속 수정했다. WSL/Docker·최소3이미지 후속 및 Weather 웹 전용 실제 배포·n150 PC/모바일·원47상태/Manager/실부모 종료를 정산했다. 전체1초/원천 stale·미제공은 별도 한계다. [원장](reports/2026-11-08-remaining-read-and-overlap.md).

## 2026-11-08 — READ-NEXT-20261107 실제 반영 정산

- [x] **READ-NEXT-20261107** — 추가 조회 개선/60초 상태 캐시0180e523의 API 실제 반영·n150 지도/상세 PC모바일·27일정/Manager·최초 부모 종료를 수용했다. first current503과 긴 조회는 별도 미완이다. [원장](reports/2026-11-08-selection-cache-release.md).

- [x] **W-READ-MAX-02/03** — 세 회차 제품7ccf88a의 최종 API/수집기 반영·원27복원·Manager healthy·최초 실제 done/자식 종료와 실제 HTTP200/304·PC/모바일 수용을 마쳤다. Transport 세 회차·잔여 stale/지연은 별도다. [근거](reports/2026-11-07-read-max-optimization.md).
- [x] **W-READ-MAX-01** — 1회차 전체 확정 항목 구현·WSL/Docker·전후 검산과 API/수집기/DB0038 반영, 원27복원·Manager healthy·최초 실제 done/자식 종료를 확인했다. 남은 HTTP 지연과2·3회차 배포는 별도다. [근거](reports/2026-11-07-read-max-optimization.md).
- [x] **W-READ-MAX-CODE-02/03** — 2회차 JSON 계산·3회차 fetch 왕복 개선 및 WSL/Docker95개씩 검증. 운영 배포 완료와 구분한다. [근거](reports/2026-11-07-read-max-optimization.md).

- [x] 2026-11-07 Weather PR207 최신 제품3a4ae3f API·수집기 배포, 자연 drain·원27상태 복원·Manager·실제 종료·PC/모바일/API 검증. 웹·DB 유지, backfill 미실행. [기록](reports/2026-11-07-weather-pr207-release.md).

- [x] 2026-11-07 Weather PR206 최신 제품87a0b50 API·수집기 배포, 자연 drain·원27상태 복원·Manager·실제 종료·PC/모바일/API 검증. 웹·DB 유지, backfill 미실행. [기록](reports/2026-11-07-weather-pr206-release.md).

- [x] 2026-11-06 Weather PR205 제품ba66543 API·CS 반영·실제5초·원27상태·Manager·부모 실제 종료·PC/모바일/API 확인 완료. 웹·DB 유지, 백업·복원 생략. [기록](reports/2026-11-06-weather-reader205-release.md).

- [x] 2026-11-05 최신 Weather 제품9acc409의 웹·API·CS 이미지, DB0037, 원26상태·새 특보 일정, Manager·부모 실제 종료와 PC/모바일 화면을 확인했다. 백업·복원은 사용자 지시로 생략. [수용 기록](reports/2026-11-05-latest-weather-release.md).

## 2026-11-03 — 읽기 비용 API·DB·실제 HTTP 수용

- [x] **W-READ-COST-01 운영 후속** — 제품539d6/API4c349d·schema0036·healthy·Manager 정산·부모 실제0/종료를 확인했다. 기존 웹·CS·일정·환경을 보존했다. 자기 invalid index 한 개를 삭제 없이 검산·복구하고37행의 메타데이터 지문을 보존했다. 실제500행200·조건부304를 관측했으며 최초GuardTimeout503도 보존했다. [실제 증거와 한계](reports/2026-11-03-weather-read-performance.md).

## 2026-11-03 — 읽기 반복 비용 구현·로컬 검증

- [x] **W-READ-COST-01 구현** — W1 큰 JSON 조건 반복 해석, 304 불필요한 전체 직렬화,
  일반 예보 metric별 재순회를 줄였다. 원문·발급순·TTL·cursor·결측·출력 값 회귀를 확인했다.
- [x] 격리 PG18·WSL/Docker 집중 검증 완료. 운영 반영과 운영 cold 성능은 별도 미완이다.
  [측정·검증·적용 경계](reports/2026-11-03-weather-read-performance.md).

## 2026-10-31 — 적용 특보가 없을 때 빈 칸 숨김

- [x] 지도·상세 미확인 설명과 불필요한 칸을 제거하고 유효한 특보가 있을 때만 카드를 유지했다.
- [x] 웹 단독 배포·실제 PC1440/모바일375·설명/특보 패널0을 확인했다.
  API/수집기 재배포·백업/복원은 하지 않았다. [실제 결과](reports/2026-10-31-alert-weather-separation.md).

## 2026-10-31 — 특보의 뇌우 오변환과 미확인 구역 적용 방지

- [x] 특보와 실제 날씨 상태를 분리하고 KMA 구역의 정확한 일치와 미확인 안내를 반영했다.
- [x] API·웹 c712956 운영 반영·Manager healthy·실제 PC/모바일 마커와 서울 상세 검사를 완료했다.
- 구역 자료의 실제 연결은 W-ALERT-ZONE-01로 남겼다. [원 실패·전후 캡처·한계](reports/2026-10-31-alert-weather-separation.md).

## 2026-10-31 — 날씨 상세·지역 대표값 운영 화면 확인

- [x] API·웹 `e8733c5e`를 운영 반영하고 정본 Manager 등록·검증을 완료했다.
- [x] 실제 Firefox1440/375에서 현재/내일·작은 부가 정보·맨 아래 시간별 그래프·대표값/클러스터 전환을 확인했다.
- 수집기·기존 실행·DB·환경은 유지했고 백업·복원은 사용자 지시로 생략했다.
  행정구역 별칭과 미제공 자료의 한계는 [실측 원장](reports/2026-10-31-details-regional-live.md)을 따른다.

# 완료 작업

## 2026-10-28 — 특보 후보 제한 API 단독 배포와 첫 cold 수용

- [x] **W-MARKER-PREFIX-01 / P1** — 원 후보21d9의 전체 WSL1545/Docker1544+1SKIP,
  관련150×2, 통합7504의 관련150×2·정확 tree CI771, 원 소스2인·통합2인 검토를 구분해 회수했다.
  [PR #190](https://github.com/digitie/kor-travel-weather/pull/190) 머지는0683431이다.
- [x] API만 교체하고 실제 build·SDK·accept·rollout0 및 역할·보호파일 보존을 확인했다.
  첫 Firefox29PASS/actual0, 첫60×3 HTTP200·본문18.075/16.469/17.593초·고유180지점·동시2,
  1440/375 지도·실타일·상세·DTO·키보드·로그아웃 쿠키0·소유10PID 부재를 수용했다.
- 원 a707 cold26초 FAIL·warm 진단·QA 실패 원문은 보존한다. 전국 전체25초/장시간/원인별 개선율,
  main0035·CS/web 새 배포·외부 소비자 OFF 직접 관측·retention/특보 지역 정합 수용은 포함하지 않는다.
  [증거·현재 이미지·미완 경계](reports/2026-10-28-marker-prefix-deployment-acceptance.md).

## 2026-10-25 — 첫 작은 UI 묶음 구현·로컬 검증

- [x] **W-UI-BATCH-01 구현·로컬 검증** — Weather navy/dark `--kt-*` 연결, 지도 제목/상세 메타 `i`,
  `/weather`만 compact 헤더, coarse 포인터 44px 버튼을 반영했다. WSL 타입·lint·253개와 Chromium/Firefox UI8개 PASS.
  [검증·미완 경계](reports/2026-10-25-weather-ui-compact.md), 최종 CI·독립 리뷰·머지는 [PR #169](https://github.com/digitie/kor-travel-weather/pull/169)가 정본이다.
- C-MAP-01/W-UX-01~03 전체 수용·실제 운영 타일·전체 가용 높이·배포는 완료로 세지 않는다.

## 2026-10-25 — 웹·Android/iOS 조사와 날씨 지도 설계 문서

- [x] **W-UX-DOC-01 문서 작성·검토** — [사전 조사](research/2026-10-25-weather-ux.md)와
  [후속 설계·수용 조건](design/2026-10-25-weather-map-improvements.md), 두 차례 정합 점검을 마쳤다.
  독립 Popper 1명의 미등록 목적지 검색 P2를 반영하고 동일 리뷰어 재검토 PASS(잔여 P0/P1/P2 없음)를 받았다.
- [x] Markdown만 정리했고 기능/DB/운영·빌드/배포는 변경하지 않았다. [검토 기록](reports/2026-10-25-weather-design-review.md).
  최종 머지 상태는 [Weather #168](https://github.com/digitie/kor-travel-weather/pull/168)과
  [Transport #130](https://github.com/digitie/kor-travel-transport/pull/130)이 정본이며 문서 PR은 사용자 지시대로 CI 결과를 기다리지 않는다.
- C-MAP-01/W-UX-01~03 구현·실제 운영 수용은 미완료 백로그에 남겼다.

## 2026-10-24 — npm 보안·버전 정합 소스 완료

- [x] **Next.js 보안 갱신 — Next.js 16.4.3**. npm 직접/전이 보안 패치와 공통 버전 정합, 이 저장소 기존 테스트 243개·타입·린트·빌드 검증을 반영했다. 최종 게이트와 머지 상태는 [PR](https://github.com/digitie/kor-travel-weather/pull/166), 잔여 상류 권고와 범위는 [정합 기록](npm-security-alignment-2026-10-24.md)을 따른다.

- [x] 2026-11-02 모든 미반영 PR 통합·새 ARM64 이미지 운영 반영·PC/모바일 실제 확인. [결과](reports/2026-11-02-all-pr-images-release.md)

- [x] 2026-11-03 PR198 API 새 이미지 반영·응답14사례·실제 health/status200·Manager 정산. [원 실패와 범위](reports/2026-11-03-pr198-api-release.md)
