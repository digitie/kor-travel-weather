# DB 스냅샷 전환과 읽기 성능 개선

상태: 구현·통합 시험 중. 운영 배포 완료 문서가 아니다. 사용자2026-11-18 요청으로
[Map PR1505](https://github.com/digitie/kor-travel-map/pull/1505)의 Weather4항목과
DB snapshot 전환을 병행한다. 기존 [성능 설계](../design/weather-performance-use-cases-20261109.md)의
정합성·승인·수명·최대60초 상태 재검증 계약을 유지한다.

## 저장 구조

원본·정규화 값은 이미 PostgreSQL에 있다. 기존 JSONL은 승인된 generation을 고정하는
조회용 사본이다. 단기예보 fact는 valid_at 뒤6시간에 삭제될 수 있고 일반 fact도 partition
정리 대상이므로, 기존 fact FK만으로 성공 snapshot의 독립 수명을 재현할 수 없다.

`weather_export_artifacts`에 digest PK·불변 canonical payload·크기·점 개수·생성 시각을
보관한다. 이 조회 사본의 DB 용량도 별도로 측정한다. 원문 이력을 통째로 복제하지 않는다.
fact·receipt·snapshot은 같은 transaction에서 확정하고 파일 후처리 쓰기를 제거한다.

서비스 factory는 DB 저장소만 만든다. DB row가 없을 때 이전 파일을 자동으로 읽지 않는다.
기존 파일 저장소는 명시적인 이관 도구와 관련 호환 시험에 한정한다. 조회용 DTO·cursor와
승인 generation/head/snapshot·일별 checkpoint의 참조 구조는 유지한다.

## 보존·이관

DB GC는 `(created_at,digest)` keyset을 사용한다. 기존 live 참조와24시간 보존·1시간 유예를
유지하며, DB advisory guard 안에서 삭제 직전 참조를 확인한다. SQL3초 상한과 실패한 batch의
다음 tick 재시도 원칙을 유지한다. DB commit 뒤에만 삭제 수·진척을 확정한다.

이전 파일의 이관은 동일 live 판정식으로 현재 필요한 것만 선택한다.
`python -m kortravelweather.export_artifact_migration`은 기본 점검 모드이며, 명시적
`--execute`는 검증된 내용만 DB에 추가한다. 이관 자체는 파일·기존 DB 행을 삭제하지 않는다.
최종 전환 직전 수집·publisher drain 후 다시 확인해 누락0을 검증한다. 파일 삭제는 DB 조회
전환과 실제 응답 수용 뒤 별도 단계로 수행한다.

## 성능 작업

- `T-READ-WEA-01`: 공개 DB 읽기 예산·503·연결 반환, pool/freeze/content/serialize 단계별 지표,
  첫 페이지·cursor·304 구분.
- `T-READ-WEA-02`: 안전한 ETag 선판정, 큰 manifest·health의 반복 구성 절감, 한 페이지가 읽는 scope
  수의 상한. 기존 완료 페이지/선택 캐시를 중복 구현하지 않고 재사용한다.
- `T-READ-WEA-03`: 유효 cursor에서만 TTL 갱신, 총 수명 상한, 만료 handle 부활 금지.
- `T-READ-WEA-04`: 조건부 특보 최신성 개선.2026-11-18 06:50 KST 실제 읽기 전용 EXPLAIN에서
  최신 성공 조회만 Seq Scan+Sort였다. 최신 run과 성공 시작 하한은 started 인덱스를 썼다.
  이 증거로 필요한 최소 인덱스와10배 fixture의 계획을 검증한다.

운영 PinVi는 일반 날씨를 Map Valkey로 읽으며 Weather 직접 경로는 주로 특보다.
이 작업은 Weather 요청 한 건의 시간뿐 아니라 Map용 전체 페이지 순회가 끝나는지와
odroid CPU·I/O·메모리·DB pool 비용을 함께 확인한다. Map 운영을 직접 변경하지 않는다.

## 현재 검증과 남은 수용

DB 저장소·원자적 capture·보호 참조·GC·이관·읽기 예산·실제 pool 고갈을 PostgreSQL에서
시험했다. 읽기 관련 API249건과 별도 수집·일별·publisher170건도 통과했다. 범위가 겹치는
시험 수는 합산하지 않는다.

전체2,113건 실행에서2,106건이 통과했고7건은 같은 schema 상태 누수로 실패했다.
임시 schema를 생성하는 engine이 공용 metadata의 schema 기억값을 덮어써 이후 public
engine이 삭제된 schema를 참조하는 문제다. 검증된 schema를 engine/Table별 약한 참조에
보관하도록 수정하며, search_path를 바꿔도 검증된 schema를 쓰는 기존 방어를 유지한다.
서로 다른 engine·schema와 임시 schema 삭제 뒤 새 public engine의 회귀 시험을 추가했다.
관련76건과 기존 실패를 재현한 순서의308건이 통과했다. 수정 뒤 전체 CI 통과를 병합 조건으로 한다.

배포에는 canonical migration0042(DB artifacts)와0043(최신 성공 부분 인덱스),
현재 live 파일만의 prefix별 재개 가능한 이관, 최종 delta 확인이 필요하다. 기존 GDAL·Python·npm
빌드 캐시를 재사용하며, 원본 데이터 백업이나 불필요한 전체 파일 복제를 추가하지 않는다.
운영 SDK 적용과 화면·API·성능·새 JSONL 생성0 확인은 아직 남아 있다.

최종 PR에는 실제 시험 결과와 source/image·schema·이관 개수·파일 잔여량·전후 성능을 갱신한다.
Weather를 먼저 빌드·SDK 배포·검증하고 Transport 성능 변경을 이어 배포한다.
