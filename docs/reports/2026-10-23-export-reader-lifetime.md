# Export reader 수명의 guard-first 경계

## 범위와 현재 상태

PR160 `ba70683eb1d8f8b582b060295b169a14643ce987`를 기준으로 공개 page와 일간
reducer의 metadata→파일 읽기→최종 CAS 구간을 하나의 SH guard 수명 안으로 옮긴다.
공식 page entry가 SH guard를 잡은 뒤 metadata를 읽고 응답/health를 확정할 때까지
유지하도록 구현했다. GC·unlink·DDL·보존 기간·운영 설정은 변경하지 않는다.

공식 공개 entry가 guard를 한 번 소유하고 private 파일 helper는 중첩 flock을 잡지 않는다.
current·forecast·mid·reference·daily page, daily reducer를 대상으로 하며 파일 IO 동안
PG transaction/connection을 반환하는 기존 조건과 최대2 reader/배타 writer를 유지한다.

## 파일 IO entry 감사

| 진입점 | guard 소유와 DB 수명 |
| --- | --- |
| `FrozenCurrentExports.page` | metadata/cursor/fallback부터 파일 읽기와 health까지 SH |
| `FrozenForecastExports.page` | metadata/cursor부터 `_page_items`와 health까지 SH |
| `FrozenMidForecastExports.page` | limit 검증 후 상위 forecast entry의 SH를 사용. 중기 private chunk helper에는 별도 flock 없음 |
| `FrozenLocationExports.page` | 파일을 직접 읽지 않아도 frozen reference metadata의 사용 수명을 SH로 보호 |
| `DailySummaryExports.page` | frozen roster와 요약/coverage 조회의 수명을 SH로 보호 |
| `refresh_daily_summaries` | checkpoint/refs 조회 전 SH, 파일 IO에는 연결 반환, 이후 summary/coverage/checkpoint CAS 후 SH 해제 |
| `ExportArtifactStore.publish` | 기존 EX 유지. native receipt TX commit 후 호출 |
| `ExportHeads.promote` | 기존 EX 아래 reconcile·candidate/parent 파일 검증·최종 head CAS 유지 |

`ExportLifecycle.reconcile`의 파일 read는 공식 publisher의 EX 안에서 호출된다.
`_page_guarded`, `_page_items`, `_chunk`와 reducer private body는 이미 guard를 소유한
entry 내부 helper다. helper를 독립 서비스 진입점으로 쓰거나 중첩 guard를 취득하는
계약을 추가하지 않았다. guard 취득 대기 예산은2초이며, 이 값이 전체 SQL/파일 처리의
엄밀한 wall deadline이라는 의미는 아니다. SQL별 기존5초 상한을 유지한다.

## 검증 현황과 남은 수용

- 별도 OS process의 공개 page 5종에 대해 metadata body 입장부터 EX writer 배제,
  정상 반환·예외·강제 종료 뒤 main guard와 두 reader slot 회복을 확인했다.
  두 공개 forecast/mid page가 동시에 입장해 중첩 slot 대기를 하지 않는 것도 확인했다.
- 다른 프로세스의 실제 EX probe와 새 전용 PG를 사용한 5종 page의 first/cursor 조회,
  파일 IO 중 pool checked-out 0, 마지막 health SQL까지 SH depth1을 확인했다.
  reducer는 metadata 조회 전부터 파일 검증과 최종 checkpoint CAS까지 같은 검사를 수행했다.
- 초도 신규22건 PASS(97.50초). 이 뒤 추가한 공개 entry의 admission2초 상한1건과
  기존 frozen/mid/daily/publisher 관련 회귀는 별도 실행 중이다. 전체 Ruff PASS다.
- 독립 두 리뷰·정확 CI·실제 HTTP/UI는 후속 수용이며 완료 전 결과로 세지 않는다.
- 선행159의 별도 source 건강 보강은 작성자 소유로 유지하고 확정 뒤 통합한다.

시험 DB는 기존 lab 안의 새 이름으로 생성하고 0001→0033 migration을 적용했다.
다른 작업자의 DB를 재사용/truncate하지 않았다. DSN·자격 증명은 보고서/저장소에 넣지 않는다.

이 변경만으로 GC 준비나 운영 CPU/RSS 개선을 완료했다고 주장하지 않는다.
