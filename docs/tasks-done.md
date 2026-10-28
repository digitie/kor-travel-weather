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

