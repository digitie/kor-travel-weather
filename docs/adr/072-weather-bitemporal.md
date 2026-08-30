# ADR-072: weather bitemporal

`known_at`은 provider response를 받은 시각이고 `target_at`은 해당 값이
가리키는 관측/예보 시각이다. `known_at`을 fact identity에 넣지 않는다. 같은
응답 replay는 같은 source revision이고, 수정 응답은 새로운
`source_record_key`로 append된다. public current projection은 target별 가장
최근 known/source revision 하나를 선택하며, explicit history/as-of query만
revision 전체를 반환한다.
