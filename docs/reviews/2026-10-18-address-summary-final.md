# Weather 현재 원천 주소 확인 집계 최종 종결

상태: COMPLETE. **두 독립 FULL SOURCE PASS, OPEN P0/P1/P2=0.**
기준36c11950e1f5b6476f2c9b15396a097300a0e1e5 → 제품2bd77942a21c5b449347963488a38814d55f0990.
[PR113](https://github.com/digitie/kor-travel-weather/pull/113)은2026-10-18T03:54:48Z,
merge d1c034960612540b3629684ecb8e1e6fd6287b5d로 완료했다.
[기존111/112 종결](2026-10-18-address-refresh-final.md)을 보존하고 새 pin 수용과 구분한다.

Common40의522525a는 현재 source·동일 좌표만 정상 확인 시각으로 집계하며 merge27cd146으로 선행 완료됐다.
비활성·이동 이전 결과와 더 최근 orphan을 현재 확인일에서 숨기고 과거 sidecar를 보존한다.
같은 좌표에서 실패한 재시도의 마지막 정상일은 계속 표시한다. 원천 주소·KMA 격자·관리자 metadata는 불변이다.

A는 실제 모델6쌍 old→new, B는 모델11검사·pin/health2검사를 독립 수행했다.
같은 immutable manifest를 사용하고 새 상대 결과는 양쪽 원문 확정 전 비공개였다.
원문에 전달 요청·관찰 hash·격리·시각 수집 여부·fixture 준비 오류·미실행 한계를 보존했다.
수량은 서로 합산하지 않는다.

작성자 기존pin3FAIL→주소4PASS/health포함11PASS, 소유 PostgreSQL 전체661PASS/205.07초,
exactCI39094583346 Python661PASS/213.48초·lint/migration/OpenAPI·Admin211/type/build SUCCESS다.
새 API/native fixture 및 byte 동일 compiled UI의 실브라우저13개(두 viewport 합)가 통과했다.
provider는 주입 fixture이며 소유 queued2 취소·5개 프로세스 그룹 종료·Daemon 미기동을 확인했다.
최초 pin assertion/Dagster manifest 불일치 실패는 evidence에 그대로 남겼다.

운영 migration/provider 실호출/배포·운영 UI·CPU/RSS는 이번 검증에서 NOT_RUN이다.
Transport155의 별도 최종 CI/리뷰를 이 Weather 판정으로 대신하지 않는다.
Track1·2 전체 완료 후 수집기 CPU/RSS 개선, Daemon OFF 방침을 유지한다.
이 문서 PR은 제품 source/test/lock을 바꾸지 않으며 사용자 지시로 CI를 생략한다.

## 원문과 무결성

| 원문 | SHA256 |
| --- | --- |
| [2026-10-18-address-summary-reviewer-a.md](evidence/2026-10-18-address-summary-reviewer-a.md) | 7e04db7efa44d61b9456f1adbb684db012ca294443d9aac212a6261bff453883 |
| [2026-10-18-address-summary-reviewer-b.md](evidence/2026-10-18-address-summary-reviewer-b.md) | 74838ca645f1394b7d83d0e802a590a66c1bab856b3987c9fa994b91d2e9cdcb |
| [2026-10-18-address-summary-manifest.json](evidence/2026-10-18-address-summary-manifest.json) | 9215b8d655e363579c004e8f2c9981ca31c68b51e56cfc0a253d440c1f9d8ac1 |
| [2026-10-18-address-summary-consumer-verification.json](evidence/2026-10-18-address-summary-consumer-verification.json) | 31892dbe8c2348052e902438840c5c4028e94fd4ff2e24172015e34579074d1a |
| [2026-10-18-address-summary-merge-receipt.json](evidence/2026-10-18-address-summary-merge-receipt.json) | f4b654acb2a8b0af2f4355524a210a47ed3c14bad2ef9176e4e4c140da1aac78 |
| [2026-10-18-address-summary-source-ci.json](evidence/2026-10-18-address-summary-source-ci.json) | 1cfc92b46ae95cb8449a950b1c02321646cbdd66679c71a35f07056503de0683 |
