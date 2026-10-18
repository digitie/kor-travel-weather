# Weather111 formal FULL post-fix r2 — Popper 독립 리뷰

판정: **PASS_WEATHER_SOURCE_FIXTURE_ONLY**. 현재 exact head에서 열린 P0=0, P1=0, P2=0. 기존 W111-B-P2-01/02는 아래 검증 범위에서 CLOSED다. 배포 승인이나 운영 수용 판정은 포함하지 않는다.

## 불변 대상과 격리

- Weather base: 28f731e6dcb12d5f4cde2ad86fd25acbf3da2eac
- Weather head: 78817516561968868bb7a77c6cb17c6b90cefee5
- 이전 본인 제품: c52e08b5a5d7401fc600ddd4a378f327f6395f60
- Common pin: c067a7f6a11b106931a73cd7465503d90e54d976
- Manifest: /home/digitie/dev/reverse-address-weather-review-manifest-20261018-r2.json
- Manifest SHA256: bfc6246e4361a371761ac28810e7aced666f6676e664fd8ce5afcad51bd1d3a0
- base→head 전체 37파일, 이전 본인 검토 head→현재 12파일 변경. 나머지 25파일의 immutable binding을 재확인했다. 이전 FULL 검토를 계승하고 변경 전체와 연결 제품·문서·설정을 다시 읽었다.
- git diff --name-only의 순서대로 path+NUL+SHA256+LF를 연결한 37파일 목록 SHA256: 9b2d42c03dd134368030862457bd8534da6585420847955b08b07fa297adeb6f
- AGENTS.md 적용. immutable git show로 읽고 임시 디렉터리에 필요한 파일만 추출했다. 소스/index/branch/WIP 변경, 상대 새 리뷰 열람, 실제 provider/키/운영/SSH/Docker/Daemon 접근 0. 임시 fixture는 종료 시 제거했다.
- 시작 UTC는 수집하지 않았다. 아래 종료 UTC는 이 보고서 저장 시각이다.

이전 본인 Weather r1 보고서 SHA256: c745706727e97ac2f46f15d4c2217a39fe33af03957c0ef7c6cc9fb6f8c095da.
이전 본인 Common r4 보고서 SHA256: e49c27d070cc6f8dd30a87ea19299534da25f819d1cdd59db7d089223138a6b3.
두 원문은 수정하지 않았다.

## 기존 지적 closure

### W111-B-P2-01 — AUTO=false 수동 실패 재개: CLOSED

위치: src/kortravelweather/address_refresh.py:51–57; packages/kor-travel-weather-dagster/src/kortravelweather_dagster/address_refresh.py:26–38; Common reverse_address.py:519–534, 708–719.

기존 실패 조건은 수동 마지막 행이 provider 오류로 끝나고 자동 갱신이 꺼진 경우였다. 이전 제품은 수동 cycle을 비워 재시도가 영구 정지했다. 현재 소비자는 source.page/source.retries와 필수 pending_failures를 고정 Common c067에 전달한다. Common은 활성 실패가 남아 있는 수동 cycle을 보존하고 backoff 대기만 있을 때 has_work를 false로 돌려 빈 sensor run을 억제한다.

독립 재현은 실제 WeatherLocationRow/WeatherSettings/address_source/run_address_refresh와 실제 Common c067 SQL을 소유 SQLite에서 연결했다. 정확 _pending 함수 AST만 추출해 SDK import 없이 실행했다:
1. AUTO=false, force=true, 마지막 z 오류 → remaining=1; 119초까지 pending=false; 120초 pending=true; 정상 resolver로 force=false 재개 → 1회 요청/remaining=0/pending=false.
2. 오류 후 z 비활성화 → cleanup pending=true; 다음 배치 remaining=0, 이후 pending=false. 사라진 원천이 수동 cycle을 영구 유지하지 않는다.
3. z backoff 동안 앞쪽 신규 a 추가 → pending=true/다음 배치 a 처리; 다시 backoff pending=false; z due 시 pending=true.
기존 원천보존 시험도 함께 실행해 총 **4 passed**다. 원천 name/좌표/metadata가 유지되고 별도 파생 address.full만 새 결과가 되는 것을 확인했다.

PostgreSQL 행락/동시 claim 및 실제 Dagster sensor 실행을 이번에 수행한 결과로 해석하지 않는다. 해당 Common DB fencing은 이전 독립 Common r4 범위를 계승한다.

### W111-B-P2-02 — 동시 POST 중복 예약: CLOSED, 단일 프로세스 범위

위치: frontend/app/api/address-refresh/route.ts:10–12, 69–71, 99–100 (frontend는 packages/kor-travel-weather-admin/frontend).

현재 guard check/set 사이에 await가 없고 finally가 이전 실행 재조회·active 거절·launch 성공/오류 모두에서 해제된다. busy 요청의 조기 409는 다른 요청의 guard를 해제하지 않는다.

실제 route.ts와 origin.ts/dagster-scope.ts를 TypeScript transpile하여 새 Node VM에서 실행했다. 세션만 명시적 fixture로 대체했고 fetch는 소유 응답 함수라 네트워크 호출 0이다. 첫 previous 조회를 promise로 막은 상태에서:
- same requestId와 different requestId 각각 두 번째/세 번째 POST는 409, previous 조회는 여전히 1, launch는 0.
- 첫 요청 해제 후 launch는 정확 1, 다음 요청은 다시 정상 진입한다.
- previous/active/launch 단계 각각 throw 및 GraphQL 오류 typename을 주면 502 뒤 다음 요청 202; 비밀 fixture 예외 문자열은 응답에 없다.
- prior SUCCESS/FAILURE/CANCELED/QUEUED 각각 1회 조회만으로 replayed=true와 원래 status를 반환한다.
- 잘못된 Origin/추가 job 필드/본문 초과는 upstream 호출 전에 거절한다.

위 route 및 아래 component 검증을 합쳐 **45 조건 PASS**. 다중 replica/프로세스 재시작/상위 launch 타임아웃의 서버 측 늦은 확정까지 원자적 exactly-once로 입증한 것은 아니다. 문서 docs/reverse-address-refresh.md:56–59와 route 주석이 단일 Node 범위를 명시한다. 확장 전 공유 원자적 제출 경계가 필요하다. 불확실한 결과는 현재 UI도 완료로 표시하지 않고 같은 ID 재조회 경로를 유지한다.

## 전체 연결 검토와 직접 검증

- migration0021: 소유 SQLite에서 실제 Alembic upgrade 후 Common metadata와 21열 이름·nullable, 두 복합 PK 및 index 집합을 비교했다. 기존 별도 guard 테이블/행 보존, downgrade 후 새 두 테이블만 제거되는 것을 확인했다. PostgreSQL dialect DDL compile도 성공했지만 실제 PostgreSQL 적용은 수행하지 않았다.
- API schema: routers/weather.py:1286 이후 AddressRefreshSummary 실제 Pydantic class AST의 model_json_schema가 저장 OpenAPI component와 정확히 일치한다. 기존 DTO/locations 응답 치환은 없다. 관리자 GET은 require_admin 및 threadpool 경계를 유지한다.
- repository.py:119 이후 새 파생 테이블 metadata 등록 외 기존 fact/upsert/lineage 변경이 없다. 주소 엔진의 쓰기는 별도 두 테이블에 한정된다. 좌표 hash가 달라진 파생 결과를 정상 최신 주소로 게시하지 않는 Common projection 경계를 계승한다.
- settings/compose/example: provider SecretStr와 auto/batch 1..500/timeout >0..10 설정이 일치한다. API와 Dagster 환경에 같은 이름이 전달된다. 소스나 문서가 daemon을 자동 시작하지 않는다.
- pyproject 두 소비 경로, Dagster pyproject, uv.lock의 Common pin은 모두 c067이다. reverse-address extra와 잠긴 python-vworld-api 의존성이 연결된다. PostgreSQL engine은 소비자 _run/_pending의 finally에서 dispose된다. _pending은 provider를 호출하지 않는다.
- definitions.py: job/sensor 등록, 고정 code location/tag/job/force config를 검토했다. Common sensor는 같은 위치의 active/queued run 존재 시 skip하고 pending이 false면 run을 만들지 않는다. 시간·batch·cache·pool 한계는 Common c067 제품 범위이며 실측 CPU/RSS 개선을 주장하지 않는다.
- frontend component 실제 launch 함수 본문도 AST로 추출 실행했다. FAILURE/CANCELED는 error가 남고 queued notice/load가 없으며 새 수동 요청이 가능해진다. SUCCESS는 완료 문구, QUEUED replay는 기존 실행 문구, 새 QUEUED는 예약 문구로 분리한다. 실제 DOM/React scheduler/브라우저 검증은 수행하지 않았다.
- 최종 주소의 사용/표시: Admin의 최근 최대 50개 파생 결과에 address.full/provider/stale/날짜를 표시하고 API에는 region도 포함한다. 일반 locations/지도 응답의 원천 주소·관리자 좌표·region_code/fact는 대체하지 않는다. 이 한계가 현재 manifest와 문서에 명시되어 있다. region 전용 UI 표시나 일반 지도 주소 치환이 구현되었다고 판정하지 않는다.
- E2E spec/설정의 loopback 제한, UI 실패/키 없음/503 fixture, desktop/mobile 및 replay 상태 기준을 읽었다. 운영 API 검증으로 승격하지 않았다.

## 미실행·비차단 관찰

- root의 CI39090100738 Python658/frontend211/lint/type/build/OpenAPI SUCCESS와 loopback 실브라우저 22개는 전달받은 증거다. 본인이 전체 suite/브라우저를 재실행한 수치에 합산하지 않았다. Transport formal gate는 이 보고서 범위 밖이다.
- 실제 PostgreSQL migration/동시 transaction, 실제 Dagster queued 실행/취소, provider quota/키, native image·운영 compose·배포/UI, 운영 DB pool/RSS/CPU는 검증하지 않았다.
- Weather 기본 conftest의 기본 PG/TRUNCATE 진입을 막기 위해 --noconftest, -c /dev/null, PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 및 env 비상속/-I/-B를 사용했다. 새 SQLite 3회귀+기존1시험은 0.75초/4PASS였다. pytest cache가 /dev 하위 생성을 시도하여 PermissionError warning 1개가 있었고 원 저장소 파일 생성은 없었다.
- git diff --check base→head는 exit 2: frontend/lib/api.ts:376의 EOF 빈 줄 1개. 이전 검토부터 동일하며 기능/보안 차단 지적으로 올리지 않았다. 이를 diff-check PASS로 쓰지 않는다.
- 단일 프로세스 guard는 admission 직렬화다. unknown launch 뒤 늦은 upstream 확정/여러 worker/replica 간 전역 exactly-once 증명은 없으며 배포 시 현재 단일 프로세스 계약을 별도 확인해야 한다.

## 주요 소스 SHA256

| 경로 | SHA256 |
|---|---|
| src/kortravelweather/address_refresh.py | 5640f95e0d16471715e9570ee90a0fce2505dcb81a341f47785b7515fdb838f7 |
| frontend/app/api/address-refresh/route.ts | 3642642df5cdadc1a90b926f459a5dc79c8ef8fc9409952a1198f55137d40f62 |
| frontend/components/address-refresh.tsx | e56f42dcdb391ada1390b0092cae00e1c762a78e62c0c664ac5cfc19f49136d3 |
| alembic/versions/0021_reverse_addresses.py | 5870ae21c23059c8e2631111bc31811cf0c14d394bdecd90ef1d35ce7b07cae0 |
| Dagster address_refresh.py | b1fd72af87e7a057ae0f774a088633d94ae8bd59d66fa1362520608ae4576237 |
| docs/reverse-address-refresh.md | 8059b1c491e06f5e55dbfa08c3fb542d78add76bf954e8440f5d7523512bab0d |

## 실제 전달받은 리뷰 요청 원문

아래는 이번 대화에 남아 있는 parent의 NEW_TASK payload 원문이다. 메시지 자체의 UTC timestamp는 제공되지 않았다.

```text
Weather111 formal FULL post-fix r2 독립 적대 리뷰를 진행하세요. 동일 manifest /home/digitie/dev/reverse-address-weather-review-manifest-20261018-r2.json SHA256 bfc6246e4361a371761ac28810e7aced666f6676e664fd8ce5afcad51bd1d3a0. Weather base28f731e6dcb12d5f4cde2ad86fd25acbf3da2eac -> exact head78817516561968868bb7a77c6cb17c6b90cefee5, 이전본인c52e08b5. 최종 CI39090100738 Python658 및 frontend211/lint/type/build/OpenAPI 모두 SUCCESS. Commonc067 고정 및 새 callback, 단일 Node 프로세스 BFF 동시 진입 가드, 이전 실행의 실패/취소/성공 replay 상태표시를 보강했습니다. UI11앱별(총22) fixture provider loopback 실브라우저 통과. 통과시키는 것이 아니라 결함을 찾는 목적이며 애매하면 재현을 시도하세요. 본인 W111-B-P2-01 AUTO=false 수동실패 재개/W111-B-P2-02 동시POST 지적 closure와 backend/PostgreSQL/ops 렌즈(마이그레이션·원천보존·스키마/DTO·common pin/callback·sensor·설정·실패 신뢰성), 전체 base→head 회귀를 검토하세요. 복수 replica 전역 exactly-once는 미보장·현재 단일 프로세스라는 문서 한계도 검토하세요. Transport는 context만, formal gate 별도. 읽기 전용 Git object/소유 fixture만 사용; 다른 작업자가 있으니 소스/index/branch 변경·되돌리기 금지. 운영/SSH/실provider/키/Daemon/Docker 조작 금지. 상대 새 결과 비공개. /home/digitie/dev/reverse-address-weather-review-popper-r2.md에 원문·SHA·P0/P1/P2·파일:행·실패시나리오·판정/한계 및 이 전달요청 원문을 보존해 주세요.
```

보고서 종료 UTC: 2026-10-18T02:38:39.544325+00:00
