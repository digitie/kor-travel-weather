# Weather103 purge once 독립 FULL 검토 D

판정: **SOURCE_PASS / OPEN P0·P1·P2 0**. 실제 삭제 성공·권한·시간·행수 승인과 구분한 운영 source 검토다.

고정 child: weather103-purge-once-child-c-r1-20261017.py
SHA256 aee701b70efebd8861b5b73722c93c75df6b524524c24ea63c840a8faa9d2985
고정 controller: weather103-purge-once-controller-c-r1-20261017.remote.py
SHA256 99ec502b473ec8500a7afdc4234ba1869064c1f42ece2281cb3d29bc50254aa2
handoff: weather103-purge-once-handoff-c-r1-20261017.md
SHA256 73b13bff657cbe4e0842346145b7a59a431d9d33d00e851d7452460be3fe6eb0
제품 source58230797b1b3c932537b28fc8ca5139e8075c49e / Common c485 계보.

두 source·인계 전체와 실제 제품 retention/repository 호출 경로를 읽었다. D는 이 child/controller 작성자가 아니며 A의 이번 리뷰 원문/모형은 읽지 않았다. private/seed/인증/운영 증거를 열지 않았다. 제품 source와 다른 작업자 파일 변경0이다.

## 정본·권한·삭제 범위

child의 EXPECTED_SOURCE_FILES core31 전집을 source582 Git ls-tree/show로 직접 재계산해 exact를 확인했다. 설치 source와 함수 slot은 전후 검사한다. 실행은 기존 settings의 database_url로 기존 WeatherRepository/run_short_forecast_retention을 호출하며 별도 DELETE/DDL·관리자·Env override·timeout 확대가 없다. execute 미명시는 dry-run이다. controller도 다른 key/admin/Env 주입을 거절한다.

제품 정본은 fixed aware cutoff<=(host와 DB now-6h), KMA short/valid_at<cutoff만 선택하고 정확 복합키로 pointer→fact를 같은 batch transaction에서 삭제한다. source/raw 테이블 전체 DELETE는 추가하지 않는다. 제품의 statement5s/lock1s·purge advisory·SKIP LOCKED/remaining 재조회·batch별 commit·기존 retry없는 계약을 따른다.

child는 DB/OID/0020 revision/fact parent11·leaf10/부분 index11 valid-ready-btree·정확 key/predicate/원99 index·constraint를 검사하고 전후 canonical hash를 비교한다. writer_guard는 사전 및 각 BATCH_INTENT에서만 관측하며 새 writer를 원자적으로 막는 락이 아니다. root는 daemon OFF·수집 및 외부 writer 격리를 별도로 유지해야 한다.

## 보호·시간·부분 commit 경계

보호 표본은 0..16개 exact(value_id,known_at)다. future/equality/null/nonshort category를 확인하고 fact/source/현재 pointer MD5의 canonical 전체 SHA를 전후 비교한다. 이는 표본 불변 증거이며 모든 미래값·모든 source/raw 불변을 표본이나 통계 추정치로 증명하지 않는다. 0표본/category0이면 해당 보호 실측은 없다. root의 실제 future 표본과 경계에서 충분히 먼 valid_at 선택은 실제 입력 gate다.

허용 profile은100/1/10 또는1000/100/60뿐이다. 제품60초는 batch 사이의 협력 budget이며 pre/post query와 이미 진입한 batch 시간이 있어 프로세스 전체 hard60초를 보장하지 않는다. 각 SQL5초와 outer420초 observer를 구분한다. budget_exhausted/deferred는 PURGE_COMPLETE로 바꾸지 않는다.

BATCH_INTENT를 fsync한 후 제품 batch로 들어가고, 정본 함수가 commit을 반환한 다음 BATCH_COMMITTED를 fsync한다. 이전 ACK와 pending을 별도로 유지한다. SQLSTATE57014/55P03의 알려진 실패는 FAILED, commit/ACK 불명은 UNKNOWN이다. 이전 commit을 전체 rollback했다고 기록하지 않으며 두 상태 모두 자동 재전송하지 않는다. 후행 catalog/protected/source 검사 실패도 이미 commit한 삭제를 되돌리지 않는다.

## once·G·실행 정산

controller는 fixed API/CS/image/Compose와 frozen50전체 inventory·running9·Geo5 healthy를 전후 검사한다. G root700/file600/regular/nlink1/inode/flock, boot/PID/ticks/restart/full Config·HostConfig·정규 Mounts를 계승한다. 원 index controller에서 이어진 함수14개의 AST가 exact임을 직접 확인했다(상수/호출 정책은 새 purge 계약으로 별도 읽었다).

ExecCreate ID를 durable 저장하고 exact command/ContainerID/TTY·privileged/PID를 확인한 뒤 exclusive start.once와 detach start1회만 수행한다. 같은 known Exec terminal까지 관측하며420초/통신오류는 noKill/noReplay UNKNOWN이다. G 해제는 child/DB 종료 증거가 아니므로 unknown 때 root는 같은 ID의 읽기 정산을 먼저 수행한다.

회수는 own UUID의 정확5개 일반파일만 최대1MiB tar로 허용한다. 경로·source/seed·stdout1JSON·ledger operation ID/첫INTENT/마지막결과·native Exit와 phase·빈stderr를 묶는다. raw 오류/DSN·Env·SQL 문구는 안전 응답에 출력하지 않으며 error_class/sqlstate/ACK·수치/hash만 남긴다.

## 독립 inert 확인

실제 함수/AST의 핵심42조건 PASS:
- 두 profile/default dry, 미래/naive cutoff·bool·변조 source/hash·중복 partition/보호 pair·admin key 거절.
- 전체 Git core31 source map exact.
- 보호4 category/0표본·변경된 pointer digest 검출·누락 pair/잘못된 future category 거절.
- 실제 child exception handler에 pending true/false, SQLSTATE57014/55P03/08006 및 이전 ACK를 주어 FAILED/UNKNOWN·partial commit 보존 확인.
- 실제 parse_archive에 정상 DRY_RUN 및 source/seed/Exit/stderr/stdout/ledger/path traversal 변조 거절.
- 실제 outer schema의50개 synthetic inventory 및 sourcehex/Compose/개수/Env 주입 거절.
- 실제 readonly wrapper의 READ ONLY→statement5000ms→lock1000ms 순서.

모형은 정의만 AST로 추출했고 module entrypoint/제품 run/psycopg connect/SDK를 호출하지 않았다. tar는 메모리 BytesIO, query/transaction·ledger는 대역이다. 원 conftest/기본 PG에 접속하지 않았다.

모형: weather103-purge-once-review-d-r1-20261017.model.py
결과: weather103-purge-once-review-d-r1-20261017.safe.json
SHA256 47e7c91c2fcc605b4115e7a6ad29bf3c8af314e8a5c2a5a6a228198591417f0b

실제 PG/DELETE/SSH/Docker/HTTP/SDK/provider/private 접근0. 실제 권한·5초 DELETE latency·WAL/디스크·변경 row수/보호 표본·전체정리 완료는 root의 별도 실제 단계에서만 판단한다. root가 수행 중인 dry-run이나 기존 index 실측을 본인 모형 결과에 더하지 않았다. 원 source/실패/부분 commit 기록을 보존하며 모든 도구 terminal이다.
