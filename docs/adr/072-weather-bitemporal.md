# ADR-072: weather bitemporal

`known_at`은 provider response를 받은 시각이고 `target_at`은 해당 값이
가리키는 관측/예보 시각이다. `known_at`을 fact identity에 넣지 않는다. 같은
응답 replay는 같은 source revision이고, 수정 응답은 새로운
`source_record_key`로 append된다. public current projection은 target별 가장
최근 known/source revision 하나를 선택하며, explicit history/as-of query만
revision 전체를 반환한다.

## 예외: 정리된 KMA 단기예보의 재입장

[ADR-105](105-kma-short-forecast-retention.md)로 정리된 구간에 보존할 단기예보가 다시 들어오고
원 수신 시각을 저장할 partition이 없을 때에만 실제 재입장 시각을 `known_at`으로 쓴다. 원
시각은 source의 `fetched_at`에 남고 `source_record_key`·raw payload는 바뀌지 않는다. 일반
provider의 `known_at` 정의는 바뀌지 않는다. 판정과 행 재사용 규칙은
[`../shortterm-retention.md`](../architecture/shortterm-retention.md) "늦은 미래 응답의 재입장"이 정본이다.
