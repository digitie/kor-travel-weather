# AirQualityMeasurement 원문 재해석

```python
from airkorea import parse_measurement

measurement = parse_measurement(saved_row, station_name="종로구")
```

`row`는 provider 단일 행인 `Mapping[str, Any]`다. 원문에 stationName이 없을 때만
명시한 station_name을 사용한다. 결측은0 대신None, dataTime은 기존 KST 규칙,
알 수 없는 필드는raw에 보존한다. 기존 행 파서의 AirKoreaParseError 의미를 유지한다.

## 원문 재해석의 신뢰 경계

이 API는 이미 확보한 provider record를 같은 SDK DTO로 다시 해석하는 로컬 함수다.
네트워크·인증키·현재 시각·원천 재조회 없이 기존 파서와 같은 변환을 사용한다.
HTTP header/resultCode, 전체 items 형태·byte/count 상한, 전국 집합의 완결성·신선도는
이 단일 행/장소만으로 검증하지 않는다. 호출자는 원문 확보 때의 응답 검증·출처·수집 시각과
대상 identity를 따로 보존해야 하며, 빈 결과를 전국 정상0건의 근거로 사용하면 안 된다.
