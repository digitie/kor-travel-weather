# Weather103 purge once 외피 독립 FULL 리뷰 A

**SOURCE_PASS / 열린 P0·P1·P2 = 0/0/0.** 실제 정리 완료·운영 실행 판정이 아니다. 다른 reviewer/작성자 모형 원문은 읽지 않았다.

## 동결 범위

- child weather103-purge-once-child-c-r1-20261017.py SHA256 aee701b70efebd8861b5b73722c93c75df6b524524c24ea63c840a8faa9d2985
- controller weather103-purge-once-controller-c-r1-20261017.remote.py SHA256 99ec502b473ec8500a7afdc4234ba1869064c1f42ece2281cb3d29bc50254aa2
- 시작/종료 두 번 source SHA를 확인했고 전달값과 동일하다. child/controller/BOOTSTRAP 전체와 관련 제품 호출 문맥을 읽었다.
- 제품 source 58230797b1b3c932537b28fc8ca5139e8075c49e. 기존 product runner 사전검토 weather103-purge-preflight-review-a-r1-20261017.md를 계승한다.
- 운영·private/seed/auth·SSH·DB·Docker·SDK·provider 호출/접근 0. 수정은 본인 새 모형/리뷰 파일뿐이다.

## 검토 결과

### 제품 경계 / 입력

직접 DELETE/UPDATE/DDL을 재구현하지 않는다. 검증·writer/catalog/sample/추정치 SELECT와 정본 WeatherRepository.purge_expired_short_forecast를 호출하는 run_short_forecast_retention만 사용한다. source_files 31개 SHA를 실제 Git582의 src/kortravelweather 각 blob과 대조해 모두 일치했다.

installed_source는 선택 package root 단일성/허용 위치/심볼릭 링크 배제/전체 py 또는 py.typed map/실제 GIT_COMMIT을 확인한다. 실제 사용할 두 함수의 code filename도 같은 검증 root의 retention.py/repository.py여야 한다. 동결 image/API/CS·설치 파일의 runtime 양성은 root 실행 gate이며 여기서 읽지 않았다.

canonical UUIDv4, aware 고정 cutoff<=host/DB 현재시각-6h, 정수이며 bool이 아닌 bounded profile, DB name/OID·parent OID·11 partition IDs, 표본 pair 중복과 category를 검증한다. 허용 profile은 **(100,1,10) 또는 (1000,100,60)**이다. 앞 product CLI 검토의 예시60초와 달리 이 외피의 초기100 profile은10초로 좁다. DB 문장5초/lock1초와 전체 협력적 budget의 차이는 유지한다.

### schema / 인덱스 / 보호 표본

catalog는 정본 revision0020/partition parent RANGE(known_at), parent 포함11 OID/leaf10, 새 parent+leaf index11의 valid/ready/btree/정확 keys(valid_at,known_at,value_id)와 정확4개 discriminator+valid_at NOT NULL predicate를 검사한다. 기존99 index의 정의·OID·filenode·상속 fingerprint를 seed와 대조하며 constraint 및 tree hash도 전후 동일해야 한다. LIMIT sentinel은 초과를 정상 완료로 숨기지 않는다.

보호 검사는 최대16개 명시 pair를 배열 zip으로 읽는다. future/equality/null/nonshort category, fact/source/current-pointer MD5를 포함한 canonical hash를 전후 대조한다. raw row/payload/주소를 반환하지 않는다. **이는 선택된 표본만의 비교이며 전체 미래/raw/source 보존 증명이 아니다.** samples=[]도 명시 sample_count0으로 허용되므로 root는 실제 사용할 미래/기타 보호 표본의 존재·category_counts를 확인해야 한다. equality category에 정확 일치 row가 없으면 이를 만들어 넣거나 가짜 표본으로 채우지 않는다. future는 실행 뒤에도 future여야 하므로 너무 임박한 표본은 안전하게 거절될 수 있다. estimates는 통계이며 정확 count/불변 증명이 아니다.

writer_guard는 다른 client의 xid 또는 active write/DDL 후보가 관측되면 거절한다. batch 직전에도 다시 검사하나 이후 새 writer가 못 생기는 DB 전역 배타 잠금은 아니다. 제품 transaction/advisory/FK·정확 pair 정리 안전성을 대신하지 않으며 root가 maintenance/admission 조건을 책임진다.

### 단발 실행 / G / 결과 계보

controller는 정확50 inventory/9running, API/CS image·PID/ticks/fp, Geo5 포함 주요7개 healthy/restart0/noOOM, boot·Compose hash와 전체 foreign를 전후 fence한다. 실제 현재가8running이면 seed를 임의로 바꿔 통과시키지 않고 이 동결 계약이 거절하는 상태로 취급한다.

root0700/0600/nlink/inode 검증 G를 획득하고 새 UUID stage를 exclusive 생성한다. API 내부에 새 privileged/user/env override 없이 기존 환경으로 isolated Python child를 실행한다. 입력/소스 hex에 DSN·credential을 넣지 않는다.

**ExecCreate intent → 생성된 ID fsync → 전 inventory fence → inspect의 API/command/args/tty/privileged 일치 → start.once fsync → detached start1회** 순서다. 실행 중 positive PID/ticks를 결박한다. controller/BOOTSTRAP/child의 UUID 전용 directory·exclusive file은 같은 attempt 재사용을 차단한다. child source와 seed는 fsync 보관하고 외부 library stdout/stderr는 Quiet으로 억제한다. ledger는 append+fsync하며 product batch 호출 전 BATCH_INTENT, transaction 반환 뒤 BATCH_COMMITTED를 남긴다.

성공 archive도 <=1MiB 응답 cap, 비압축 tar/정규 파일/정확5개 파일/경로·중복·링크 거절, child source SHA/seed 원형, ledger operation UUID·최대210 events·첫INTENT·마지막결과 동일/단일stdout/빈stderr/exit code 정합을 검사한다. fsync한 archive hash·exec terminal 뒤 full fence를 다시 확인한다. 실제 G/파일 권한/exec 및 artifact는 본인이 실행·조회하지 않았다.

## 부분 commit / 실패 / UNKNOWN

- **이 외피는 PURGE_BUDGET_EXHAUSTED·PURGE_DEFERRED도 known-terminal exit0**이다. CLI와 exit 정책이 다르며 삭제 완료는 PURGE_COMPLETE/remaining=false까지 읽어야 한다. DRY_RUN exit0은 최대1 batch 표본이다.
- 100건 commit 뒤 잔여 true면 budget exhausted. 이전 batch commit은 다음 failure나 마지막 보호검증 실패로 rollback되지 않는다. BATCH_COMMITTED는 0삭제/deferred transaction 반환에도 생길 수 있으므로 phase·삭제수·terminal을 함께 본다.
- pending batch에서 SQLSTATE57014/55P03은 제품 context rollback 후 FAILED로 보고한다. 연결유실 등 다른 pending 실패나 commit 후 ledger fsync 실패는 UNKNOWN으로 남기고 acknowledged와 partial_commits_preserved를 분리한다.
- child 생성/초기검증 실패로 archive가 불완전하거나 stdout/ledger 정합이 없으면 controller가 UNKNOWN으로 보존한다. 성공을 합성하지 않는다.
- 420초는 observer 예산이지 child SQL hard deadline이 아니다. timeout 시 exec/child를 kill하거나 재전송하지 않는다. G는 finally 해제될 수 있으므로 root는 UNKNOWN child가 남은 동안 새 mutation을 시작하지 말고 기존 exec/ledger를 readonly 정산해야 한다.
- 같은 UUID를 다른 새 attempt로 바꿔 즉시 재전송하는 것은 once 보장이 아니다. root의 terminal/부분commit/보호/공간·WAL 정산 후 별도 명시 operation만 가능하다.
- count 전체 반복·공간 회수·CPU 개선·10분 job 활성 성공을 이 결과에서 확대 주장하지 않는다.

## 독립 실제 검증

실행 ID A-purge-once-source-20261017. 본인 모형:
 /home/digitie/dev/weather103-purge-once-review-a-r1-20261017/independent-r5.py

1. validate/catalog/protected/archive 실제 함수 AST 및 실제 source 상수 사용 **15개 묶음 PASS**:
정확100 profile, 잘못된 source/bool/budget/cutoff/partition 거절,31Git SHA,0020+11new+99old 정상,invalid/unready/method/keys/predicate/leaf/old-index drift 거절,4category·MD5표본 변화 및 잘못된 category,50/9 seed/누락·tick/UUID·source tamper 거절, archive 정상/exit·extra·stderr·execute-dry 불일치 거절, ID 선행저장과 단일start/noKill AST,compile2.
2. 실제 child run과 실제 제품 runner AST를 유지하고 isolated importer/합성 repository·connection·ledger로 **8 scenario PASS**:
DRY_RUN, PURGE_COMPLETE, BUDGET_EXHAUSTED, DEFERRED,57014 FAILED,55P03 FAILED,08006 UNKNOWN,commit 뒤 progress fsync 실패 UNKNOWN. 모두 기존 BATCH_INTENT 뒤에만 합성 batch가 진행하고 pending unknown을 유지했다. 실제 library/DB/SDK import·접속은 사용하지 않았다.
3. child/controller/내부 BOOTSTRAP compile PASS. 원본 코드·타인 파일 변경 없음.

초기 모형의 GEO 상수 추출 누락(KeyError), isolated import hook 적용 순서(ModuleNotFoundError), Path 주입 누락(NameError)는 본인 scratch r1/r3/r4 원문과 도구 출력에 보존했다. 이들을 제품 실패/운영 실행으로 세지 않고 r2·최종r5에서 수정했다. 첫 실패의 psycopg import 시도는 모듈 부재로 종료했으며 DB/SDK/네트워크 호출은 없었다.

실제 PG/DELETE/rollback·locking·WAL/전체 미래/원천보존·OOM/exec420초·source runtime 검증은 미실행이다. 위 모형 PASS를 운영 양성으로 포장하지 않는다. 새 source blocker 없음; root의 실제 fresh/DB/schema/protected samples/maintainance/WAL gate는 별도다.

safejson: weather103-purge-once-review-a-r1-20261017.safe.json
SHA256 3a38373342a0943e86db0a9e4873749e77affa4004cc91fe9040ef4eb4230988
