# 디버그 UI fixture 설계

이 문서는 Streamlit 같은 별도 Web UI에서 `airkorea`를 실행해 본 뒤, 의미 있는 응답을 pytest replay fixture로 저장하는 기준입니다. 라이브러리 본체는 Streamlit에 의존하지 않고, UI는 wheel 또는 editable install된 `airkorea`를 import해서 사용합니다.

## 역할 분리

| 영역 | 책임 |
|---|---|
| `airkorea` | API 호출, Pydantic 모델 변환, 디버그 실행 결과와 fixture 저장 helper 제공 |
| 외부 Web UI | 함수 선택, 입력 폼, 실행 결과 표시, fixture 저장 버튼 제공 |
| `tests/test_generated_fixtures.py` | `tests/fixtures/**/*.json`을 자동으로 읽어 replay 회귀 테스트 수행 |

Web UI는 테스트 코드를 생성하지 않습니다. 저장된 fixture JSON이 테스트 자산이고, pytest는 공통 runner로 이 파일들을 실행합니다.

## 디버그 실행

외부 UI는 `run_debug_method()`로 public method를 실행할 수 있습니다.

```python
from airkorea import AirKoreaClient, run_debug_method

air = AirKoreaClient()
debug_run = run_debug_method(
    air,
    "station_measurements",
    {"station_name": "종로구", "num_of_rows": 1},
)

debug_run.request    # 인증키가 제거된 요청 정보
debug_run.response   # 저장 가능한 response.body
debug_run.parsed     # Pydantic 모델 또는 모델 목록
debug_run.processed  # call()은 AirKoreaPage.items를 편 list, 그 외는 parsed와 동일
debug_run.catalog    # 데이터셋명, endpoint, 서비스키 링크
debug_run.error      # 검증 오류나 예외 정보
debug_run.trace      # HTTP 호출/trace 요약
```

Streamlit의 Debug Trace 탭에서는 다음처럼 선택된 API 카탈로그와 trace를 같이 보여줍니다.

```python
st.dataframe(debug_run.catalog)
st.code("\n".join(debug_run.trace))
```

`catalog` 항목에는 `dataset_name`, `endpoint`, `method_names`, `service_key_param`, `service_key_url`, `portal_url`이 포함됩니다. 사용자가 API를 선택하면 `service_key_url`을 링크로 표시해 해당 공공데이터포털 데이터셋에서 서비스키를 신청하거나 확인할 수 있게 합니다.

`measurement_near()`처럼 내부에서 여러 API를 호출하는 method도 실행할 수 있지만, fixture의 대표 `request`/`response`에는 마지막 HTTP 교환이 들어갑니다. 여러 응답을 하나의 케이스로 replay해야 하면 fixture에 `responses` 배열을 두어 순서대로 fake session에 넣습니다.

## Fixture 저장

`save_debug_fixture()`는 기본적으로 같은 파일명을 덮어쓰지 않습니다.

```python
from airkorea import save_debug_fixture

path = save_debug_fixture(
    base_dir="tests/fixtures",
    case_name="jongno_normal",
    description="종로구 측정소 실시간 정상 응답",
    debug_run=debug_run,
    library_version="0.4.0",
)
```

저장 위치는 `tests/fixtures/{function}/{case}.json`입니다. `case_name`은 파일명에 안전한 slug로 바뀌며, `function` 값은 원래 public method 이름을 유지합니다.

## Fixture 형식

```json
{
  "name": "jongno_normal",
  "function": "station_measurements",
  "description": "종로구 측정소 실시간 정상 응답",
  "input": {
    "station_name": "종로구",
    "num_of_rows": 1
  },
  "request": {
    "method": "GET",
    "url": "http://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty",
    "query": {
      "returnType": "json",
      "stationName": "종로구",
      "numOfRows": 1
    },
    "headers": {}
  },
  "response": {
    "status_code": 200,
    "headers": {
      "content-type": "application/json"
    },
    "body": {
      "items": []
    }
  },
  "catalog": [
    {
      "dataset_name": "한국환경공단_에어코리아_대기오염정보",
      "service_label": "대기오염정보",
      "service_name": "ArpltnInforInqireSvc",
      "endpoint": "getMsrstnAcctoRltmMesureDnsty",
      "method_names": ["station_measurements", "latest_station_measurement", "measurement_near"],
      "service_key_param": "serviceKey",
      "service_key_url": "https://www.data.go.kr/data/15073861/openapi.do",
      "portal_url": "https://www.data.go.kr/data/15073861/openapi.do"
    }
  ],
  "parsed": [],
  "processed": [],
  "assertion": {
    "mode": "snapshot",
    "exclude_fields": ["fetched_at", "request_id", "updated_at", "collected_at"],
    "required_fields": []
  },
  "meta": {
    "created_at": "2026-05-15T20:30:00+09:00",
    "library_version": "0.4.0",
    "source": "debug_ui"
  }
}
```

`response.body`는 AirKorea JSON envelope의 `response.body` 부분입니다. replay runner는 이 값을 정상 `resultCode` envelope로 감싸 `FakeSession`에 넣습니다. 오류 응답을 fixture화할 때는 `response.header` 또는 `response.body.response` 전체 envelope를 저장하는 방식으로 확장합니다.

## 민감정보 처리

fixture 저장 전 `input`, `request`, `response`, `parsed`, `processed`는 모두 `redact_sensitive()`를 통과합니다.

- `serviceKey`, `ServiceKey`, `api_key`, `apikey`, `Authorization`, `x-api-key`, `access_token`, `refresh_token`은 `<REDACTED>`로 치환합니다.
- `AirKoreaCallContext.request_params`와 HTTP debug exchange는 인증키성 request param을 저장하지 않습니다.
- 실제 `DATA_GO_KR_SERVICE_KEY`나 원본 인증키를 fixture, 로그, 문서에 남기지 않습니다.

## Assertion mode

| Mode | 의미 | 상태 |
|---|---|---|
| `snapshot` | replay 결과와 `processed` 전체를 비교하고 `exclude_fields`를 제외 | 기본 |
| `schema_only` | public method가 예외 없이 모델 변환에 성공했는지만 확인 | 지원 |
| `required_fields` | 지정한 필드 path가 결과에 있는지 확인 | 지원 |
| `count` | list 길이 또는 `count` 값을 비교 | 지원 |

기본 fixture replay는 실제 AirKorea 네트워크를 호출하지 않습니다. 실제 API 호출 검증이 필요하면 `@pytest.mark.integration` 테스트로 분리합니다.
