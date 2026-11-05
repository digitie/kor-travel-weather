# 공식 기상특보 발표 관측 계약

기존 활성 카드와 별도로 GET /v1/weather/alert-events를 제공한다. 이것은 공식 발표 관측의 이력이며 현재 활성 특보 전체를 확정하는 API가 아니다. 빈 목록·수집 실패·누락·취소로 해제를 만들지 않는다. Pinvi의 사용자·여행·동의·Telegram 발송은 이 저장소가 소유하지 않는다.

## 원천과 식별자

공개 python-kma-api SDK의 request_with_metadata로 WthrWrnInfoService/getPwnCd를 호출한다. 기존 DataGoKR 서비스 키를 사용하고 stnId108을 명시한다. 공식 첨부 지점표에서108은 전국이지만 각 행의 적용 범위는 areaCode로 판단한다.

[공식 서비스와 260601 첨부](https://www.data.go.kr/data/15000415/openapi.do)에 따라 command1/2/3/6/7/8은 발령/해제/연장/정정/변경발표/변경해제다. cancel1은 취소이며 일반 해제가 아니다. warnVar13 열대야와 warnStress2 중대경보도 보존한다. 알 수 없는 값은 unknown이다.

event_id는 정규화한 공식 발표 식별 필드와 취소·발효시각 관측으로 만든다. 페이지 번호·수집 시각·응답 순서·추가 필드가 같은 관측의 ID를 바꾸지 않는다. announcement_id는 동일한 공식 발표 아래의 취소·발효시각 수정 관측을 묶는다. tmSeq는 월별 번호이므로 발표 시각·관서·구역·종류·단계·command를 함께 사용한다.

원천에는 해제와 원래 발령을 연결하는 회차 ID가 없다. occurrence_id는 null이고 correlation_status는 unresolved다. 근접한 이전 발표로 회차를 추정하거나7/8을 최종 발령/해제로 합치지 않는다. 소비자는 여행·공식 발표·알림 의미별 중복 제어를 할 수 있지만, 원천에 없는 경보 회차 연결을 보장하면 안 된다.

## 지역의 검증 범위

namespace는 kr_bjd다. WeatherLocation.region_code의 예보구역 코드나 발행 관서를 BJD로 간주하지 않는다.

[공식 특보구역 CSV](https://www.data.go.kr/data/15043573/fileData.do), 위 첨부 XLSX의 REG_UP·코드 유효기간, [기상청 primary 행정구역 안내](https://www.weather.go.kr/w/forecast/guide/wrn-area.do), 보존된 공식202605 Juso를 교차 확인했다. 결과와 원천 SHA256은 src/kortravelweather/reference/alert_areas.json에 있다.

시도·시군구 전체가 확인되는 관계만 verified다. 부산처럼 여러 구를 통째로 열거한 구역은 각 구와 연결한다. 광주·고성 동명이역은 공식 REG_UP 관계로 구분하며 코드 prefix로 특보구역의 부모를 추정하지 않는다. 산지·평지·제외·부분·모호한 안내는 unknown이다. 현재413구역 중178개가 검증됐으며 이것은 전국 지역 커버리지 비율이 아니다.

코드 등록기간과 매핑 근거의 적용기간은 다르다. 현재 매핑을 확인하기 전 시점의 관측은 검증된 과거 관계로 소급하지 않는다. mapping_valid_from과 개별 코드 유효기간을 모두 확인한다. 부분 매핑의 범위를 응답에 항상 명시한다.

2자리 시도·5자리 시군구 집계를 지원한다. 10자리 개별 코드는 원천의 실제 동·리 suffix와 대조한다. 10자리 zero-padding 집계는 명시적으로 시도/시군구로 해석한다. 보존 원천에 없는 개별 코드는 unknown이지, 실제로 존재하지 않는다는 확정 판정이 아니다.

매핑 재생성은 scripts/build_alert_area_mapping.py에 공식 입력 파일과 timezone-aware verified-at을 전달한다. 원본 파일은 읽기만 하고 저장소에 넣지 않는다. 패키지는 변환된 reference JSON만 포함한다.

## 조회와 최신성

bjd_code는 필수다. since는 timezone-aware 최초 관측 시각 기준이며 기본 최근3일, 최대31일 이내다. 발표 시각만으로 영구 cursor를 전진하면 늦게 관측된 정정·취소를 놓칠 수 있으므로 구간을 겹쳐 조회하고 안정적인 ID로 중복을 막는다.

- region_status는 입력 코드의 확인 여부다. mapping_coverage는 partial이며 완전한 전국 적용 범위를 뜻하지 않는다.
- mapping_valid_from 이전 또는 코드 유효기간 밖 관측은 알림용 verified 이벤트에서 빠지고 unverified_scope_count에 표시한다.
- collection_status·last_attempt_at·last_success_at·last_error_code는 성공적인 빈 목록과 미수집/실패를 구분한다.
- fresh는 최근15분 내 성공과 최신 실행의 상태를 확인한다. 최신 실패 또는 오래된 성공은 fresh=false다.
- events_complete=false는5000행 조회 상한을 넘겼다는 뜻이다. 소비자는 이를 완전한 결과로 사용하면 안 된다.
- absence_is_clear는 항상 false다. 해제 알림은 명시적 release 관측만으로 판단한다.
- retained_since는 지원 보존 경계이며 history_available_since는 실제 성공 이력이 시작된 시각이다. DB가 그 이전부터 수집했다는 뜻이 아니다.

공개 DTO는 원천 payload·요청 URL·credential·운영 오류 원문을 내보내지 않는다. source_record_key는 원천 추적용 식별자다.

## 수집과 보존

kma_regional_alert_events_job을5분마다 실행한다. 기존 활성 카드의 hourly 작업과 WeatherValue export 계약은 유지한다. 공개 SDK가 조회·인증·응답 오류 처리를 소유한다.

조회 시작 시각을 KST로 고정하고3일 전의 날짜부터 당일까지 요청한다. 날짜 단위 API이므로 양 끝을 포함한 실제 요청 구간을 사용한다. 최대100페이지·페이지당100행·8MiB를 넘으면 실패한다. pageNo/numOfRows/totalCount·실제 행 수·중복을 검사하고, 여러 페이지면 첫 페이지를 다시 대조한다. 이것은 요청 구간의 페이지 검증이며 원천의 정렬·동시 갱신 snapshot 완전성을 새로 보장하지 않는다. 반복 조회는 누락 보완책이며 빈 결과에서 해제를 추정하는 근거가 아니다.

모든 페이지를 받은 후 원천 canonical key·이벤트·run-source 연결과 성공 종결을 같은 짧은 transaction으로 게시한다. 실행 행을 잠그고 running 소유권을 다시 확인하여 회수된 실행이 뒤늦게 게시하지 못한다. 시작 결과를 기다리는 중 취소돼도 DB thread의 결과를 회수하여 실행을 취소 상태로 닫는다. 한 관측은 최초 관측 시각과 원천 연결을 유지한다.

이벤트는31일 보존한다. 기존 정리 작업에서 오래된 이벤트를 지운 뒤 어떤 이벤트도 참조하지 않는 원천만 정리한다. 기존 fact/run-source 참조 보호도 유지한다. 같은 원천을 재조회한 시각으로 데이터의 최초 관측 시각을 덮지 않는다. source의 최초 fetched_at을 수집 성공 최신성으로 오용하지 않는다.

## 오류 분류와 검증

페이지 불일치·중복·상한·조회 중 첫 페이지 변경, 인증 거절·요청 한도·제공자 장애·전체 시간 초과·실행 소유권 상실을 고정된 안전한 오류 코드로 남긴다. 원문 URL이나 인증키를 예외 chain과 공개 API에 보관하지 않는다.

WSL에서 소유 Postgres와 실제 공개 SDK의 소유 HTTP fixture를 사용한다. 원천 의미·지역 매핑·게시 원자성·원천 GC·취소/회수·페이지 완결성·공개 HTTP 계약을 검증한다. 최종 전체 검증과 두 전문 적대 리뷰·정확 HEAD CI를 마친 뒤 owner PR을 병합하고 소비자가 실제 병합 계약을 동기화한다. 운영 접근·배포·시험은 하지 않으며 최종 운영 테스트는 Claude Code 담당이다.
