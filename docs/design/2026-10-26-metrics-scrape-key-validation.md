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

최초 설계 커밋571db9d와 Draft PR #171을 먼저 남겼다. main merge와 운영 적용은 Root의 후속 범위다. 아래 구현 체크포인트를 과거 설계 상태와 구분한다.

## 구현 및 합성 비교 체크포인트

memo 한도는4,096개·262,144문자다. 유효 문자열만 보존하며 상한 초과 새 key는 원 검증으로 돌아간다. merge race의 재시도 preflight에서도 새 지역 set으로 시작한다. upstream merge·파일 전체 순회·malformed 격리·오류 응답·수집 코드는 바꾸지 않았다.

새 회귀10개는 원 코드에서9FAIL/1PASS이고 수정 후10PASS/1.67초다. 중복 검증 횟수·개수/문자 한도 fallback·후행 invalid key·inode/size/mtime가 동일한 다음 scrape의 key 오염·실제 mmap과 upstream 출력 전체 bytes 일치를 포함한다. 전체 Ruff PASS다. 기본 실행은 root conftest의 PG autouse fixture가 localhost15432 연결 거부로10ERROR여서 원 로그를 보존했다. 이후 `--noconftest`의 순수 범위로 검증했으며 PG fixture·guard를 수정하지 않았다.

기존 `tests/test_metrics.py`는 로컬 공용 venv의 구 Common/구 KMA로 import가 실패해 collection 미도달이다. 정확 pin Common의 A-only 소스 대조 뒤에도 KMA `is_missing` 부재가 남았으며 venv/provider 제품은 바꾸지 않았다. 전체 pytest와 frontend type/test·정확 lock CI는 아직 수용하지 않았다. 새 테스트 통과를 이 미완 게이트의 대체로 세지 않는다.

A의 합성 비교는979개×64KiB=64,159,744B, 파일마다 동일32 raw key를 사용했다. Python3.13.14/Linux x86_64/prometheus-client0.26.0에서 old/new fresh process ABBA×2의 첫 HTTP endpoint 요청을 측정했다. 생성·import 시간은 제외하고 같은 프로세스의 client+server CPU를 포함한다. 운영 ARM 또는 실제 파일 key 분포와 동일하다고 주장하지 않는다.

| CPU 표본4개씩의 중앙값 | old | new |
| --- | ---: | ---: |
| endpoint CPU초 | 0.135418 | 0.081760 |
| endpoint wall초 | 0.135242 | 0.081583 |
| process peakRSS B | 35,887,104 | 35,827,712 |

응답5,152B는 모든 표본에서 whole bytes가 같고32 counter 각각979.0이다. 전용 listener와 임시 디렉터리는 정리됐다. RSS 감소는 수용하지 않는다. 별도 tracemalloc 1표본씩의 peak는4,235,290→4,235,202B/current는26,892→30,236B여서 메모리0 또는 메모리 개선 주장도 하지 않는다. memo가 merge의 중복 JSON decode까지 제거하는 것은 아니다.

원장 `weather-metrics-key-validation-endpoint-paired-r1.json` SHA256 `c6913c3bcede67296d661d452f5df21dd5072ee2892f18c6e4892ff239f16775`와 재현 스크립트는2026-10-24-operations 감사 디렉터리에 보존한다. 실제 운영 CPU 절감·수집 성능·배포·장시간 안정성은 미측정이며 두 독립 리뷰와 최종 CI를 기다린다.
