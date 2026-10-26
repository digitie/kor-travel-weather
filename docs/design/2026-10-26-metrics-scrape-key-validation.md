# scrape 안의 중복 metric key 검증 제한 메모

## 근거와 범위

기준 main은 `5d7a6e10e52c563fb2de03ea78581733fec30a6f`다. 운영에 새 수집이나 설정 변경을 하지 않고 Root가 승인한 py-spy0.4.2/15초/10Hz GIL 관측에서 Weather25표본 중 JSON decode·mmap·`_valid_metric_key`·merge 프레임을 확인했다. 이것은 CPU초 또는 전체CPU 기여율의 증거가 아니다. 저장된 metrics 파일은979개/64159744B였으며 실제 운영 프로파일 결과 SHA는 `670bf8ffa6cde923fe1899d4685620a89d3f233d58f38fc770a45a4062a05551`이다.

변경은 `metrics.py`의 파일 preflight에서 이미 검증한 **동일 raw key 문자열**을 제한된 set에 잠시 보존하는 것뿐이다. 원 upstream merge와 mmap 읽기·파일 무결성·잘못된 key 격리·재시도·API/DTO/수집/권한은 유지한다. inode/mtime나 PID 생존을 key 내용 검증의 대체 증거로 사용하지 않는다.

## 설계

- memo는 한 preflight 호출의 지역 상태다. scrape 또는 merge-race 재시도의 새 preflight에서는 항상 비운다. cross-scrape 전역 cache는 없다.
- 정상 `_valid_metric_key` 검사를 통과한 raw `str`만 저장한다. label 정규화·key 재인코딩·원문 변경은 하지 않는다.
- 개수와 저장 문자열 문자량을 모두 제한한다. 저장 허용량을 넘은 새 key는 원 검증을 매번 그대로 수행하며 거부·skip·검증 우회하지 않는다. 이미 저장한 동일 key의 재사용만 가능하다.
- 잘못된 key, 뒤늦은 invalid key, 동일 파일의 다음 scrape 변경은 원 검증·격리 경로를 따른다. 파일별 모든 mmap entry 순회는 유지한다.

## 수용 계획

중복 정상 key의 JSON 검증 횟수, 두 예산의 unique-key fallback, 후행 invalid key, 다음 scrape 파일 변경, 실제 mmap+upstream merge의 출력 동일성을 확인한다. 기존 metrics 회귀와 저장소 필수검사를 실행하고 환경상 불가능한 검사는 분리 보고한다.

별도 A 실험은 합성979개×64KiB mmap 파일을 한 번 생성해 old/new를 fresh process ABBA로 비교한다. 같은 registry endpoint 결과 bytes·sample값, wall/CPU/peakRSS·메모리 보관량을 기록하되 생산기 CPU/RSS·전체 수집 성능·운영 절감 또는 zero-memory 개선으로 환산하지 않는다. unique-key 및 오염 표본도 별도로 검증한다. benchmark는 실제 서비스·운영 파일·provider·DB를 사용하지 않는다.

현재는 설계 체크포인트이며 구현·비교 측정·독립 두 리뷰·정확 head CI는 미완료다. 초안 PR 후 작은 구현 커밋으로 이어간다. main merge와 운영 적용은 Root의 후속 범위다.
