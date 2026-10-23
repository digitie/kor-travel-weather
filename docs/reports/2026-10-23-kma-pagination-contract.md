# KMA 페이지 완결성 계약 수용

## 변경 범위

python-kma-api PR32 병합 b77fc5ad980e5baa0138ec05e7e13af67bd83859를 고정한다.
provider의 public KmaClient가 pageNo·고정 totalCount/numOfRows·페이지별 및 누적 행 수를
검사하고, 단축/중간 빈 페이지를 KmaParseError로 거부한다. Weather는 이 동작을 직접
호출하며 HTTP pagination·기상청 발표 시각·격자를 재구현하지 않는다.

기존 KMA lineage metadata allowlist에 public PaginationReceipt의 검증된 필드만 더한다.
collected_at 같은 조회 clock은 계속 source identity에서 제외한다. pagination 증거가
추가되므로 과거 증거 없는 원천과 source_record_key가 달라질 수 있다. 동일 원천·동일
증거의 재수집 키는 안정적이며 fact의 기존 unique/upsert 정책은 바꾸지 않는다.
기존 source payload를 수정하거나 과거 응답이 완결이었다고 소급 판단하지 않는다.
잘못된/확장된 외부 pagination metadata는 증거로 저장하지 않는다.

## 검증과 미완 범위

실제 public client + MockTransport → stage_grid 계약 회귀는 정상 응답의 receipt와
raw lineage 보존·인증값 제거, 불완전 페이지의 성공 staging 차단, 조회 clock에 독립적인
identity와 증거 없는 응답의 구분을 검증한다. KMA32 자체는 독립2인 리뷰·Python3.10~3.13
CI 및 Odroid 별도 프로세스의 실제 초단기실황 1회(8/8행) 검증 후 머지했다.
Weather PR141은 c6c007bda1af2730b2ddc74bb202d27077c625c7 기준으로 머지했다.
독립 적대 리뷰2인 최종 PASS, 정확 최종CI Python788(289.16초)/frontend243·타입·빌드·Ruff
SUCCESS다. 로컬 PostgreSQL 전체788(1342.64초), frontend243·타입·Ruff도 통과했다.
전체 로컬은 ac29059 제품에서 시작했고 최종c6는 journal제목2+/2-만 바뀐 동일제품이다.
정확 최종후보의 격리실제TCP API3/UI11 PASS, browsererror0/upstreamservice호출0이다.
이 API/UI 검증은 인증/503/기존 화면 수용이며 실제 export 데이터 공개나 운영 배포가 아니다.
[최종 수용 기록](https://github.com/digitie/kor-travel-weather/pull/141#issuecomment-6359190142)을 따른다.

receipt는 페이지 완결 증거다. 행의 의미·유일성·전체 source 권위·발표 revision·정상0을
보증하지 않는다. 빈 forecast list에는 metadata가 없어 정상0 승격에 쓰지 않는다.
실제 Dagster terminal adapter/collector receipt·3B 성공head/CAS·비용/frozen/GC·W1~W5
공개는 후속이다. export collector opt-in OFF와 API503을 유지하며 운영 배포하지 않았다.
기존 API DTO/OpenAPI·TypeScript·DB 스키마와 보존 정책은 바꾸지 않는다.
