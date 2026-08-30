# Architecture decision records

원본 `kor-travel-map`의 ADR 목록/번호 체계를 유지하되, 지도 전용 책임은
weather source 경계에 들여오지 않는다. 구현에 직접 적용되는 정본은 다음과 같다.

| ADR | 적용 내용 |
| --- | --- |
| 020 | API/backend와 admin frontend 분리 |
| 048 | `/v1`, `{data,meta}`, request id, pagination |
| 062 | consumer-independent weather history |
| 072 | known/target bitemporal semantics |
| 074 | token, immutable facts, disabled instead of delete |
| 089 | current projection와 source revision identity |
