# Weather 문서 안내

문서는 무엇을 기록하느냐에 따라 위치가 정해진다. 같은 내용을 두 곳에 쓰지 않는다.

| 위치 | 담는 것 | 고치는 방식 |
| --- | --- | --- |
| [`adr/`](adr/README.md) | 되돌리기 비싸거나, 다른 저장소를 제약하거나, 대안을 버리고 고른 결정 | 채택 뒤에는 새 ADR로 대체한다 |
| [`architecture/`](architecture/) | 지금 동작하는 구조·계약·운영 경계. API 설명은 [`architecture/weather-api.md`](architecture/weather-api.md) 하나다 | 코드가 바뀌면 함께 고친다 |
| `packages/kor-travel-weather-api/openapi.json` | API 필드와 DTO 형식의 정본 | `scripts/export_openapi.py`로 생성한다 |
| [`design/`](design/), [`execplans/`](execplans/) | 진행 중이거나 구현 전 설계, 실행 계획 | 구현이 끝나면 현재 동작을 `architecture/`에 옮긴다 |
| [`runbooks/`](runbooks/README.md) | 배포·복구 절차 | 절차가 바뀌면 고친다 |
| [`integration-map.md`](integration-map.md) | Map·PinVi·Manager와의 호출 관계 | 연계가 바뀌면 고친다 |
| [`tasks.md`](tasks.md), [`resume.md`](resume.md), [`journal.md`](journal.md) | 남은 작업과 인수인계 | 작업마다 갱신한다 |
| [`reports/`](reports/README.md), [`reviews/`](reviews/README.md), [`audits/`](audits/README.md) | 측정·리뷰·감사 결과 | 시점 기록이다. 고치지 않는다 |
| [`archive/`](archive/README.md) | 지난 변경 단위 메모 | 고치지 않는다. 새로 추가하지 않는다 |

변경 하나의 이유(성능 개선, 버그 수정, 상수 조정)는 문서를 새로 만들지 않고 PR 본문과 커밋
메시지에 쓴다. 그 결과 바뀐 현재 동작만 `architecture/`의 해당 주제 문서에 반영한다.
