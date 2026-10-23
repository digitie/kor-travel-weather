# 중기예보 지역 원천 재해석 증거

## 범위와 계약

`export_mid.py`는 저장된 중기 지역 원문 1행을 공개 `python-kma-api`의
`MidForecastItem`으로 복원하고 기존 Weather 중기 정규화를 재사용한다.
이 변경만으로 실제 수집 배선, head 승격, REST 응답의 중기 지원을 완료하지 않는다.
기존 수집·fact·이력·API DTO·운영 flag를 변경하지 않는다.

- scope는 provider·dataset·지역·land/temperature·정렬된 위치 목록(최대 50곳)·
  정규화/선택 정책으로 고정한다. 발표 시각은 namespace가 아닌 data revision이다.
- source record의 기존 canonical hash, 원천 identity, SDK endpoint·요청 지역·첫
  페이지·행 수·발표 회차와 timezone-aware fetch 시각을 확인한다.
- 발표 회차와 공식 가용 시각은 SDK 공개 `parse_kma_datetime`, `base_available_at`,
  `latest_mid_fcst_time`으로 검증한다. 자체 격자/발표 시간표를 만들지 않는다.
- SDK가 요청 문맥으로 제공한 `tmFc`를 사용한다. 원문에 없는 발표 시각을 원문
  필드라고 표시하지 않는다. 원문이 회차를 제공하면 요청과 같아야 한다.
- land는 기존 정규화 기준 13구간의 하늘 상태·강수확률, temperature는 8일의
  최저·최고기온 전체가 있어야 한다. 누락·중복·잘못된 숫자·비정상 확률·역전 기온은
  거절한다. SKY 문자열을 숫자로 축약하지 않는다.
- fetch는 공식 가용 시각 이후이고 발표 후 18시간 이내여야 한다. 원본 이력에는
  이 제한을 적용하지 않으며 export 승인 증거에만 사용한다.

현재 SDK의 중기 단건 메서드는 pagination receipt를 만들지 않는다. 따라서 이
모듈은 **한 지역 응답의 완결성**만 증명하며 전국 집합 또는 서버 페이지 완결을
주장하지 않는다. 선택적 receipt가 있으면 단일 전체 행과 일치해야 한다.

## 후속 배선 조건

1. HTTP 전에 실제 선택된 지역과 위치의 cohort를 고정한다. land와 temperature는
   독립 source part로 보관한다. quota 바깥의 이전 승인 위치를 잃지 않아야 한다.
2. 한 실행에서 SDK의 최신 중기 회차를 한 번 선택하고 두 메서드에 같은 `tm_fc`를
   명시한다. 같은 land 지역을 여러 온도 지역이 공유할 때 원문을 한 번 수집한 뒤
   위치로 fan-out한다. 공유 source scope의 중복 claim을 만들지 않는다.
3. 두 part의 발표 회차가 같은 경우에만 한 location의 W3 묶음으로 조합한다.
   불일치·실패·급감 시 이전 승인 집합을 stale로 유지하고 정상304를 반환하지 않는다.
4. head dispatcher/승격·inventory·frozen REST에서 실제 조합을 검증하고 기존 기본
   OFF, 인증, ETag, 페이지네이션 계약을 유지한다.
5. 운영 rollout 전 실제 ARM 비용·artifact 보존/GC·Map 소비자 수용을 확인한다.

## 검증

- 공개 `DataGoKrClient`와 httpx offline transport로 만든 실제 SDK DTO를 기존
  `_source_spec`으로 저장한 뒤 네트워크 없이 복원했다. 외부 원천 호출은 없다.
- 집중 54건 PASS: land/temperature 값·lineage·UTC revision, 정책 namespace,
  올바른 hash를 다시 계산한 원문 변조·빈/복수 행·필수 구간 누락·숫자·지역·endpoint·
  시각·가용 지연·선택적 receipt·scope 상한을 검사한다.
- 전체 Ruff PASS. 두 독립 리뷰 및 정확 CI는 PR에서 별도 수용한다.
