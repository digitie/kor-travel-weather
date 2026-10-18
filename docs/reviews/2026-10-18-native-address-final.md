# 주소 갱신의 시간대·응답 유실 재시도 후속 종결

**두 독립 SOURCE PASS, 열린 P0/P1/P2=0.**
base332cd1f2fc74dd263473ea57ebc2b0594265f883 →
최종 제품7f549f1257089c7c05d6ee63b412ca8a1bc00c67,
Common89291f36d731ab906161b96757522e1cd833b4ef를 검토했다.
Common41은 f9148734b59023cec91ce8fdd44fad6f72d68322로 머지됐다.

UTC/미국 서부 브라우저의 날짜 표시 오류 4건을 재현하고 Asia/Seoul로 고정했다.
첫 예약이 BFF 제한시간 뒤 도착하는 경우, 같은 UUID의 native 실행을 재사용한다.
NOT_STARTED는 제출을 복구하며, 새 mutation 미지원 서버에서 무작위 실행으로 우회하지 않는다.
Common의 동시 제출·잠금 연결 유실·SQL 제한시간 반례를 수정한 최종 버전에 연결했다.

B는 r4 전체15파일과 r5 pin delta를, C도 같은 불변 기준을 독립 검토했다.
새 상대 원문은 두 판정이 고정될 때까지 공유하지 않았다.
r4의 공통 timeout 의존성 HOLD를 보존하고 r5에서 직접 PG closure·pin 정합으로 닫았다.
이전 Weather111/113의 리뷰를 이번 후속 판정으로 대신하지 않는다.

최종 pin의 root 집중12개, 실제 loopback API/native Dagster 연동 Chromium UI17개와
12초 지연 후 재시도1개가 통과했다. 단위214개·lint·type·build 및 Python/OpenAPI를
포함한 제품7f549의 CI 두 job 모두 SUCCESS다.
이전 a68의 전체661개 통과는 그 버전 결과로 보존하며 새 pin 전체 결과는 CI에 귀속한다.
초기 UI 실행은 opt-in 누락으로17 SKIP였고, 설정을 고친 r2 및 최종892에서17 PASS다.
skip을 성공으로 집계하지 않는다.

운영 배포·실 provider·운영 migration·외부 UI·수집 CPU/RSS는 NOT_RUN이다.
공용 webserver를 먼저 새 mutation 지원 버전으로 배포한 뒤 Admin을 배포한다.
Common38 daemon-pool을 포함한 whole wheel·Manager 채택을 별도로 검증하고 daemon OFF를 유지한다.
원천 주소·좌표·KMA 격자·weather fact를 바꾸지 않는다.
이 후속은 원문과 정산만 보존하므로 사용자 요청대로 문서 CI를 생략한다.

## 원문·검증 무결성

| 파일 | SHA256 |
| --- | --- |
| [2026-10-18-native-address-r4-reviewer-b.md](evidence/2026-10-18-native-address-r4-reviewer-b.md) | 425fb9dffed12fce24532121c5a325e1794900ab22ce2729bc47f41cc814c2d3 |
| [2026-10-18-native-address-r5-reviewer-b.md](evidence/2026-10-18-native-address-r5-reviewer-b.md) | f2c2f9ebb74c13601982adcf0b015f9188081d2f7efb32c631cbbaa65dfdf30d |
| [2026-10-18-native-address-r4-reviewer-c.md](evidence/2026-10-18-native-address-r4-reviewer-c.md) | cfd42192d708379bc6229cd593803cf4bbd137a4a9123cfab67d5fcf45c4714f |
| [2026-10-18-native-address-r5-reviewer-c.md](evidence/2026-10-18-native-address-r5-reviewer-c.md) | 5cad6e4b5e5e93cfac042f62dbd6616ea547d04c431eb321a415775c6adc630e |
| [2026-10-18-native-address-r4-manifest.json](evidence/2026-10-18-native-address-r4-manifest.json) | 2af750b4d7690cd4ba9865625fd6b78ec03e413a3b5b046a7970a5b690475093 |
| [2026-10-18-native-address-r5-delta.json](evidence/2026-10-18-native-address-r5-delta.json) | d7365c30e49a34a46199ed4a3ae8d348247d5af1d31f91502faf17c7c8c825e8 |
| [2026-10-18-native-address-native-validation.json](evidence/2026-10-18-native-address-native-validation.json) | 1cfe4ca8f9d80a6c161b30c2a1b5951142809f1a6143549f58ab9469cf5a5157 |
| [2026-10-18-native-address-source-ci.json](evidence/2026-10-18-native-address-source-ci.json) | a2066189704568853f6300d8b61581f8e6c187dfe95f1aa735a9d331ffd6e977 |
