# Weather103 최종 migration 시험 독립 closure A

판정 SOURCE_PASS / 열린 P0·P1·P2 없음.

대상08373c27a0eb8f3280386e51f9396695ca56c6b0는 이전 검토 d0a4312edc8de51372b214be9c75e064fb576d7c에서 tests/test_migration.py만18+/1-다. 변경 전체와 기존 시험 문맥을 읽고 실제 Git diff로 제품 코드 변경0을 확인했다. 통합 b7082e8→9f962848cfd8bb016a16ad4421f45067b169ac9e 역시 그 시험파일 하나만 달라 제품 전체 tree 내용은 그대로다.

기존 최신 migration 기대값0019를 이미 추가된0020_short_forecast_retention으로 정정한다. 새 partial index 존재/키순서 valid_at,known_at,value_id/정적4개 discriminator·valid_at NOT NULL/AND4·OR없음/pg_index indisvalid,indisready 모두True를 실제 PG 시험에서 요구한다. 운영 DB에 검토자가 실행하지 않았다.

독립 inert AST 검증: 실제 새 version assertion의20수용/19거절, index 정상수용·순서변경·누락discriminator·OR추가·NOTNULL누락·invalid·notready 거절7조건을 실행했다. version2와 source변경범위 등은 이미지 검토 묶음37조건에도 들어 있으므로 실제 PG7회라거나 전체 suite로 집계하지 않는다.

독립 model safe weather103-testonly-review-model-a-r1-20261016.safe.json SHA27dfe0fd562a9aadbc41d333abab4a79a4f0167ae259c838ffe5806222cb5d02. 기존 최종 제품 FULL/delta 원문 weather103-shortterm-retention-review-a-r3-20261016.md SHA1a12961e92bc352d0f261ebb715b73c84d71c4befc818fa7f3c0bd26cd8040a6를 계승한다.

root가 전달한 b708 전체620PASS1FAIL의 유일 oldversion 기대값 실패는 그 회차의 원문으로 유지하며, 이 test-only closure가 그 전체 회차를621PASS로 재분류하지 않는다. 새 exact focused/index 시험 결과·최종CI는 root 결과와 구분한다. DB/SDK/Docker/SSH/HTTP/운영/privateauth 접근0·제품 수정0·모든 소유 도구 terminal.
