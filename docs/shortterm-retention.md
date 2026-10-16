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

기존 index는 location-leading 또는 dataset/metric이므로 전역 valid_at 정리에 최적이라는
증거가 없다. partial(valid_at, known_at, value_id) 후보는 실제 계획/카운트 후 별도 판단한다.
partition parent의 CREATE INDEX CONCURRENTLY는 지원되지 않아 이 PR에 임의 DDL을 넣지 않는다.

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

partition 검사는5초 안에 확정 False일 때만 DDL을 허용한다. timeout·DB 오류·NULL은
삭제 허가로 바꾸지 않는다. 보존된 일별 partition은 partitions_retained_short_forecast로
따로 보고한다. 전체 partition이 더 오래 남으므로 그 안의 다른 product가 함께 남을 수 있지만,
다른 product의 기존 행 삭제 predicate 자체를 늘리거나 줄이지 않는다.
이미 detached된 과거 데이터의 projection 재구축은 이 변경의 자동 복구 범위가 아니다.

10분 schedule은 queue 대기·worker 중단·처리량 부족으로 늦어질 수 있어 물리 삭제의
정확한 wall-clock SLA를 보장하지 않는다. 새 만료 fact 유입 차단과 유한 정리 작업을 함께
제공하며 초기 backlog는 root가 disk/WAL·계획·배치 결과를 보고 별도 완주해야 한다.
