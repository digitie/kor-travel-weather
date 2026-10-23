# Export artifact 동시 읽기 제한

기존 artifact guard는 읽기끼리도 같은 배타 잠금을 사용했다. current·forecast 페이지와
일간 요약의 파일 읽기는 공유 잠금을 사용하고, publish·head 승격의 기본 배타 잠금은
유지한다. 파일 hash·행 수·byte 상한·NOFOLLOW 검증도 유지한다.

동시 읽기는 무제한 허용하지 않는다. 같은 artifact root의 두 일반 lock 파일 slot을
배타 취득해 API·code server·프로세스 수와 관계없이 최대2개만 파일을 읽는다.
slot과 본 guard의 대기에는 기존 하나의 timeout 예산을 적용한다. slot 파일은600
권한으로 만들고 symlink/비일반 파일을 거절한다. 프로세스가 죽으면 OS가 fd 잠금을
반납하므로 영구 slot 점유를 남기지 않는다.

운영 CPU/RSS·throughput 개선율을 주장하지 않는다. 이 변경은 기존 명시 export
경로의 동시 파일 읽기 경계이며 수집 parallelism·운영 flag·daemon 설정은 바꾸지 않는다.
snapshot metadata 조회와 파일 guard 사이의 in-flight pin은 아직 별도다. 파일 GC를
활성화하지 않으며 향후 GC는 `.export.lock`·`.export-admission.lock`·`.export-read-*.lock`을 삭제하면 안 된다.

## 검증

- 별도 OS 프로세스의 실제 flock4건 PASS: 동시 reader2개 허용/세 번째 대기 상한,
  writer와 모든 reader 상호 배제, 마지막 reader 종료 후 writer 취득, reader 강제 종료
  뒤 본 잠금과 두 slot 용량 복구를 확인했다. mock lock은 사용하지 않았다.
- 전체 Ruff PASS. 기존 PostgreSQL reader/publisher·두 독립 리뷰·정확 CI는 PR에서
  수용하며 운영 ARM 측정이나 전체 GC 완료로 세지 않는다.

초기 독립 리뷰에서 reader가 계속 겹치면 writer가 반복 timeout할 수 있는 점을
발견했다. 별도 admission guard를 writer는 본 잠금 대기 전부터 끝까지 보유한다.
reader는 이 guard를 통과해 SH를 취득한 즉시 admission을 반납한다. 기존 reader가
끝나기를 기다리는 writer 뒤로 새 reader가 끼어들지 않는다. 기존 reader1개 유지→
writer의 실제 admission 취득→후속 reader 차단→기존 reader 종료 뒤 writer 취득의
OS 회귀를 추가했다. slot/admission/main 대기는 같은 timeout 예산을 공유한다.