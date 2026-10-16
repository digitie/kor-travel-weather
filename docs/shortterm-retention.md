# KMA 단기예보의 지난 대상시각 보존정책

## 확정 요구와 범위

단기예보의 대상시각 valid_at이 현재시각보다6시간 넘게 지난 행만 정리한다.
발표시각 issued_at이나 수집시각 known_at의 age로 삭제하지 않는다.
경계와 같은 행 및 미래 예보는 보존한다. timezone-aware 입력만 허용하고 UTC로 비교한다.

정확한 대상은 provider=python-kma-api, dataset_key=weather_domain=kma_short_forecast,
forecast_style=short다. 초단기/중기/실황과 다른 provider는 포함하지 않는다.
이 문서와 후속 구현은 기존101 기준 별도 feature이며 health/Common pin/Docker 및 기존 수집주기 변경은 없다.

## 구현 계획과 참조 보존

기존 fact는 known_at으로 일별 partition되므로6시간 valid_at 정책에 partition DROP을 쓰지 않는다.
최대1000개의 정확한 복합키(value_id, known_at)를 한 batch로 선택하고,
같은 transaction에서 해당 fact를 참조하는 projection부터 지운 뒤 fact를 지운다.
다른 known_at의 동일 value_id는 보존한다. source/raw와 sync-run association은 삭제하지 않는다.
기존 append-only DELETE 허용은 transaction-local PURGE_GUC만 사용한다.
timeout/lock 실패는 rollback하며 자동 재전송·강제취소를 추가하지 않는다.

새 ingest는 batch 시작 때 경계를 한 번 계산하여 만료 fact를 새로 저장하지 않고,
기존 만료 replay도 projection으로 되살리지 않는다. source/raw와 기존 fact 충돌 검증은 유지한다.
전용 job은10분 주기로 최대100×1000행을 협력적60초 예산 안에서 정리한다.
문장당5초/lock1초이며 각 문장·pool/연결 대기 때문에 전체 wall은60초를 넘을 수 있으며 hard deadline은 아니다.
SKIP LOCKED로 선택하지 못한 행도 남았는지 확인하여 빈 batch를 완료로 오인하지 않는다.
queue 대기/중단/예산 소진이면 물리 정리가 늦어지며 완료 metadata 대신 실패를 남긴다.
현재 daemon OFF/배포·초기정리는 root의 별도 검토·운영 영역이다.

## 운영 명령

ktwctl purge-short-forecast는 기본 dry-run이며 batch 선택수만 센다. 전체 fact count가 아니다.
--execute에서만 삭제한다. --cutoff에 dry-run이 반환한 aware 경계를 고정할 수 있으나
현재시각-6시간보다 늦은 cutoff는 거절하여 미래/최근값 삭제를 막는다.
--batch-size1~1000/--max-batches1~100/--budget-seconds0초초과60초이하를 사용한다.
operation_id/cutoff와 batch 전 intent·commit 뒤 누적 삭제수·남은행 존재를 JSON으로 남긴다.
raw/DSN/주소/credential은 출력하지 않고 실패는 error_class만 기록한다.
중간 실패는 이미 commit한 batch를 되돌렸다고 하지 않으며 자동 재전송하지 않는다.

정본 부분 인덱스 ix_weather_values_short_forecast_retention은 정확한4개 discriminator와
valid_at IS NOT NULL인 행에만 (valid_at, known_at, value_id) 순서를 제공한다.
0019 다음0020 migration과 metadata가 같은 정의이며 새 leaf에도 자동 상속된다.
기존 인덱스는 제거하지 않는다. reported 운영 표본은 기존 후보 LIMIT1000 선택에2.921초,
BitmapAnd 약88만 entry 접근, 전체 count5초 timeout이었다. 원문 private 자료를 읽거나
재실행한 결과가 아니라 root가 전달한 측정값이다. 새 운영 인덱스의 효과는 아직 미검증이다.

partition parent의 CREATE INDEX CONCURRENTLY는 지원되지 않는다. 0020은 명시적인
transactional CREATE INDEX이므로 기존 leaf 전체의 SHARE 잠금이 쓰기를 막을 수 있다.
root만 active write/drain·잠금·실제 여유·WAL/인덱스 증가 예상량을 검토한 유지보수 창에서
실행한다. 데이터 약40만 관련행이라는 추정만으로1.84GB 여유가 충분하다고 판단하지 않는다.
설치 전 count가 timeout이면 반복하지 않고 계획과 bounded 표본으로 먼저 판단한다.
실패하면 transaction과 revision은 함께 rollback되며 IF NOT EXISTS로 다른 정의를 승인하지 않는다.
commit 여부가 UNKNOWN이면 version/index 정의·valid 상태를 읽어 정산하기 전 재실행하지 않는다.
downgrade도 이 새 parent/상속 index만 제거하며 새 DML 중 잠금이 필요하므로 root가 별도 판단한다.
온라인 설치가 필요하면 parent ON ONLY + leaf별 CONCURRENTLY/ATTACH를 별도 검토해야 하며,
이 migration을 자동으로 온라인 방식이라고 부르지 않는다.

## 검증과 운영 순서

경계1마이크로초 전/같음/후, 미래와 오래된 발표의 미래행, UTC/KST 동등성,
다른 provider·dataset·style·domain·NULL valid_at,
복합키 projection 참조와 원천공유, batch/rollback을 검증한다.
기존 fact를 전부 count하거나 원천 raw/주소를 출력하지 않는다.

운영은 root만 count/정확predicate와 실행계획·소유성 확인→bounded batch 삭제→
삭제수·미래/경계/타원천 보존·참조 정합 재확인 순서로 수행한다.
이 PR은 운영 삭제·새job 실행·서비스 변경을 수행하지 않는다.


## 디스크·WAL 운영 경계

현재 호스트의 여유가 작으므로 root가 실행 전 실제 rootfs/PG 데이터 mount의 여유를
확인하고 각 batch의 row수·elapsed·남은 시간 예산과 WAL 증가를 별도로 관측한다.
SHORT_FORECAST_COUNT_SQL과 같은 정본 predicate의 whole count는 EXPLAIN부터 검토하며,
5초 읽기 예산에서 실패하면 partial/timeout으로 남기고 무제한 count를 반복하지 않는다.
dry-run 기본 matched_in_batch는 최대1000행 표본이며 whole count라고 표시하지 않는다.
대량 초기 정리와 새 partial index 설치는 서로 별도 gate이며 agent가 운영DDL을 실행하지 않는다.

## 전체 보존 경로와 미래 예보 보호

발표가 오래됐더라도 미래 대상시각의 KMA 단기예보는 전역 known_at 보존기간으로
삭제하지 않는다. 다음 source 정본이 동일한4개 discriminator와 valid_at 경계를 공유한다.

| 경로 | source | 보호 |
| --- | --- | --- |
| 수집·재수집 | repository._ingest_batch_session / retention.expired_short_forecast | 지난6시간 밖의 새 fact·replay projection을 제외, source/raw 보존 |
| 단기예보 정리 | repository.purge_expired_short_forecast / cli / Dagster 전용 job | 엄격 valid_at < cutoff, 정확 복합키 참조 순서 |
| 전역 일별 정리 | repository.purge_expired_history / _detach_expired / _drop_detached | 최근·미래 단기예보 pointer 보존, fact가 남은 partition DDL 보류 |
| 수동 DEFAULT 부분 정리 | default_partition.purge_expired_rows | 같은 미래 fact와 projection을 삭제 대상에서 제외 |
| 수동 DEFAULT 전체 교체·잔여 DROP | default_partition.swap_default / drop_old_default | 테이블 잠금 후 보존 fact 존재 검사, 있으면 작업 거절 |

partition 보호 조회는 기존 statement_timeout과5초 중 더 작은 문장 예산을 사용한다.
0(무제한)이면5초로 제한하며 성공 후 기존 설정 원문을 복원하므로 후속 DDL 예산은 바꾸지 않는다.
확정 False일 때만 DDL을 허용한다. timeout·DB 오류·NULL은
삭제 허가로 바꾸지 않는다. 보존된 일별 partition은 partitions_retained_short_forecast로
따로 보고한다. 전체 partition이 더 오래 남으므로 그 안의 다른 product가 함께 남을 수 있지만,
다른 product의 기존 행 삭제 predicate 자체를 늘리거나 줄이지 않는다.
이미 detached된 과거 데이터의 projection 재구축은 이 변경의 자동 복구 범위가 아니다.

10분 schedule은 queue 대기·worker 중단·처리량 부족으로 늦어질 수 있어 물리 삭제의
정확한 wall-clock SLA를 보장하지 않는다. 새 만료 fact 유입 차단과 유한 정리 작업을 함께
제공하며 초기 backlog는 root가 disk/WAL·계획·배치 결과를 보고 별도 완주해야 한다.


## 늦은 미래 응답의 재입장

원 known_at이 DEFAULT 또는 실제 dated partition에 수용되면 그대로 저장한다.
원 fact가 없고 그 clock이 retention gap으로 수용 불가하면 보존할 단기예보만 실제 재입장
시각을 known_at으로 사용한다. 기존 source의 earliest retained row가 있으면 그 pair가 우선이며,
다음 replay도 같은 pair를 재사용한다. source.fetched_at에 원 canonical clock을 남기고
collected_at/issued_at/valid_at/payload/source_record_key는 바꾸지 않는다.
배치당 기존 catalog 조회를 공유하고, 원 source를 이미 가진 재입장 후보만 value_id별 최초 row를
최대1000개씩 조회한다. row별 query, DEFAULT CHECK 변경, 과거 partition ingest DDL은 없다.
현재 재입장 시각 자체도 수용할 partition이 없으면 schema 오류를 숨기지 않고 실패한다.
새 예외는 [ADR-072](adr/072-weather-bitemporal.md)와 data-model에 명시했다.

## 검증 범위와 실패 이력

최초 f64 전체 시험588PASS/2FAIL은 보존했고 현재시각 기반 fixture 두 건과 미래 재입장 예외를
보강한6fa는 전체599PASS/49warnings(652.21초), Ruff 통과했다. frontend196/type 검증은
수정 없는101 기준이다. 이 수치를 뒤에 추가한 timeout/index 소스의 최종 전체 결과로 합산하지 않는다.

별도 소유 PostgreSQL의 TEMP 표에서 원6fa 보호 조회가2초 예산을5초로 늘리는 RED를 확인했다.
새 구현은0/60초/2초/50ms의 원문 설정 복원을 확인했다. 단기예보1000행을 포함한 합성1만행
TEMP partition에서는 기존 Sort→새 Index Scan 계열/no Sort, 동일25개 복합키, 새 leaf 상속,
downgrade/reapply의 동일 결과를 확인했다. 이 로컬 소표본은 운영 실행시간·CPU/WAL 절감 증거가 아니다.
최종 통합 전체 시험과 두 독립 source 리뷰, root 운영 설치·초기 정리는 별도 gate다.
