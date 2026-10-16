# Weather103 단기예보 보존 독립 FULL12 검토

대상 f64ee6e4c93700cd5518475562d07cd82481a6ae / 기준101916715d7ca9414e259adb60ea22c73543a891af6.
판정 CHANGES_REQUESTED / P0·P1 없음 / P2 W103-A-P2-01 OPEN1.
이 검토는 두 번째 reviewer나 작성자의 결과를 읽지 않았으며 원본 제품을 수정하지 않았다.

## W103-A-P2-01 — known_at 보존 hole이 미래 예보의 늦은 ingest/replay를 버림

src/kortravelweather/repository.py:1617~1628의 expired_window 조기 return은
KMA 단기예보 valid_at이 미래인지와 무관하게 floor≤known_at<horizon인 새 fact를 제외한다.
같은 파일:2190~2193은 과거 known_at 아래의 replay를 재날짜화할 때 입력 fetch도 horizon
이전이면 valid_at이 미래여도 result에서 제거한다. 이미 존재한 미래 fact에 partition 삭제
가드를 적용하는 것만으로는 사용자 future 무제한 요구를 충족하지 못한다.

실제 immutable 메서드에 synthetic now=2026-10-16T12:00Z, valid_at=now+10일,
known/collected=now-20일, expired_window=[now-30일,now-7일), horizon=now-7일을 넣었다.
expired_short_forecast는 False임에도 새 fact는 None/add0, replay는 결과0개다.
동일 미래 fact에서 known-window만 없애면 True/add1이므로 유효시각 정책 때문이 아니다.
실DB/fixture 없이 실제 _insert_value_session 및 _redate_forgotten_replays_session을
독립 inert session으로 실행했다. SQLAlchemy engine 생성은 금지하는 대역으로 차단했다.

root는 사용자 미래 무제한 정책에 late ingest/replay 보존도 포함됨을 확인했다.
기존 PG 미래 partition 회귀는 seed에서 _retention_bounds를 우회하므로 이 반례를 검사하지 않는다.
최소 수정은 원본 source/raw·fetched lineage·기존 fact immutable 충돌과 typed 복합PK를 보존하면서
미래/최근6시간의 KMA 단기예보를 신규 입장 및 replay 단계에서 살리고 실제 parent/default
partition 수용 경로도 함께 보장해야 한다. 단순 조기 return 제거 뒤 partition 오류로
바꾸거나 raw 원천 시간을 조용히 덮는 것으로 완료하지 않는다. 기존 만료 예보 유입 차단과
다른 provider의 기존 retention은 유지해야 한다.

## 직접 읽은 경로와 확인

checkpoint의12파일 SHA를 immutable Git object와 전부 대조했고 Python11개 compile PASS.
문서·CLI·정책 모듈·repository·모델·DEFAULT 수동 경로·Dagster 정의/retention helper 및
신규/수정 시험 전체를 읽고 기존 callers/partition lock·FK 코드와 대조했다.

- 정확 provider/dataset/domain/style 및 valid_at<cutoff를 쓰며 동일 경계·미래·NULL과
  다른 원천을 새 만료 predicate에서 제외한다. UTC/KST 비교를 aware 값으로 수행한다.
- 정리 후보는 최대1000 복합(value_id,known_at), FOR UPDATE SKIP LOCKED다.
  pointer→fact 삭제를 같은 transaction으로 묶고 소스/raw/association 삭제를 추가하지 않는다.
  known_at min/max pruning도 exact pair 비교를 대체하지 않는다. SQLAlchemy VALUES는 bind다.
- local lock_timeout1초/statement_timeout5초·advisory purge lock·부분 commit 보고가 있으며,
  lock 후보를 못 골라도 EXISTS로 남은 행을 확인한다. deferred/실패/예산 소진을 완료로 숨기지 않는다.
- 전체60초는 협력적 예산이다. 매 batch 전에만 검사하고 SQL·pool 대기가 있어 hard deadline은
  아니다. 문서가 이 한계를 명시한다. 초기 전체 backlog의 공간/WAL/실제 계획과 처리량은 별도다.
- ingest의 만료 fact는 DB에 add하지 않고 transient 비교 자료만 유지해 동일 batch immutable
  충돌을 검사한다. 이미 저장한 만료 replay의 projection 재발행을 막는 새 경계는 의도와 맞는다.
- 전역 dated/DEFAULT delete·swap/drop은 미래/최근 short fact를 보존한다. partition DDL 앞
  잠금과 확정 False만 허용하는 guard가 있어 timeout/NULL은 삭제 승인이 아니다.
  row를 보존하기 위해 같은 partition의 타 product까지 오래 남을 수 있다는 storage 한계는 문서화했다.
- projection/fact는 실제FK에 의존하므로 concurrent ingest와 충돌 시 한 transaction이 rollback된다.
  단순 순수 모형이 실제 PostgreSQL lock/deadlock/WAL/partition pruning 성공을 입증하지는 않는다.
- 새10분 job1개/asset1개·runtime300초만 추가하고 기존 수집·UI/API·Common pin·Docker는 변경하지 않는다.
  PurgeReport 추가 필드는 Dagster metadata 경로이며 외부 OpenAPI DTO consumer는 확인되지 않았다.
  defaultSTOPPED optional native sensor와 기존 수집 태그·grace를 별도로 바꾸지 않는다.

## 독립 실행과 한계

actual method future 입장 반례2경로+control1, 그리고 추가23조건의 실제 함수/SQL compile
독립 모형을 실행했다. 경계/다른원천, unsafe cutoff/batch의 transaction 진입 전 거절,
정확 pair·min/max·bind SQL, pointer 뒤 fact 오류 rollback, READ ONLY dry-run,
purge lock deferred, SKIP LOCKED 잔여, partition True/NULL/False/식별자 거절,
첫 batch commit 뒤 둘째 실패에서 마지막 commit을 합성하지 않음을 확인했다.
각 조건은 자체 대역이며 작성자38 tests/PG/fullsuite 결과와 합산하지 않았다.

실제 DB·SDK·HTTP·Docker·운영·SSH·기본/shared test fixture 접근0. 후보 conftest/저자 Session
fixture를 import하지 않았다. private auth/runtime 파일은 열지 않았다.
actual PostgreSQL 복합FK/동시성/DDL·DEFAULT/FLOOR 입장·대량 backlog·WAL은 source만 검토했고
재실행하지 않았다. 작성자 full은 checkpoint에서 RUNNING으로 기록돼 있어 PASS 선집계하지 않는다.

모형 원본:
- weather103-retention-review-model-a-r1-20261016.py SHA3769ca5945e5e984293244e714bb46eed22db89c9f0253e6e9306bfced996dc2
- 확장 r2.py SHA4c431283348df2417148aa1bb96bc86b1dedeadce08398e03e6a52682d06ad4f
- r2.safe.json SHA28b5ae825e04db79de35a0020d47483bdfe4f3e1ba2b020de5e0805cbe179a5d

원f64·모형·실패는 보존하며 저자에게 W103-A-P2-01 직접 전달했다. 수정본은 별도 immutable
HEAD로 admission/partition 경계와 기존 삭제·원천 보존 회귀를 다시 확인해야 한다.
