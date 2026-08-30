# 테스트 원칙

`airkorea`의 기본 테스트는 네트워크 없이 항상 재현 가능해야 합니다.

## 기본 명령

```bash
python -m compileall src/airkorea tests
python -m pytest
python -m pytest --cov=airkorea --cov-fail-under=90
python -m ruff check .
python -m mypy src/airkorea
```

## 단위 테스트 기준

- 모든 public method는 endpoint URL과 주요 파라미터를 검증한다.
- 응답 parser는 list, 단일 dict, `items.item` 형태를 모두 처리해야 한다.
- 결측값 `-`, 빈 문자열, `null`은 `None`으로 변환한다.
- 숫자 문자열, 날짜 문자열, KST 시각 문자열을 타입으로 변환한다.
- 신규 API는 확인된 필드만 파싱하고 `raw` 보존을 전제로 한다.
- 서비스 목록은 `SUPPORTED_ENDPOINTS`와 `tests/test_expanded_api.py`가 서로 맞아야 한다.
- API 카탈로그는 `SUPPORTED_ENDPOINTS` 전체를 덮어야 하며 데이터셋명과 서비스키 링크를 포함해야 한다.
- raw `call()`/`iter_pages()`는 인증키성 params를 실제 요청과 context에서 제거하고, `pageNo` 순회를 fixture로 검증한다.
- 비동기 클라이언트는 실제 네트워크 없이 fake async session으로 동기 클라이언트와 같은 파라미터/모델/페이지 순회 결과를 검증한다.
- public API를 새로 추가하면 `tests/test_public_api.py`의 권장 export 목록도 함께 갱신한다.

## 디버그 fixture replay

디버그 Web UI나 수동 조사 중 저장한 JSON fixture는 `tests/fixtures/{function}/{case}.json`에 둡니다. `tests/test_generated_fixtures.py`는 이 파일들을 자동으로 읽고, `tests/runners.py`의 공통 runner가 저장된 `response.body`를 `FakeSession`에 넣어 public method를 다시 실행합니다.

- fixture는 실제 AirKorea 네트워크를 호출하지 않는다.
- 테스트 코드를 케이스마다 생성하지 않는다.
- fixture 저장 전 `serviceKey`, `ServiceKey`, `Authorization`, `x-api-key`, token류 값은 제거하거나 `<REDACTED>`로 치환한다.
- 기본 assertion mode는 `snapshot`이며, 변동 필드는 `assertion.exclude_fields`로 제외한다.
- `schema_only`, `required_fields`, `count`는 좁은 회귀 검증이 필요할 때 사용한다.

fixture 형식과 Web UI 연동 방식은 `docs/debug-fixtures.md`에 정리합니다.

## Live API 테스트 기준

실제 AirKorea 호출 테스트를 추가할 때는 다음 조건을 지킵니다.

- `@pytest.mark.integration`을 붙인다.
- `DATA_GO_KR_SERVICE_KEY`가 없으면 skip한다.
- 기본 테스트 경로에서 네트워크를 쓰지 않도록 live smoke test는 `AIRKOREA_RUN_LIVE=1`을 함께 요구한다.
- 특정 농도값이나 문구를 고정 assert하지 않는다.
- 응답이 비어 있을 수 있는 API는 empty list를 실패로 보지 않는다.
- rate limit과 포털 장애를 고려해 CI 기본 경로에서는 실행하지 않는다.

```bash
AIRKOREA_RUN_LIVE=1 DATA_GO_KR_SERVICE_KEY=<decoded service key> python -m pytest tests/test_live_api.py
```
