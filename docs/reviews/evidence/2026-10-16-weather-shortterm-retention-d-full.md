# Weather103 독립 FULL12 검토 D

판정: REQUEST_CHANGES / P0=0·P1=0·P2=1. 운영 삭제/서비스 적용 승인은 아니다.

HEAD f64ee6e4c93700cd5518475562d07cd82481a6ae, base916715d7ca9414e259adb60ea22c73543a891af6.
전체 diff SHA256 3cd6c86d802806faee40cac717b21e8eaeecb095c2524f9013681bdd02ae0a36
제품·문서·시험12파일 전체와 기존 ingest/retention/partition lock·참조 문맥을 읽었다.
immutable Gitarchive에서만 검증하고 타 리뷰 원문은 읽지 않았다. 원본/공유 DB/운영/private credential 접근0.

## W103-D-P2-01 — 미래 대상시각이어도 old known_at 입장 분기에서 제외

src/kortravelweather/repository.py:1618–1626의 기존 expired_window 조기반환이 새
short_forecast_cutoff 검사보다 먼저 실행된다. 따라서 정확한 KMA short discriminator이고
valid_at이 현재보다100일 미래여도, known_at이 기존 global expired_window 안이면 None을 반환하여
새 fact/candidate로 publish하지 않는다. 보존 중인 미래행의 기존 pointer가 없을 때도 그 old clock의
replay는 여기서 제외된다.

자기 실제함수 모형은 aware oldknown=now-20일, valid_at=now+100일, expired_window=[1970,horizon),
short_cutoff=now-6h로 _insert_value_session을 호출했고 결과None/add0을 확인했다. DB나 monkey
대체 함수가 아니라 frozen 본체의 조기반환이다. _redate_forgotten_replays_session은 저장행이
없고 replay일 때만 적용하므로 모든 future 입장/기존행 재발행 경계를 해결하지 않는다.
실제 PG 미래 보호 회귀도 seed 때 _retention_bounds를(None,None)로 바꾸어 이 입장 경로를 우회한다.

이것은 기존 known_at 정책에서 유래한 상호작용이다. 기존 저장 future fact의 DELETE/partition
보호가 구현되지 않았다는 뜻은 아니다. 이번의 무제한 미래 보존 요구와 과거 source의 미래값
재입장 요구까지 완료하려면, 그 범위의 known_at admission/재발행 정책과 기본 partition floor
정합성을 명시·검증해야 한다. 만료valid_at의 재유입 금지는 유지해야 한다. 수정은 author/root 소유다.
author는 경로 응답에서 별도 future 재입장 fix 준비 중이라고 알렸으나 새 source/타 리뷰는 읽지 않았다.

## 확인한 안전 성질

- 정확한 provider/dataset/domain/style 및 valid_at<cutoff만 대상이다. 경계 같음/미래/NULL/타원천은
  전용삭제 predicate에서 제외한다. whole COUNT 대신 batch최대1000을 선택한다.
- 복합키(value_id,known_at)와 정확한 join, pointer→fact 순서가 한 transaction 안에 있다.
  FOR UPDATE SKIP LOCKED, 실제잔존 EXISTS, advisory purge lock·statement5s/lock1s를 사용하며
  행수불일치는 예외로 pointer삭제도 rollback된다. 전용삭제에서 source/raw/association DELETE0.
- 신규 expired fact를 transient cache에 남겨 같은 batch의 immutable 충돌 검증을 유지한다.
  기존 source alias/lineage 검증 뒤 skip하므로 만료라는 이유로 원천 유효성 오류를 삼키지 않는다.
- global dated/default retention 및 수동 DEFAULT swap/drop은 table lock 뒤 future fact의 확정False를
  확인한 경우만 destructive DDL로 진행한다. NULL/timeout/DB오류는 보존/실패 경계다.
  동적 테이블명은 기존 catalog 날짜이름 또는 canonical DEFAULT로 제한된다.
- retained partition은 기존과 다른 metadata로 보고하며 그 안의 타 product 행이 더 오래 남을 수 있다는
  문서 한계가 있다. 새10분 schedule의 실행량/큐/협력60초와 job300초는 wall-clock 삭제 SLA가 아니다.
- CLI 기본dry-run/명시execute/aware안전cutoff/실패error_class만 출력·각batch progress·무자동재전송이
  명확하다. 문서가 SQL/연결·pool 대기 때문에60초를 넘을 수 있다고 적어 harddeadline으로 과장하지 않는다.

## 독립 실행/한계

실제 Weather dependencies가 있는 읽기전용 venv를 이용하되 ownarchive PYTHONPATH와
--noconftest/-c /dev/null로 원root conftest를 배제했다. 실제 module.__file__가 ownarchive임을
검증한 순수 회귀 **38 PASS/0.12s**. pytest cache를/dev에 만들지 못했다는 경고1개는 원문 보존했고
DB/SDK instance/HTTP/provider/SSH/Docker 실행0이다. 원문 weather103-review-d-pure-r1-20261016.log,
자기 future 반례 own_future_model.py는 archive reference에 고정했다.

실제 PG 동시 writer/FK/DDL 계획·처리량·WAL·disk/새job 실행을 재현하지 않았다. Session 대역 rollback
시험은 PG rowlock·복합FK 실행 증거를 대체하지 않는다. actor C의 소유 full/PG 시험 결과는
본인 count에 합산하지 않는다. source pass 이후에도 root의 bounded 운영계획·초기 backlog·future 보존
전후 검증이 필요하다. 현 f64의 유일한 미해결 제품 범위는 위 future admission 상호작용으로 남긴다.
