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

핵심 PostgreSQL 시험10건이 통과했다. 파일 없는 DB 저장, 중복 방지, 반환값 수정 격리,
rollback, 다른 엔진 간 advisory guard, fact·receipt·snapshot 원자성, 실패 시 원본 보존,
live 파일만 명시 이관, 점검 모드 무변경을 포함한다. 기존 파일 GC 관련26건은 앞 시점의
핵심8건을 포함한 묶음이며10+26으로 고유 시험 수를 합산하지 않는다.

기존 reader/guard 호환20건은 별도 범위다. 전체 통합·migration·CI·운영 속도 개선 수용은
아직 남아 있다. 완료 전에는1초·p95 목표나 DB 전환 배포를 달성했다고 표시하지 않는다.

최종 PR에는 실제 시험 결과와 source/image·schema·이관 개수·파일 잔여량·전후 성능을 갱신한다.
Weather를 먼저 빌드·SDK 배포·검증하고 Transport 성능 변경을 이어 배포한다.
