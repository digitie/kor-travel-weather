# Map 예보 배열 API

## 계약

`GET /v1/service/exports/forecasts?style=ultra_short|short&provider=python-kma-api`
를 기존 `x-admin-token` 서비스 인증으로 제공한다. 기본 OFF이며 모든 활성 위치의
승인된 원천이 없으면 첫 성공 집합을 만들지 않는다. mid/external은 아직 503이다.
기존 current/이력/통계 API와 fact의 값·시각은 변경하지 않는다.

위치×provider당 한 항목을 반환한다. `target_at[]`과 각 `metrics[key][]`는 같은
길이이며 metric이 없는 시각은 null이다. 현재 이후의 대상 시각만 내보낸다.
`known_at`은 원본 값의 시각이며 `published_at`은 승인 집합이 공개된 시각이다.

`since`는 **publication watermark**다. 응답 `meta.next_since`를 다음 조회에 사용한다.
원본 known_at을 watermark로 사용하면 느린 수집이 뒤늦게 commit될 때 누락되므로
공개 시각과 분리한다. 모든 페이지를 받은 뒤에만 next_since를 저장해야 한다.
`since`를 지정한 정상 빈 델타는 변경이 없다는 의미이고, 원천 미준비/빈 실패를
성공으로 바꾼 것이 아니다. 같은 bytes가 재승격되면 원본 known_at은 보존되며
새 publication으로 재전달할 수 있다. 소비자는 위치×provider×style로 upsert한다.

기존 publication clock 한 행을 최종 head metadata TX에서만 잠깐 잠근다.
동일 시각·시계 역행에도 1 microsecond씩 증가시키며, RR reader는 동일 snapshot의
head vector와 마지막 공개 clock을 함께 고정한다. 파일 검증/DB fact 저장 중에는
이 clock 잠금을 보유하지 않는다. 따라서 늦게 commit된 자료도 다음 델타에 포함된다.

## 비용과 오류

- 이력 날짜 파티션을 읽지 않는다. 승인 generation의 위치별 chunk index로
  필요한 파일만 읽고 한 번에 최대 5,000 point의 chunk 하나를 유지한다.
- 위치는 chunk에 나뉘지 않는다. REST 페이지는 최대 5,000개 위치 및 4 MiB다.
- vector는 cursor 동안 고정한다. 과거 예보를 반환하지 않기 위해 시간창은
  매 요청 `as_of`로 잘라내므로 오래 걸린 페이지 탐색 중 대상 배열은 짧아질 수 있다.
- cursor는 style/since/limit에 묶인다. 변조·조건 변경은 422, 15분 만료는 410이다.
- 누락·손상 artifact는 503이다. 새 원천 미준비는 이전 승인 vector를 stale로
  제공하되, 그 vector에도 미래 예보가 없으면 503이다.
- 약한 ETag, gzip, 권한 거부를 제공한다. 운영 자동활성화는 하지 않는다.

API·code-server·publisher는 동일한 NVMe artifact 디렉터리를 같은 권한으로
마운트해야 한다. eMMC에 별도 artifact 복사본을 만들지 않는다. cursor TTL 동안
승인 generation 파일을 지우지 않도록 `snapshot_refs`/pending pin을 GC 보존
루트로 취급해야 한다. 현재 GC 구현과 운영 최대 비용 검증은 미완료이므로 이
PR만으로 자동 ON 또는 운영 배포 완료로 판단하면 안 된다.

## 남은 범위

중기/외부 제공처, W2의 모든 provider, W4 status 및 W5 미리 계산한 일간 요약은
후속 구현이다. 운영 배포 전 최대 wall-clock/RSS/GC 보존 측정과 두 독립 리뷰가 필요하다.
