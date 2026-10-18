# Weather111 formal FULL post-fix r3 — Popper 독립 리뷰

판정: **PASS_WEATHER_SOURCE_FIXTURE_ONLY**. 열린 P0=0 / P1=0 / P2=0.
W111-B-P2-01/02의 closure는 유지되고, 전달받은 W111-A-P2-03도 본인 독립 반례에서 CLOSED다. 운영·배포·Transport formal gate의 PASS가 아니다.

## 대상·불변성·격리

- Weather base: 28f731e6dcb12d5f4cde2ad86fd25acbf3da2eac
- exact head: ba40131d224fdb34dfd125c27e15e4a2a1f6df17
- 이전 본인 head: 78817516561968868bb7a77c6cb17c6b90cefee5
- Common pin: c067a7f6a11b106931a73cd7465503d90e54d976
- Manifest: /home/digitie/dev/reverse-address-weather-review-manifest-20261018-r3.json
- Manifest SHA256: e788e6bed6b7b13a45c7c4bfcb04d11d8c6c797ec1d2b15c6fe55cdabb747091
- 이전 본인 r2 보고서: /home/digitie/dev/reverse-address-weather-review-popper-r2.md
- r2 보고서 SHA256: b086d1d6208a058b24ccd6876d39c60337bbc8a6d4816ad2b80be07119b9efec
- base→head 44파일. 이전 검토 이후 13파일 변경: 원문 보존·문서 10파일, component/E2E/EOF 정리 3파일. 기존 37파일 중 31파일이 byte 동일하다. 전체 base→head 검토를 계승하고 변경·연결 경로를 재확인했다.
- 변경 경로 순서대로 path+NUL+SHA256+LF를 연결한 44파일 목록 SHA256: 0ca0ac43df811a06772a3fa153047ba7a6e1e914669aa4e59950b7b13ee2ae21.
- 고정 Git object만 읽고 임시 파일은 소유 TemporaryDirectory에 생성·종료 시 제거했다. 소스/index/branch/WIP 수정·되돌림, 새 상대 리뷰 열람, 운영/SSH/Docker/실provider/키/Daemon 실행 0. 기존 AGENTS를 적용했다.
- 실제 시작 UTC는 수집하지 않았다. manifest created_utc는 작성자의 시각이며 내 시작 시각이 아니다. 보고서 종료 시각은 아래에 기록한다.

## W111-A-P2-03 독립 closure

위치: packages/kor-travel-weather-admin/frontend/components/address-refresh.tsx:13–14, 20–32, 34–61, 77–78.
원 지적의 발견자는 A다. 아래 결과는 원문이나 작성자 모형을 재인용한 판정이 아니라 정확한 이전/현재 component 함수의 본인 실행이다.

실패 조건: 수동 상태 새로고침 GET을 보류한 채 POST가 기존 FAILURE를 반환하거나 실패한다. 그 뒤 GET200이 도착하면 r2의 공용 setError("")가 실행 오류까지 지웠다.

검증 방법: TypeScript AST에서 정확 load의 useCallback initializer와 launch 함수 본문을 추출·transpile하고 Node VM에서 실행했다. 상태 setter/응답만 소유 fixture이며 실제 React scheduler/DOM/브라우저·HTTP 서버는 사용하지 않았다. GET은 promise로 보류하고 POST 종료 후 해제하여 순서를 강제했다.

| POST 결과 | 이전 r2, 뒤늦은 GET200 이후 | 현재 r3, 같은 순서 |
|---|---|---|
| replay FAILURE | 실행 오류 소실 RED | 실행 오류 유지 GREEN |
| replay CANCELED | 실행 오류 소실 RED | 실행 오류 유지 GREEN |
| HTTP503 + detail | 실행 오류 소실 RED | 실행 오류 유지 GREEN |
| fetch network exception | 실행 오류 소실 RED | 실행 오류 유지 GREEN |
| HTTP503 + invalid JSON | 실행 오류 소실 RED | 실행 오류 유지 GREEN |

이전 5 RED / 현재 5 GREEN을 같은 실제 함수 입력으로 확인했다. 또한:
- FAILURE/CANCELED/503과 늦은 GET503이 겹치면 launchError와 loadError가 함께 남는다.
- queued 성공 요청은 지난 launchError를 지우고, 뒤의 성공 GET은 loadError만 지운다.
- 오류 때 busy는 false로 복구된다.
- 실제 JSX에 loadError/launchError가 각각 독립 role=alert 분기로 표시된다.
총 **56 조건 PASS**, actual network 0 / browser 0. stderr 오류 0, Node exit0.

권고 closure: 현재 분리를 유지한다. 조회 성공을 예약 성공의 증거로 쓰지 않는다. 실제 브라우저 4 FAIL→4 PASS 및 UI26은 root가 제공한 별도 결과이며 본인 VM 조건에 합산하지 않았다.

## 이전 지적·backend/DB/ops 전체 연결

### W111-B-P2-01: CLOSED 유지

src/kortravelweather/address_refresh.py:51–57의 page/retries/pending_failures와 Dagster address_refresh.py:26–38의 has_work(automatic=...) 연결은 r2와 byte 동일하다. Common c067 pin도 바뀌지 않았다.

직전 본인 r2에서 실제 Weather 모델/Common SQL/정확 _pending AST를 SQLite로 연결해 AUTO=false 수동 마지막 오류→119초 pending=false→120초 pending=true→정상 재개 및 원천보존, 비활성 실패 cleanup, 실패 cursor 앞 신규 위치 처리를 검증했다(새 3회귀+기존1=4 PASS). 이 결과를 동일 source binding으로 계승하며 이번에 4개를 다시 실행한 것으로 쓰지 않는다. 실제 PostgreSQL 행락/동시 claim·Dagster sensor는 이번에도 미실행이다.

### W111-B-P2-02: CLOSED 유지, 단일 Node 범위

frontend/app/api/address-refresh/route.ts:10–12, 69–71, 99–100은 r2와 byte 동일하다. 동기 check/set, finally 해제 및 busy 요청의 조기409 경계가 유지된다. r2의 same/different ID 동시 POST·오류 후 가드 해제·replay·Origin/본문 거절 검증을 계승한다.

docs/reverse-address-refresh.md:56–59의 단일 프로세스 한계도 보존됐다. 복수 replica/재시작/상위 timeout 뒤 늦은 서버 확정까지 전역 exactly-once를 보장한 것이 아니다. 배포자가 현재 단일 Node 프로세스 계약을 확인해야 한다. 새 shared lock 구현이나 daemon 시작을 요구하지 않는다.

### migration·원천·schema·설정

- backend src/, API, Dagster, alembic/, pyproject/uv.lock, compose/example은 이전 검토 head와 byte 동일하다. 새로운 DB/schema·dependency·callback·provider 동작 변경이 없다.
- migration0021과 Common 두 테이블의 21열/복합 PK/index, 업그레이드·다운그레이드 원천 guard 보존, PostgreSQL dialect DDL compile 및 실제 Pydantic schema=OpenAPI 일치는 r2 직접 검증 결과를 계승한다. PostgreSQL 적용·운영 스키마는 직접 확인하지 않았다.
- source_record_key/fact/raw lineage, 원천 weather_locations/관리자 좌표·region_code 보존과 별도 파생 테이블 쓰기 경계가 유지된다.
- API 관리자 GET의 require_admin/threadpool, _run/_pending의 engine finally dispose, 활성/queued sensor skip, bounded batch/cache/pool 계약은 변경되지 않았다. 기존 Common c067 FULL 결과의 범위도 유지하며 새 CPU/RSS 실측 주장은 없다.
- secret 설정/Compose/timeout·batch 경계와 VWorld 직접 SDK 우선/Kakao fallback은 동일하다. 키·실제 provider는 접근하지 않았다.
- frontend/lib/api.ts 변경은 EOF 빈 줄 제거뿐이다. DTO 필드 의미는 동일하며 base..HEAD git diff --check를 직접 실행해 **exit0**을 확인했다.

## 최종 주소·문서·원문 보존

- 확인 주소는 Admin의 최근 최대50개 파생 결과에 address.full/provider/stale/확인일/다음 갱신일로 표시되고 API는 region도 반환한다. 일반 locations/지도 원천 필드 치환 또는 region 전용 UI를 구현했다고 판정하지 않는다. manifest와 문서의 명시된 경계가 유지된다.
- E2E는 1365/390 두 viewport에서 늦은 GET 반례를 추가했고 기존 조건/loopback 제한/고정 queue·CSRF 기준을 완화하지 않았다. root의 UI13×2와 CI39090950401 Python658/frontend211/lint/type/build/OpenAPI SUCCESS는 전달된 증거다. 본인 재실행 수치가 아니다.
- 새 리뷰 아카이브는 r1/r2의 REQUEST_CHANGES와 당시 P2, 이후 수정 사실을 구분한다. 아카이브의 기존 B r1 SHA c7457067…와 B r2 SHA b086d1d6…가 원본과 동일한 것을 확인했다. 여섯 이전 manifest/report의 SHA를 읽기 전용으로 계산했다. 현재 r3 상대 결과는 열람하지 않았다.
- docs의 역사적 Weather657+추가 GraphQL 기록과 최종658은 시점별 증거로 읽었다. UI26/provider fixture와 운영 UI·migration·실제 provider·배포 NOT_RUN 경계가 명시되어 있다. 원본 실패 이력을 최종 성공으로 덮지 않았다. Daemon OFF 정책은 그대로다.

## 한계와 최종 판정

이번 새 직접 실행은 정확 component의 56조건 Node VM, immutable source/아카이브 hash 및 diff-check다. 직전 r2의 SQLite/DTO/migration/BFF 검증을 source 동일성으로 계승했다. 전체658/211 suite, 실제 React/브라우저, 실제 PostgreSQL·Dagster 실행/중단, provider quota, native image/운영 배포·DB pool/RSS/CPU는 직접 검증하지 않았다. root 전달 결과와 명확히 구별한다.

현재 검토 범위에서 새 P0/P1/P2 또는 closure를 뒤집는 회귀는 발견하지 못했다. **PASS_WEATHER_SOURCE_FIXTURE_ONLY / OPEN0**. 운영 수용과 Transport formal gate는 별도다.

## 주요 새 source SHA256

- component: 3e8de13e819a18eab38765c59743d5e0896eab52ba04744e0289e4279caca638
- E2E spec: 6fd9e0e8a44e62a60e2441d69de877014833e1422d5e080a69de7a89eb558acd
- TS API: e321d6f12b7b03405bda43782fd30a37f11c4211838101db5488427d6569dd03

## 실제 전달받은 이번 요청 원문

parent NEW_TASK payload를 그대로 보존한다. 메시지 자체의 UTC timestamp는 제공되지 않았다.

```text
Weather111 formal FULL post-fix r3 독립 재검토를 요청합니다. 동일 manifest /home/digitie/dev/reverse-address-weather-review-manifest-20261018-r3.json SHA256 e788e6bed6b7b13a45c7c4bfcb04d11d8c6c797ec1d2b15c6fe55cdabb747091. exact base28f731e6dcb12d5f4cde2ad86fd25acbf3da2eac -> headba40131d224fdb34dfd125c27e15e4a2a1f6df17, 이전788175. r2 양쪽 원문 확정됐고 A 새 W111-A-P2-03(늦은 GET200이 POST 실패를 지움)을 loadError/launchError 분리로 수정했습니다. 두 앱 desktop/mobile 실제 브라우저 4 FAIL→4 PASS, 전체 UI26 PASS(각13), EOF 정리 base..HEAD diff-check PASS. 최종 CI39090950401 Python658/frontend211/lint/type/build/OpenAPI 모두 SUCCESS입니다. 통과 목적이 아니라 결함 찾기가 목적입니다. Backend/DB/ops·pin/callback·원천보존·migration/DTO·재시도와 단일프로세스 한계, 전체 base→head delta/원문 보존을 다시 확인하고 새로운 회귀도 찾으세요. 읽기 전용 Git object/소유 fixture만; 소스/index/branch 수정·타인 변경 되돌림 금지. 운영/SSH/Docker/실provider/키/Daemon 금지. 상대 새 결과 비공개. /home/digitie/dev/reverse-address-weather-review-popper-r3.md에 원문·SHA·판정/P0-P2/파일:행/실패시나리오·한계 및 이 전달 요청 원문을 보존하세요. Transport formal gate는 별도입니다.
```

보고서 종료 UTC: 2026-10-18T02:48:35.669302+00:00
