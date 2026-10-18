# Weather 주소 갱신 독립 리뷰 r1·r2 원문 보존

상태: POST_FIX_REVIEW. r2 **A REQUEST_CHANGES / B SOURCE PASS**로 새 A-P2-03이 남았던 판정을 보존한다.
최종 수정본의 두 독립 재검토는 별도다. 두 실행 ID는 /root/review_finish_a(Frontend/live UI)와
/root/review_finish_b(Backend/DB/ops)이며 서로의 새 결과를 원문 확정 전에 공유하지 않았다.

| 실행 | 기준 head | 당시 판정·finding |
| --- | --- | --- |
| r1 | c52e08b5a5d7401fc600ddd4a378f327f6395f60 | A REQUEST_CHANGES: W111-A-P2-01 동시 제출·W111-A-P2-02 실패 replay 안내; B CONDITIONAL: W111-B-P2-01 auto off 수동 실패 복구·W111-B-P2-02 동시 제출 |
| r2 | 78817516561968868bb7a77c6cb17c6b90cefee5 | 기존 지적은 명시된 단일 Node 프로세스 범위/공통 c067에서 CLOSED. A는 늦은 GET200이 POST 실패를 지우는 W111-A-P2-03 P2를 새로 재현. B의 독립 검증에서는 새 OPEN0 |

두 원문을 확정한 뒤 통합했다. 실제 source/fixture 판정을 운영·실제 provider·PG 행 경합·브라우저 판정으로 확대하지 않는다.
r1 시작/전달 입력 byte 원문을 작성자가 사전 별도 보존하지 못한 한계는 그대로 남긴다.
r2는 두 원문 안에 실제 전달요청이 포함됐다. 정확 시각·격리·미실행 항목은 각 원문을 따른다.
원본 내용은 수정하지 않고 byte 그대로 복사해 hash를 대조했다.

| 실행 | 원본 | SHA256 |
| --- | --- | --- |
| r1 a | [2026-10-18-address-refresh-r1-reviewer-a.md](evidence/2026-10-18-address-refresh-r1-reviewer-a.md) | c8c1b75ba6fc9ffe3aee93d82c7afd42fbc380024da2ab344a6266a7b3a566e6 |
| r1 b | [2026-10-18-address-refresh-r1-reviewer-b.md](evidence/2026-10-18-address-refresh-r1-reviewer-b.md) | c745706727e97ac2f46f15d4c2217a39fe33af03957c0ef7c6cc9fb6f8c095da |
| r1 manifest | [2026-10-18-address-refresh-r1-manifest.json](evidence/2026-10-18-address-refresh-r1-manifest.json) | 2c1d031e4aed34a14a88be9a29124963c72b44fc6a8a1c784370aab90d60b469 |
| r2 a | [2026-10-18-address-refresh-r2-reviewer-a.md](evidence/2026-10-18-address-refresh-r2-reviewer-a.md) | 54d71d21262741724532bcc1e5a8bff46eadb8087b948174e37a88d1504cd0c6 |
| r2 b | [2026-10-18-address-refresh-r2-reviewer-b.md](evidence/2026-10-18-address-refresh-r2-reviewer-b.md) | b086d1d6208a058b24ccd6876d39c60337bbc8a6d4816ad2b80be07119b9efec |
| r2 manifest | [2026-10-18-address-refresh-r2-manifest.json](evidence/2026-10-18-address-refresh-r2-manifest.json) | bfc6246e4361a371761ac28810e7aced666f6676e664fd8ce5afcad51bd1d3a0 |

## 수정과 작성자 검증

수동 실패는 Common c067의 영속 cycle/backoff/eligible cursor로 재개한다.
BFF 제출 가드는 단일 Node 프로세스에 한정하고 replica 간 exactly-once를 주장하지 않는다.
이전 실행의 FAILURE/CANCELED/SUCCESS를 새 예약과 구분한다.

추가 A-P2-03은 두 앱의 desktop/mobile 실브라우저에서 4 FAIL로 재현한 뒤
loadError/launchError 상태를 분리했다. 같은 반례를 포함한 앱별13개/합계26개 UI가 통과했다.
로그인/API/네이티브 Dagster 큐는 실제 소유 loopback fixture를 쓰고 provider만 주입했다.
운영 UI·provider/키·migration·배포·Daemon 기동은 NOT_RUN이다. 운영 OFF 정책을 바꾸지 않았다.
수정 후 type-check/Vitest211/운영 빌드도 양쪽 통과했다. 새 SHA의 최종 독립 리뷰/CI는 별도다.
EOF 새 빈 줄은 정리해 base→head diff-check를 다시 확인한다.
