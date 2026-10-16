# Weather103 단기예보 보존 독립 리뷰 closure

최종 제품 d0a4312edc8de51372b214be9c75e064fb576d7c 및 시험 정정
08373c27a0eb8f3280386e51f9396695ca56c6b0에 대한 A·D 독립 검토는
SOURCE_PASS/열린 P0·P1·P2 0이다. 최초 f64의 두 독립 P2는 같은
known_at gap의 미래값 입장 누락이었으며 6fa에서 폐쇄했다. 원문 실패는 보존한다.

최초 FULL → 미래 재입장 → timeout/인덱스 → migration 시험 정정의
전체 계보를 아래 원문으로 결박한다. 각 실제 시험과 한계는 원문에 따른다.

| 원문 | SHA256 |
| --- | --- |
| [a-full](evidence/2026-10-16-weather-shortterm-retention-a-full.md) | aa0caac3689bc56153b2f3ba8d9f1d511398375b1f26f3b6f685278976208404 |
| [a-future](evidence/2026-10-16-weather-shortterm-retention-a-future.md) | b26f86a5bfbe1b8a317079440c0d2025cabbe2efdb24a28c618118d17e20b5c3 |
| [a-index](evidence/2026-10-16-weather-shortterm-retention-a-index.md) | 1a12961e92bc352d0f261ebb715b73c84d71c4befc818fa7f3c0bd26cd8040a6 |
| [a-test](evidence/2026-10-16-weather-shortterm-retention-a-test.md) | 43f8d3d040f8a29b15cd6381a8d6bcc438708378da19d8fd10eadedb80b08b65 |
| [d-full](evidence/2026-10-16-weather-shortterm-retention-d-full.md) | 84dc88513da34b47ff78b117d7cffeb9ec6809a63fc5089e9c45ea76c257ec85 |
| [d-future](evidence/2026-10-16-weather-shortterm-retention-d-future.md) | 05962387194528e8a00c5cc1b32017bc2555af93abbf1d1ec3a8ab0d5e081b83 |
| [d-index](evidence/2026-10-16-weather-shortterm-retention-d-index.md) | 824f8cb3c3af7007a9f99781bd3be0caec4989fa20c2293ba5e739bb042b4b8c |
| [d-test](evidence/2026-10-16-weather-shortterm-retention-d-test.md) | b2ecc370738d272a2363ba3c214ef0c4c8b16432409c917cd9a3dfcd046d9d6c |

원문은 byte 그대로 복사했고 운영 설정·자격값은 포함하지 않는다. 이 아카이브
커밋은 제품 코드를 바꾸지 않는다.

검증 이력:
- 선행 6fa 전체599통과.
- 최종 제품을 합친 b708 통합 전체620통과/1실패(0019를 기대한 기존 시험).
  이 원 회차를621통과로 바꾸어 기록하지 않는다.
- 기대값0020·새 index 실제 계약 검증을 반영한9f 통합의 실패 시험 및 index6개
  실제 PostgreSQL 통과, Ruff통과. 프런트 type-check/196통과.
- 08373의 GitHub Python·프런트 성공 확인. 통합9f Python CI는 확인 진행 중.

운영의 실제 index 설치·잠금/WAL/공간 예산, 초기 과거 데이터 정리와 미래값
전후 보존, 10분 주기 실제 실행 및 이미지/UI/CPU 효과는 별도 운영 gate다.
이 소스 승인은 해당 운영 성공을 대신하지 않는다.
