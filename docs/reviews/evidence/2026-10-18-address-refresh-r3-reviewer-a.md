# Weather111 formal FULL post-fix 독립 적대적 리뷰 — James r3

- **판정: SOURCE_PASS_WEATHER_FIXTURE_ONLY. OPEN P0=0 / P1=0 / P2=0.**
- W111-A-P2-03 CLOSED. 이전 P2-01/02 closure는 문서화한 단일 Node 프로세스 및 UI 상태 구분 범위로 유지한다.
- 검토자 실행 ID: /root/review_finish_a. Frontend/live UI·비동기 상태·auth/CSRF·재시도·사용자 상태 진실성 렌즈.
- Weather base: 28f731e6dcb12d5f4cde2ad86fd25acbf3da2eac.
- Weather exact head: ba40131d224fdb34dfd125c27e15e4a2a1f6df17.
- 이전 본인 head: 78817516561968868bb7a77c6cb17c6b90cefee5.
- Common pin: c067a7f6a11b106931a73cd7465503d90e54d976.
- Manifest: /home/digitie/dev/reverse-address-weather-review-manifest-20261018-r3.json.
- 실제 manifest SHA256: e788e6bed6b7b13a45c7c4bfcb04d11d8c6c797ec1d2b15c6fe55cdabb747091.
- 시작 UTC: 2026-10-18T02:46:10.039478+00:00.
- Transport 정식 수용/운영 배포/실제 provider 검증은 포함하지 않는다.

## 범위·격리·전체 변경 보존

고정 Git object의 AGENTS를 다시 읽었다. 기존 r1/r2 FULL 검토의 전체 base→788 제품 연결을 계승하고, 이번 base→ba401 전체 변경 집합과 788→ba401의 모든 delta를 대조했다. base→head는 44파일 1778+/19-이며, 신규 7파일은 이전 리뷰/manifest 아카이브와 정산 문서다. 이전 head→현재는 13파일이다.

실제 제품 delta는 정확히 세 경로다:
1. frontend/components/address-refresh.tsx: 오류 상태 분리.
2. frontend/e2e/address-refresh.spec.ts: 지연 GET 반례.
3. frontend/lib/api.ts: EOF 빈 줄 제거.

frontend는 packages/kor-travel-weather-admin/frontend 하위다. 이 세 경로 외 제품 source/config/dependency/BFF/API/OpenAPI/migration/Common pin/Dagster/인증·session·Origin/CSS는 788과 바이트 동일함을 git diff 이름 집합으로 확인했다. 따라서 기존 전체 검토에서 확인한 보호를 새 코드를 추정하여 계승한 것이 아니라 변경 부재를 확인하여 계승한다. 새 문서·history도 읽었으며, 이전 상대 r1/r2 보고서는 원문 확정 후 공개된 역사 evidence다. 상대의 이번 r3 결과는 보지 않았고 판정 근거에 사용하지 않았다.

시작·종료 worktree status는 clean이다. immutable git show와 소유 임시 디렉터리/메모리 fixture만 사용했다. 제품/index/branch/WIP/기존 원문을 수정하지 않았다. Vitest 복사본·config/cache는 소유 임시 디렉터리 안에 만들고 정상 종료 후 제거했다. 실제 운영/SSH/Docker/provider/키/실제 DB/SDK 실행/Daemon 조작/브라우저는 모두 0이다.

## W111-A-P2-03 — 지연 GET이 POST 실패를 지우는 경합: CLOSED

기존 위치:
- 이전788 components/address-refresh.tsx:27의 GET200 setError("").
- 같은 상태를 사용하는 :46–49 replay 실패와 :58–59 예약 오류.

현재 위치:
- components/address-refresh.tsx:13–14: loadError와 launchError가 독립 상태다.
- :28–30: 상태 조회는 loadError만 변경한다.
- :36/:48–50/:60: 예약 재시도 시작·실패는 launchError만 변경한다.
- :77–78: 두 오류가 각각 role=alert로 표시된다.

원 실패 순서:
최초 configured=true GET → 두 번째 상태 GET 대기 → 수동 예약/동일 요청 replay가 실패 → 실패 alert 표시 → 앞선 GET200 도착. 이전 제품은 끝의 GET200이 방금 받은 실패 alert를 삭제했다.

독립 재현:
양쪽 exact Git object의 실제 AddressRefresh TSX를 TypeScript transpile하여 새 Node VM에서 각각 실행했다. useState/useRef/useCallback/useEffect/JSX는 소유 stub, fetch는 지연 Promise 대역이다. 실제 load/launch/button 콜백을 사용하고 결과 구조의 alert를 검사했다. React DOM/실브라우저 실행은 아니다.

| POST 결과 | 788: GET200 전→후 alert 수 | ba401: GET200 전→후 alert 수 |
| --- | --- | --- |
| 기존 FAILURE replay | 1→0 (RED) | 1→1 (GREEN) |
| 기존 CANCELED replay | 1→0 (RED) | 1→1 (GREEN) |
| HTTP503 예약 오류 | 1→0 (RED) | 1→1 (GREEN) |
| 네트워크 예외 대역 | 1→0 (RED) | 1→1 (GREEN) |

8개 old/new 실행 모두 기대값과 일치하고 Node exit0이다. 네 유형에서 GET은 정확히 두 번이며, 지연 응답 후 summary가 갱신되어도 예약 실패는 유지된다. 오래된 GET의 성공은 자신의 조회 오류만 지우며 예약 오류에 접근하지 않는다. 실패 replay에서 후속 load를 생략하는 기존 보조 방어도 유지됐다.

새 E2E는 같은 순서의 delayed GET을 직접 잡아 둔 뒤 실패 안내를 먼저 확인하고, GET을 풀어 total2 표시까지 기다린 후 동일 실패 안내가 남는지 검사한다. desktop/mobile viewport 반복 안에 들어 있고 실제 제품 오류를 숨기도록 시간·기준을 완화하지 않았다. 본인이 이 브라우저 시험을 재실행하지는 않았다.

권고했던 오류 소유권 분리와 직접 반례가 모두 충족되어 이 P2를 닫는다. 새 P0/P1/P2를 확인하지 못했다.

## 기존 지적·신뢰 경계 재확인

### 동시 제출 및 재시도

app/api/address-refresh/route.ts는 이전788과 동일하다. 인증/Origin/body 검사 뒤 submissionInFlight check/set 사이에 await가 없고, finally에서만 해제한다. busy 요청은409로 거절하며 기존 요청 ID는 재확인에 사용할 수 있다. 실제 고정 BFF 단위시험을 독립 임시 archive에서 다시 실행하여 **9 PASS / test30ms / 전체342ms / exit0**를 확인했다. 같은/다른 ID 경합, 기존 run replay, 오류 뒤 가드 해제 및 인증·CSRF·body 경계를 포함한다.

이 가드는 현재 살아 있는 단일 Node 프로세스의 동시 진입을 직렬화한다. 여러 replica/worker, 재시작 또는 upstream 타임아웃 뒤 지연 확정까지 전역 exactly-once로 보장하지 않는다. docs/reverse-address-refresh.md가 이 한계와 확장 전 공유 원자 경계 필요성을 명시하며, 현 소스 수용은 그 문서화한 범위다. 실제 현재 운영 프로세스 수는 본인 확인하지 않았다.

이전 W111-A-P2-01의 old202/202 launch2→new202/409 launch1 독립 반례는 r2 원문에 보존돼 있다. 이번 BFF는 바이트 동일하고 9시험을 다시 통과했으므로 제한 범위 closure를 유지한다.

### 이전 실행 상태의 진실성

컴포넌트 :47–56은 FAILURE/CANCELED를 오류, SUCCESS를 완료, QUEUED replay를 기존 요청 확인, 신규 QUEUED를 새 예약으로 구분한다. 새 수정은 메시지/상태 분기 자체를 바꾸지 않고 저장 상태만 분리했다. 실행 기록 링크는 이전 run을 가리킬 수 있다는 중립 명칭을 유지한다. 이전 P2-02의 “실패한 replay를 새 예약 성공으로 표시”는 다시 도입되지 않았다.

조회 오류가 예약 시도만으로 성공 처리되거나 예약 오류가 조회 성공으로 해결됐다고 처리되는 경계를 분리했다. 조회 요약과 실행 결과는 서로 다른 수명이며 새 코드가 이를 구분한다. raw provider/GraphQL 오류를 추가 노출하지 않는다.

### 인증·CSRF·공개 계약

- BFF의 서명 세션 + durable session active 확인은 변경되지 않았다.
- exact configured Origin, JSON, UUID requestId 한 필드, bounded body를 유지한다.
- 브라우저의 임의 job/location/config가 아닌 서버 상수로만 예약한다.
- 관리자 상태 endpoint의 require_admin 및 서버 관리자 token 전달을 유지하고 provider 키는 UI로 반환하지 않는다.
- API/OpenAPI/TS DTO와 migration0021 선언은 788과 동일하다. 이번 EOF 제거는 DTO 타입 본문을 변경하지 않는다.
- 실제 로그인/쿠키/CSRF 운영 검증은 실행하지 않았다. BFF9시험의 session은 대역이다.

### 주소 원천·최종 사용·Common 연결

core/Weather API/Dagster/설정·Compose/Commonc067 pin은 788과 동일하다. 활성 weather_locations를 source로 삼고 별도 entries/control에 파생 결과를 기록하며 원천 좌표/주소/region_code/nx/ny/metadata/weather fact를 대체하지 않는다. VWorld 우선/Kakao 보조, 달력6개월, 수동 cycle/오류 backoff/eligible cursor/필수 pending_failures callback 연결은 이전 전체 검토 범위를 보존한다.

최종 주소 표시는 Admin/API의 최근 최대50개 파생 결과다. 일반 locations/지도 주소를 자동 치환한 구현으로 과장하지 않는다. UI의 stale/error·마지막 확인·다음 확인 날짜 및 제공자 표시도 그대로다. 원천보존·별도 표시라는 manifest와 guide 계약에 부합한다. 새 주기/보조 provider/격자 로직을 소비자에서 재구현하지 않았다.

### 반응형·접근성·Daemon OFF

named section/표 caption·header/오류 alert·상태 안내/명확한 버튼 텍스트를 유지한다. 작은 화면용 가로 스크롤 영역은 접근 가능한 이름과 tabIndex0이 있으며 CSS는 바이트 동일하다. 두 오류가 동시에 있으면 각각 alert가 표시되어 한 오류를 조용히 덮지 않는다. 실제 스크린리더·키보드·mobile browser 수용은 본인 NOT_RUN이다.

sensor 등록이 Daemon을 start/restore하지 않는다. OFF이면 예약은 대기한다는 설명을 유지하고, 본 검토도 Daemon 시작·예약 운영 실행·provider 호출을 수행하지 않았다.

## 역사 증거·문서·검증 결과

자기 원문 아카이브를 실제 파일과 바이트 비교했다:
- r1 archive SHA256 c8c1b75ba6fc9ffe3aee93d82c7afd42fbc380024da2ab344a6266a7b3a566e6.
- r2 archive SHA256 54d71d21262741724532bcc1e5a8bff46eadb8087b948174e37a88d1504cd0c6.

둘 다 원본과 정확히 같다. r1 두 P2 및 r2 새 P2로 REQUEST_CHANGES였던 이력은 지워지지 않았고, 새 수정 판정과 분리돼 있다. r2 시작·종료 및 요청 원문도 그대로다. 통합 문서는 과거 B의 SOURCE_PASS가 당시 A의 OPEN P2를 무효화했다고 쓰지 않는다. “당시 판정/현재 post-fix 대기”의 시점이 구분돼 있다.

docs의 root UI26·4 FAIL→4 PASS·CI 성공은 의뢰 증거이며 본인 새 검증 수치가 아니다. r1/r2 전용 실행 환경 실패와 Transport Docker 시험 fixture 문제 이력도 별도 보존한다. Transport 정식 소비자 gate는 이 보고서에서 계수하지 않는다.

| 본인 실행/정적 확인 | 결과 |
| --- | --- |
| Manifest SHA 및 base/head | 일치 |
| 이전→현재 제품 delta 집합 | TSX/E2E/EOF 3경로만 |
| 지연 GET old/new 실제 함수 대역 | 4 RED→4 GREEN, 총8실행 |
| immutable BFF route.test.ts | 9 PASS, exit0 |
| own r1/r2 원문 Git archive | byte exact |
| 새 정산/가이드/journal/resume 상대 링크 | 누락0 |
| base→head git diff --check | exit0 |
| 시작·종료 repository status | clean |

주요 source SHA256:
- frontend/components/address-refresh.tsx: 3e8de13e819a18eab38765c59743d5e0896eab52ba04744e0289e4279caca638.
- frontend/app/api/address-refresh/route.ts: 3642642df5cdadc1a90b926f459a5dc79c8ef8fc9409952a1198f55137d40f62.

## 판정 한계

SOURCE_PASS는 이 Weather immutable 소스 및 격리 fixture 범위다. root가 확인한 CI39090950401 Python658/frontend211/lint/type/build/OpenAPI SUCCESS와 양 앱 실브라우저 UI26은 본인 재실행으로 합산하지 않는다. 실제 PG/DDL·native Dagster event/run·provider quota/키·배포 이미지·운영 UI·CPU/RSS·여러 replica·브라우저 접근성/반응형은 별도 수용 영역이다. Common c067의 앞선 독립 검토 및 root 격리 DB 시험을 새 소비자 운영 증거로 확대하지 않는다.

본 보고서에서 신규 OPEN P0/P1/P2는 없다. 이는 모든 가능한 결함 부재의 증명이 아니라 위 렌즈와 검증 범위에서 재현 가능한 차단 결함을 발견하지 못했다는 뜻이다. 제품/기존 보고서는 수정하지 않았다.

## 전달받은 이번 요청 원문

```text
Weather111 formal FULL post-fix r3 독립 재검토를 요청합니다. 동일 manifest /home/digitie/dev/reverse-address-weather-review-manifest-20261018-r3.json SHA256 e788e6bed6b7b13a45c7c4bfcb04d11d8c6c797ec1d2b15c6fe55cdabb747091. exact base28f731e6dcb12d5f4cde2ad86fd25acbf3da2eac -> headba40131d224fdb34dfd125c27e15e4a2a1f6df17, 이전788175. r2 양쪽 원문 확정됐고 기존 결과를 보고서에 보존했습니다. 본인 W111-A-P2-03을 loadError/launchError 분리로 수정했고 두 앱 desktop/mobile 브라우저에서 실제 4 FAIL→4 PASS, 전체 UI26 PASS(각13)를 확인했습니다. EOF 빈 줄도 정리해 base..HEAD diff-check PASS. 최종 CI39090950401 Python658/frontend211/lint/type/build/OpenAPI 모두 SUCCESS입니다. 통과 목적이 아니라 결함 찾기가 목적입니다. Frontend/live UI·비동기 상태·auth/CSRF·재시도/단일프로세스 한계와 전체 base→head delta/역사 evidence를 다시 확인하고 본인 P2 closure를 독립 재현하세요. 읽기 전용 Git object/소유 fixture만; 소스/index/branch 수정·타인 변경 되돌림 금지. 운영/SSH/Docker/실provider/키/Daemon 금지. 상대 새 결과 비공개. /home/digitie/dev/reverse-address-weather-review-james-r3.md에 원문·SHA·판정/P0-P2/파일:행/실패시나리오·한계 및 이 전달 요청 원문을 보존하세요. Transport formal gate는 별도입니다.
```

종료 UTC: 2026-10-18T02:48:27.686276+00:00
