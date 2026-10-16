# Weather103 최종 timeout/index 독립 delta 검토 A

판정 SOURCE_PASS / P0·P1·P2 열린 지적0. 기존 W103-A-P2-01 미래 재입장 closure를 유지한다.

Exact HEAD d0a4312edc8de51372b214be9c75e064fb576d7c, 이전 독립 검토6fa7b5947dda7dfb486de47f08921485cf0a6e62. CP weather103-shortterm-retention-c-r3-20261016.checkpoint.json SHA66d8ad931a5d7b9bf6167b9985ea4b51e404ca84826f6c32935e9924ea4eb0f1. 전체5파일 delta SHA c02ae163e3bc8e18a14f5699f16e721e54c9cfdcf705b62358ba243ffb17be46를 직접 읽었다. CP 최종18파일의 size/SHA 모두 Git blob과 대조, Python15개 compile PASS.

이전 FULL12 및 6fa FULL9 delta 원문은 그대로 보존한다. 6fa 보고서 weather103-shortterm-retention-review-a-r2-20261016.md SHAb26f86a5bfbe1b8a317079440c0d2025cabbe2efdb24a28c618118d17e20b5c3를 계승한다. 다른 검토자의 원문/모형은 읽지 않았다.

## timeout 복원

_preserve_short_forecast_partition은 실제 SHOW 원문과 pg_settings.setting bigint를 읽고, 잘못된 타입/음수/누락을 SET 전에 거절한다. 무제한0은5초, 양수는 min(기존,5초)를 사용한다. 확정 False인 성공 조회 뒤 set_config의 bound parameter로 원문을 LOCAL 복원한다. True/NULL/SQL오류는 삭제 허가가 되지 않고 호출 transaction 밖으로 전파된다.

호출 경계도 읽었다. _detach_expired/_drop_detached 및 DEFAULT swap/drop은 보호 조회가 반환한 뒤에만 DDL로 진행한다. _try_partition_ddl/DEFAULT의 transaction 바깥에서 보존 예외를 처리하므로 오류 경로는 rollback된다. 실패 transaction 안에서 설정만 복구하려는 잘못된 finally는 없다.

## 부분 인덱스 및 migration

새 metadata와 Alembic0020의 predicate는 정확히 같은 정적4개 discriminator와 valid_at IS NOT NULL이다. now/cutoff/다른 provider를 index predicate에 넣지 않는다. 키 순서 valid_at,known_at,value_id가 후보 SQL의 ORDER BY와 일치하며 복합키 선택·삭제·bounded LIMIT·보존 경계는 바뀌지 않는다.

0019→0020 체인은 기존 index를 수정/제거하지 않고 parent weather_values에 일반 CREATE INDEX 하나를 추가한다. ON ONLY/CONCURRENTLY/IF NOT EXISTS가 없어 parent와 기존 leaf에 대한 정본 생성이며 새 leaf의 표준 상속 계약을 따른다. downgrade는 새 index만 DROP한다. PostgreSQL transactional DDL 및 Alembic env의 begin_transaction 경계를 확인했다. 실제 leaf 상속·락/WAL·rollback·성능은 이 검토자가 DB에서 다시 실행하지 않았다.

기존 future 재입장/ingest/insert/forgotten-replay 네 실제 함수는 6fa와 AST 동일하다. source/raw/원 fetched·collected/valid 및 earliest retained pair 정책을 변경하지 않는다.

## 독립 실행

immutable git archive와 inert connection에서 기존 재입장36 + transaction/policy23 + 신규 delta23 = 82조건 PASS/EXIT0. 새23에는0/60s/2s/50ms 예산·복원, 잘못된 타입·음수·boolean 거절, True/unknown/timeout 전파 및 모형 transaction rollback, 식별자 선행 거절, 실제 SQLAlchemy metadata/CreateIndex와 실제 offline Alembic upgrade/downgrade SQL 정합, 새 index만 생성/삭제·정적 parent형식·revision chain·4함수 AST 동일이 포함된다.

모형 SHA c596891caa8adb6e489801149b10bd168976606bf1c92ea4694c4823233dc323.
모형 safe SHA c61a44259d8f99ec621eee182a89e53a349640381332a9660330acb00aaff62b.

## 한계/다음 gate

실제 PG·SDK·SSH·HTTP·Docker·운영·private auth/runtime 접근0. author PG5/50pure 및 6fa 전체599를 독립 수치나 이 최종 HEAD의 전체 시험으로 합산하지 않았다. root 통합 b708 전체 회귀는 별도 결과를 기다린다.

정본 index는 concurrent 방식이 아니다. 설치에는 active writer/drain·강한 잠금·실제 디스크/WAL 증가량과 UNKNOWN commit 정산 gate가 필요하다. 현재 관련행 수 추정으로 운영 free가 충분하다고 주장하지 않는다. 인덱스 CPU/처리시간·초기 보존 backlog 완주·6시간 물리 삭제 SLA는 아직 운영 실증이 아니다. 소유 도구는 모두 terminal.
