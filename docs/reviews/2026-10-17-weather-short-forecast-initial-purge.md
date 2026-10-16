# 단기예보 지난6시간 밖의 기존 데이터 초기 정리

요구는 미래 예보 제한 없이 보존, valid_at이 현재시각보다6시간 넘게 지난 정확한
KMA 단기예보만 정리하는 것이다. source582의 정본 runner/repository를 실제 API 권한으로
실행했고 별도 DELETE/DDL/관리자 권한·전역 JIT 변경은 추가하지 않았다.

## 실제 결과

고정 cutoff 2026-10-16T10:12:41.557564+00:00의 대상55,987 fact를 모두 삭제했다.
projection은21,262개를 삭제했다. 각 batch의 정확한(value_id,known_at)을 사용하며
source/raw/association 삭제 경로는 없다. dry100은0삭제/약0.208초였다.

| 단계 | fact 삭제 | projection 삭제 | batch | runner 경과초 | 잔여 |
| --- | ---: | ---: | ---: | ---: | --- |
| 소량 확인 |100|100|1|0.560|true|
|60초 예산1|24000|10579|24|60.436|true|
|60초 예산2|25000|7716|25|62.259|true|
|마지막 별도 operation|6887|2867|7|17.877|false|

각 호출은 독립 once이며 앞 호출의 실제 native terminal·누적 commit·보호값·공간을
정산한 뒤 다음 새 operation을 시작했다.60초는 batch 사이 협력 예산으로,
진입한 transaction을 강제 취소하지 않아 실제 runner wall이 넘을 수 있다.
budget_exhausted를 완료로 표시하지 않고 마지막PURGE_COMPLETE/remaining=false로 확인했다.
초기 DDL·과거 실패 이력·기존 컨테이너/이미지는 보존했다.

추가 읽기 검증의 새 cutoff 2026-10-16T10:43:47.750501+00:00에서도
만료 fact count0(약23.3ms)을 확인했다. 이 시점 실제 writer0이며0020 정본을 유지했다.
이는 그 시점 snapshot이고 미래에 새로 만료되는 값의 영구0건 보장은 아니다.

## 보호와 한계

전체 작업에서 미래 exact-pair8개의 fact/source/current-pointer MD5를 묶은 SHA가
같았다. equality/null/nonshort category는 실제표본0이므로 해당 전체 데이터의 운영
불변성을 표본으로 증명했다고 하지 않는다. 정확 predicate와 다른 pair·원천 보존은
기존 전체628 테스트 및 두 제품 리뷰의 별도 범위다.

기존99 index·새11 index valid/ready, FK/partition tree/정의 fingerprint가 전후 같다.
PG 데이터 여유는508GB 이상, 할당 WAL segment는79개로 유지됐다.
segment 수가 같다는 것은 WAL 생성0이라는 뜻이 아니며 DELETE 직후 파일 축소를 주장하지 않는다.
원천 raw와 source lineage는 코드 경계상 이 명령의 삭제 대상이 아니다.

[A once 검토](evidence/2026-10-17-weather-purge-once-a.md),
[D once 검토](evidence/2026-10-17-weather-purge-once-d.md)는 독립 SOURCE_PASS이며
새 열린P0/P1/P2가 없다. 두 리뷰어는 운영에 접근하지 않았고 실제 결과는 root가 관측했다.
[안전 실측 JSON](evidence/2026-10-17-weather-short-forecast-initial-purge.json)을 보관한다.

## 삭제 뒤 live UI

R26은 R25의 실행/증거 alias만 바꾸고 같은36개·25초 지도 예산·692위치·write guard를
유지했다.2026-10-16T16:40:05.532Z~16:40:56.529Z에36PASS/0FAIL이었다.
[결과](evidence/2026-10-17-weather-live-ui-r26.json)는 삭제 뒤 실제 API/UI 읽기와
Dagster 이력/상세·503/abort 복구·로그인·모바일까지 포함한다.
이 검증은 수집 job/freshness·전체 CPU 절감 증거를 대신하지 않는다.

10분 retention 정의는 존재하나 CPU 격리 중 공용 daemon은OFF다.
주기 실행 복원과 같은 서비스 집합의90초 CPU 측정은 후속 운영 단계다.
