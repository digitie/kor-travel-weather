# Weather111 소비자 정식 독립 FULL 적대 리뷰 — Popper r1

판정: **CONDITIONAL / 수정·재검증 필요**. OPEN **P0 0 / P1 0 / P2 2**. 아래 두 반례를 해소하거나 소유자가 수용 계약을 명시적으로 조정하기 전에는 Weather111 무조건 PASS로 집계하지 않는다. Common r2 소스 PASS와 Weather 소비자 통합 경계의 판정은 별개다.

## 불변 범위와 격리

- 리뷰어: /root/review_finish_b. Backend/PostgreSQL·migration·원천 보존·pool·중단/재시도·배포 계약을 우선하며 최종 주소 표시 경로까지 검토했다. 다른 리뷰어의 새 결과·모형·보고서는 보지 않았다.
- 보고서 고정: 2026-10-18T01:59:52.227072+00:00. 정밀 시작 시각은 별도 고정하지 않았다.
- Manifest: /home/digitie/dev/reverse-address-weather-review-manifest-20261018-r1.json
- Manifest SHA256: **2c1d031e4aed34a14a88be9a29124963c72b44fc6a8a1c784370aab90d60b469**
- Weather base **28f731e6dcb12d5f4cde2ad86fd25acbf3da2eac** → head **c52e08b5a5d7401fc600ddd4a378f327f6395f60**. 변경 37파일의 전체 delta와 연결된 기존 소스를 읽었다.
- Common 제품은 **0f768a1974a65bfade4c0fa3c79b897c9663e1a6** 고정이다. 기존 본인 Common r2 closure는 재사용했으나 아래 소비자 반례는 이번에 독립 실행했다.
- Weather AGENTS.md를 읽었다. 모든 소스·테스트는 git show exact head:path를 사용했으며 원본 WIP·미커밋 타입 보정을 읽거나 복제하지 않았다. source/index/branch 변경0.
- Transport dd66363은 문맥이며 정식 PASS 범위가 아니다. root의 Weather657/schema/최종 계약/로컬 UI18·CI 성공은 본인 실행 수치와 분리했다.
- 운영 SSH/Docker/DB, 실제 provider/key/SDK/브라우저, private/env/auth/proc 접근0. Daemon start/restore0. OFF 정책을 바꾸지 않았으며 현재 운영 상태를 재관측한 것으로 주장하지 않는다.
- 소유 TemporaryDirectory에만 고정 소스·SQLite·JS VM fixture를 만들고 종료 시 제거했다. 영구 작성물은 이 보고서 하나다.

## 지적

### W111-B-P2-01 — 자동 갱신 OFF에서 시작한 수동 배치의 실패가 재시도되지 않는다

- 소비자 위치: packages/kor-travel-weather-dagster/src/kortravelweather_dagster/address_refresh.py:25–37, 특히35행.
- 연결 위치: Common0f reverse_address.py:220–227,448–483,660–668. 문서 계약: docs/reverse-address-refresh.md:18–19.
- 조건: ADDRESS_REFRESH_AUTO_ENABLED=false, 키 설정, 활성 위치1개에 force 수동 실행, provider 실패. cursor가 끝에 도달하므로 Common은 partial_failure/failed1/remaining0을 반환하면서 control.cursor=''·force_refresh=false로 종료한다.
- 독립 재현: 고정 WeatherLocationRow/address_source와 Common0f를 소유 파일 SQLite에 연결했다. 실패 resolver만 주입하고 force 실행 시각을1시간 전으로 두어 backoff가 이미 만료된 상태로 만들었다. 소비자의 정확한 _pending 함수 AST를 같은 소유 engine/settings에 연결했다. Dagster·실 provider·운영 호출0.
- 결과: run은 partial_failure, next_retry_at은 현재보다 과거, 같은 DB의 has_work(automatic=True)=true였다. 하지만 실제 소비자 _pending()은 automatic=false를 전달하므로 cursor/force가 사라진 상태에서 false를 반환했다. 다음 sensor tick은 run을 만들지 않아 사람이 다시 force하거나 자동 갱신을 켜기 전까지 재시도가 멈춘다.
- 영향: 「실패는 재시도」「자동 갱신을 꺼도 이미 시작한 수동 배치는 계속 처리」한다는 계약을 충족하지 않는다. 미처리 cursor가 남은 부분 배치는 재개되지만, 끝까지 읽은 실패 배치는 수동 cycle의 재개 의무가 사라진다. 원천/좌표/fact 파괴를 재현한 것은 아니며 실패 복구의 P2다.
- 권고: 새로운 달력 자동 갱신과 기존 수동 cycle의 미해결 실패 재개를 구분한다. 수동 실패가 남으면 backoff를 지키는 영속 재개 상태를 유지하고 성공 대상은 중복 호출하지 않아야 한다. automatic=False를 무조건 True로 바꾸는 수정은 자동 갱신 OFF 의도를 깨므로 부적절하다.
- 회귀: AUTO=false/force=true/1실패→backoff 전 추가 호출0→만료 뒤 _pending=true→실패 대상만 회복→해결 뒤 _pending=false. 성공 대상·미처리 cursor·일반6개월 due를 함께 두어 구별하고, 대기 중 불필요한 run 및 삭제/비활성 대상의 영구 pending도 확인한다.

### W111-B-P2-02 — 같은 requestId의 동시 POST가 서로 다른 force run2개를 등록한다

- 위치: packages/kor-travel-weather-admin/frontend/app/api/address-refresh/route.ts:67–93. previous 조회67–72, active 조회73–79, launch80–93 사이에 원자적인 예약이 없다.
- 조건: 인증된 사용자가 같은 UUID requestId로 동시 POST한다. 두 요청이 previous=[] 및 active=[]를 모두 읽은 뒤 launch한다. 통신 결과 불명확에 따른 재전송이나 복수 탭에서도 가능한 check→act 경합이다.
- 독립 재현: 고정 route.ts·dagster-scope.ts·origin.ts를 TypeScript transpile하여 JS VM에서 실행했다. NextResponse/유효 세션만 대역으로 바꾸고 fetch는 고정 GraphQL3문에만 응답하게 했다. previous와 active 각각에서 두 요청의 read를 barrier로 맞추고 launch마다 서로 다른 runId를 반환했다. 브라우저·통신·SDK·운영0.
- 결과: **동일 requestId에 launchRun2회, HTTP202/202, 서로 다른 QUEUED runId2개**. 순차 replay 양성 대조는 previous1회만으로 prior run을 돌려줬다. Origin 불일치403·여분 job입력422는 fetch0회로 거절했다. 임의 job/다른 location 권한 침해라는 지적은 아니다.
- 영향: Daemon OFF에서도 중복 run이 큐에 남는다. DB lease는 동시 worker 저장을 격리하지만, 두 run이 나중에 순차 실행되면 첫 cycle 완료 뒤 두 번째 force는 새 cycle이 되어 같은 수동 요구의 중복 갱신·quota 소비가 가능하다. 기존 순차 replay 테스트나 「중복 실행 차단」 검증만으로 병렬 재전송을 보장할 수 없다.
- 계약의 한계: Common 가이드가 UUID 조회로 중복을 「줄인다」고 하며 strict exactly-once라고 쓰지 않은 점은 확인했다. 그래도 manifest의 request replay/같은 요구의 예약 복구·중복 차단을 일반 병렬 재전송까지 PASS로 집계할 수는 없다. best-effort만 의도한다면 정확한 수용 범위와 큐 중복의 잔여 위험을 명시해야 한다.
- 권고: 소비자 영속 requestId unique claim 등 launch 전에 원자적인 소유권을 확보하거나 원자적 enqueue/idempotency 경계에 연결한다. launch UNKNOWN을 미등록으로 간주해 맹목 재송신하지 않는다. 단일 Node 프로세스 mutex만으로 다중 worker/재시작을 보장할 수 없고, 실제 Dagster schema에 없는 ExecutionMetadata.runId를 추가해서도 안 된다.
- 회귀: 동시 same-ID2요청에서 mutation1회·같은 run으로 reconcile, 다른 ID의 active 경합, launch 결과 UNKNOWN 재조회, 순차 replay, Origin·고정 job·인증 음성을 함께 검증한다.

## Backend·migration·원천 보존 검사

- 원본 test_address_refresh.py를 불변 객체에서 가져와 root conftest 없이 실행해 **1 PASS**. 활성 위치만 처리하고 region_code·metadata.admin_note·KMA nx/ny·좌표를 저장 전후 유지했다.
- Common/Weather core .py를 소유 root에 고정하여 -I -B, 소유 HOME/TMPDIR/cwd, PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, development 프로필과 _env_file=None을 사용했다. 기존 conftest의 기본15432 PG/TRUNCATE는 실행하지 않았다.
- 소유 SQLite에서 실제 Alembic Operations로0021 upgrade를 실행했다. entries15열/control6열의 열 집합·nullable·PK·index 이름이 Common0f metadata와 일치했다. 기존 source_guard행은 불변이고 downgrade는 새2테이블만 제거했다. PostgreSQL DDL은 dialect compile하여 timestamptz/PK를 확인했으며 실제 PG 실행은 아니다.
- down_revision은 기존0020_short_forecast_retention과 일치한다. 역사적 migration은 실행 시 Common helper를 호출하지 않고 열을 고정한다. 새 테이블은 원 fact에 대한 FK/갱신을 추가하지 않는다.
- 실제 AddressRefreshSummary class AST를 Pydantic으로 실행한 schema가 커밋 OpenAPI의 대응 schema와 완전히 일치했다. items는 범용 dict 계약이므로 개별 필드 전체의 엄격한 DTO 타입까지 이 비교가 보장하지는 않는다.
- Dagster _run/_pending의 정확한 AST를 소유 engine fake에 연결했다. 성공/예외에서 dispose1회, 키 미설정 pending의 engine 생성0, auto flag의 정확한 source 전달을 확인했다. P2-01은 flag 전달 오류가 아니라 영속 cycle 종료와의 연결 문제다.
- 작업/조회 전용 engine은 짧게 생성·dispose하고 Common pool_size1/max_overflow0 및 connect/statement/lock timeout을 사용한다. API 상태 조회는 기존 repository engine을 재사용한다. 기존 pool10+overflow10을 이 PR이 늘리는 변경은 없다.
- 상태 조회는 DB 집계와 최대50표본이며 provider를 부르지 않는다. address_source는 활성 weather_locations의 key/좌표/name만 SELECT하고 Common은 파생2테이블만 쓴다.
- core/Dagster의 Common0f reverse-address extras와 VWorld 고정 git이 pyproject/lock에서 일치한다. Compose는 API/Dagster anchor에5설정·키를 전달하고 기존 health/resource/storage 정책을 바꾸지 않는다. 실제 설치·이미지·운영 migration은 NOT_RUN이다.

## 최종 주소의 사용·표시

실제 경로는 VWorld/Kakao→Common 파생 entries→GET /v1/admin/address-refresh→인증 BFF→Admin Dagster의 AddressRefresh 패널이다.

- 최근 최대50개의 address.full·status/provider·실패/stale 주의·과거 정상 resolved_at·다음 갱신일을 표시한다. 수동 응답은 완료가 아닌 「예약」으로 알리고, Daemon 중지 중에는 대기한다고 안내하며 run link를 제공한다.
- 기존 원천 주소·관리자 좌표·source region_code/fact를 덮지 않는다. 원 Location DTO나 map marker/일반 주소 표시에 파생 주소를 자동 채택하는 변경은 없다. 이 PR을 「모든 사용처 주소가 바뀜」으로 판정하지 않는다.
- 행정구역 구조는 저장·상태 API에 포함되지만 패널에 별도 region열은 없다. full주소의 지역 문자열과 행정코드/구조별 표시를 구분한다.
- 정상 무결과는 주소 없음, 실패는 마지막 정상 주소에 재확인 필요를 붙이는 보수적 표시다. resolved_at은 마지막 정상 주소 시각이고 최신 시도 checked_at 자체를 화면에 보여주는 것은 아니다.
- 「파생 결과를 Admin에서 확인하고 원천은 보존」한다는 문서 범위에는 맞는다. 일반 API/map 채택이나 행정코드 개별 UI까지 요구한다면 추가 미구현 범위이며 이번 리뷰가 그 수용을 보장하지 않는다.
- React/브라우저를 실행하지 않은 정적 표시 검사다. root의 로컬 UI18/657검사와 혼동하지 않는다.

## 나머지 검사·미검증 한계

- FULL변경37파일(제품·테스트·OpenAPI·의존·Compose·문서)의 전체 delta와 필요한 기존 origin/session/scope/repository/migration 경계를 읽었다.
- git diff --check는 **exit2**. frontend/lib/api.ts:376의 new blank line at EOF만 확인했다. 동작 P0/P1/P2에 세지 않았으며 자동 정리/소스 수정0이다.
- BFF 반례는 소유 fetch 대역이다. 실제 Origin 함수와 고정 location/job문은 사용했으나 durable session 생 API·브라우저 cookie는 실행하지 않았다.
- 실제 PostgreSQL lock/DDL권한/partition/schema 적용, source NUMERIC와 파생 float의 실제 PG 비교 계획, DB 총 connection/RSS, 프로세스 중단, 실제 provider timeout/quota, 브라우저/Daemon/coordinator는 **NOT_RUN**.
- Common r2의 VWorld SDK 전체 body/RSS 상한 미검증을 유지한다. 외부 payload 임의 크기에 대한 OOM 부재를 주장하지 않는다.
- CI 양쪽 SUCCESS는 의뢰자 확인이다. root657+추가schema/최종계약·UI18·PG17은 의뢰자/저자 증거이며 본인이 재실행한 결과가 아니다. Transport 정식 게이트는 별도다.
- 제품 수정은 없다. 현재 불변 head의 반례를 보존하며 새 head에서 두 건을 직접 closure해야 한다.

## 변경 git object SHA256

| 파일 | SHA256 |
| --- | --- |
| .env.example | 09eb7c83d85fbb780e152bb0c8ee817be61698e5a5017cea12c0517ce8862747 |
| alembic/versions/0021_reverse_addresses.py | 5870ae21c23059c8e2631111bc31811cf0c14d394bdecd90ef1d35ce7b07cae0 |
| compose.yaml | d8c389aa97c36382a6b7336f02499d27a508c63103155ad8cf37706cacd7e716 |
| conftest.py | 52cb672c8f69439ffa5593cee60ad588f18dc60bae5c216f3f1eb10aee11690e |
| docs/architecture/data-model.md | e3432d4548363fd2ddaa47f02436f534b1b7ed155aee7094a514a5b7074043cd |
| docs/journal.md | f7931f7262d2c56e3c82bcb4d6bb699d4e6dbb9677728845bd5f5ddcf990a020 |
| docs/resume.md | 49d9642fc1a5e16f927652374bc0f0f8e1ebbc0178f5988bf13f3dc96da2711a |
| docs/reverse-address-refresh.md | e369803cc02742001b335993a65b7a006d3aec2493ff04f2f16cebfdc84746c1 |
| packages/kor-travel-weather-admin/frontend/.gitignore | 02f2fab1ac8928d15be267a95c7758727f236d5f6669853d7b111fde8d43d1da |
| packages/kor-travel-weather-admin/frontend/app/admin/dagster/page.tsx | 50f93782578d9f3c62a899a5fa3248afadf67ab59e0af39ed967390a999bfe1e |
| packages/kor-travel-weather-admin/frontend/app/api/address-refresh/route.test.ts | e4867b467254b34b415eddc505b058816baf5bd700120774b5bd07bb9797d527 |
| packages/kor-travel-weather-admin/frontend/app/api/address-refresh/route.ts | 0b067762e2e241239691f0ff10b148b53abf4cb9a986f534ee7f85feccba9baa |
| packages/kor-travel-weather-admin/frontend/components/address-refresh.module.css | 64ac06bc40ca6740de9dca6d0cc027a915d1af2523d6957d5c9088af1595729d |
| packages/kor-travel-weather-admin/frontend/components/address-refresh.tsx | 6495e092f46dc0ad6a36504bd790fa67b94beed212f8cee8ffe01f57929c1c14 |
| packages/kor-travel-weather-admin/frontend/e2e/address-refresh.spec.ts | 026dedfdc00e07105f60997453e10e4d40a1125150b2b6e17257eca691664213 |
| packages/kor-travel-weather-admin/frontend/lib/api.ts | 174002a5c06bffdcf29ea649337b8d56080198f073a84066c935fafa60ed8511 |
| packages/kor-travel-weather-admin/frontend/lib/dagster-scope.ts | 896d1c91409a8784e412a10742d9ad98ce942634904da5709aa9eacac6508689 |
| packages/kor-travel-weather-admin/frontend/package-lock.json | 166ec442e413063f8df711fcbf32131dc866ad8030f047bcccbc4576497ef831 |
| packages/kor-travel-weather-admin/frontend/package.json | 6a3b32f71dbdd10c37fa480b297c879912de658284cc52a85b753b2ca5899c9d |
| packages/kor-travel-weather-admin/frontend/playwright.config.ts | 5bc781f375e38f9c8b5b5aa4837689c4a338a92509c9931221f5ccb8eff596e8 |
| packages/kor-travel-weather-admin/frontend/vitest.config.mts | f62cecf170a6c8979f2ba44e8d9fd2f52bcb11ea8ac400fd28ef21eb2d0182a1 |
| packages/kor-travel-weather-api/openapi.json | 81ca7dda3cf5acf3fcb9e5271923f58ead2a8372ee5e376cc2a2a6f2d194a47e |
| packages/kor-travel-weather-api/src/kortravelweather_api/routers/weather.py | 2fdf9aa5d639259afc4a9aae32dffc31292deb56fb821fc0aebd62b11c8ff92a |
| packages/kor-travel-weather-api/tests/test_address_refresh_api.py | ded54265d5e1ab3cb3e04b2aa2a25387047d840c20c2a575dcefc69b4ebfc508 |
| packages/kor-travel-weather-dagster/pyproject.toml | 0939fb25a04a428f80f9db862c692e7ddc8287ea1c537ff8952773191af0e667 |
| packages/kor-travel-weather-dagster/src/kortravelweather_dagster/address_refresh.py | b1fd72af87e7a057ae0f774a088633d94ae8bd59d66fa1362520608ae4576237 |
| packages/kor-travel-weather-dagster/src/kortravelweather_dagster/definitions.py | db09d188ac0edf4ef7c06b800fe6d14cdcd2ae11747002adac36c5ec2cdf8954 |
| packages/kor-travel-weather-dagster/tests/test_recovery_adoption.py | 983e7138383260a547a004ad7cced6ca34ea39546f89dbfcd847247f6dfdf557 |
| pyproject.toml | fb02cbeef1b206b4956c4947baa06e299e7b8b96125aa9c7fec00190a25bcacc |
| src/kortravelweather/address_refresh.py | c74aba4844165c5bc4b0425d4c3c743ebc1a805fa77ea42e08842e48303e6edc |
| src/kortravelweather/repository.py | 3c382a23715db74ebb50521c44dad02ad19eaa6fe71b6f4b2baeed9fd737688a |
| src/kortravelweather/settings.py | d656c94c454b780fbd801b6009f0767aae3136c76709f3e6d16e8d789d7363e2 |
| tests/test_address_refresh.py | ee14090ffdafe760c5ac60428cbfd85f0e72064ec717900b00a31cdabc309b40 |
| tests/test_address_refresh_graphql.py | e593956b19c726867a71f750fe1e9996aed40dc4b16924dafaa68da36162bb5f |
| tests/test_common_health_adoption.py | 49d4d50f7c08689d3edcbaa95b5f3a4bde74a9352934a72476066670c4eb7ac1 |
| tests/test_migration.py | 7d5be3d6f8c9e6ad7698268c6e6952d7b55bdee42cf469285f56f02f870f656f |
| uv.lock | 3d88c05c565f1b4e3dc1a1a76601cbd3e1a2c01a12dc10461a2cc44610885683 |
