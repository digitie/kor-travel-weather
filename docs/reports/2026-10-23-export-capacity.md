# 승인 source vector의 제한된 용량 확대

기준 PR162 `f50a4584f28aa7f796ce404c16da38576812ec8f`에서 시작한 구현 초안이다.
운영 catalog 읽기 전용 조사에서1,450곳·1,076격자·Air672/산악454/청정넷67/KREX187/
KHOA49를 확인했다. 실제 export metadata는 운영에 아직 없으며 namespace 실측은 아니다.
외부3종의 회전이 전체 위치에 도달한다는 조건에서 current의 namespace 잠재량은
6,806개다. 따라서 기존 vector5,000과 catalog/page5,000의 목적을 분리한다.

## 구현 계획

- source vector만8,192개로 제한한다. catalog/page·개별 provider/dataset cohort의
  5,000 상한과 요청별 inventory20,000행 상한을 유지한다.
- current/forecast freeze의 refs는 server cursor로64행씩 읽고 전체 ref 행 수와
  metadata bytes를 누적 제한한다. JSON field도 전송 전 SQL projection에서 제한한다.
- 전체 manifest는 기존 JSON 정규화 표현과 같은 기준으로8MiB를 넘으면 거절한다.
  per-chunk8MiB/5,000points, cursor/TTL/ETag·이전 vector stale·reader2개·RR 및
  파일 IO 중 DB 연결 반환 계약은 유지한다.
- source 건강/W5 fallback·실제 GC/unlink·schema·운영/원천 호출은 변경하지 않는다.

실제 PG 스트리밍·한계 초과 fail-closed·6,806개 대표 shape 및 허용 경계의
tracemalloc/metadata bytes/query 수/시간은 구현 뒤 별도로 기록한다. 두 독립 리뷰·
정확 CI와 운영 ARM 비용 수용 전에는 성능 개선 완료로 세지 않는다.
