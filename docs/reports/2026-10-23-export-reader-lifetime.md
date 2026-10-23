# Export reader 수명의 guard-first 경계

## 범위와 현재 상태

PR160 `ba70683eb1d8f8b582b060295b169a14643ce987`를 기준으로 공개 page와 일간
reducer의 metadata→파일 읽기→최종 CAS 구간을 하나의 SH guard 수명 안으로 옮긴다.
현재는 구현·회귀 수용 전 초안이다. GC·unlink·DDL·보존 기간·운영 설정은 변경하지 않는다.

공식 공개 entry가 guard를 한 번 소유하고 private 파일 helper는 중첩 flock을 잡지 않는다.
current·forecast·mid·reference·daily page, daily reducer를 대상으로 하며 파일 IO 동안
PG transaction/connection을 반환하는 기존 조건과 최대2 reader/배타 writer를 유지한다.

## 검증 계획

- 별도 OS process barrier로 reader가 metadata를 조회하기 전부터 EX writer를 막고,
  파일 읽기와 reducer 최종 CAS까지 보호하는지 확인한다.
- 전용 PostgreSQL에서 reader/cursor/reducer 회귀와 파일 IO 중 pool 반환을 확인한다.
- 시간 상한·예외·프로세스 종료 후 guard/두 slot 복구를 검증한다.
- 독립 두 리뷰·정확 CI·실제 HTTP/UI는 후속 수용이며 완료 전 결과로 세지 않는다.

이 변경만으로 GC 준비나 운영 CPU/RSS 개선을 완료했다고 주장하지 않는다.
