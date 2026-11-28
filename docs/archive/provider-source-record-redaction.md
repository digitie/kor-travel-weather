# Provider source record 중복 순회 축소

## 범위와 동작

기준 main은 dde76a2e0356cfb69f23c95a2aff7c78c5ca738d(PR107 병합)이다.
providers/base.py와 해당 회귀·동일 작업량 벤치만 변경한다.
공개 source_record_key는 raw payload를 계속 redaction한다.
make_source_record는 payload/endpoint/request_params를 정제한 뒤 내부 private hash helper로
같은 canonical JSON/SHA256을 계산한다. 두 번째 전체 redaction/임시 container 복사만 생략한다.
캐시·공개 unsafe 플래그·provider 호출·수집량·API DTO·DB·격자·Common 변경은 없다.

status_code는 공개 타입인 int/None일 때만 내부 경로를 쓴다. 비정수 기존 입력은
종전 공개 redaction 경계로 되돌려 기존 반환 payload/hash를 유지한다.
Unicode·URL query secret·시간/Decimal canonical identity와 원본/반환 객체 독립성도 보존한다.

## 동일 작업량 측정

[재현 벤치](../benchmarks/provider_source_record.py)는 제품 함수만 AST로 로드해 외부
provider/DB/SDK/network 없이 source record를 만든다. natural OpenMeteo hourly336 응답
(8변수, 수집 provider 호출 없음)과 raw6000행을 동일 객체·동일 호출수로 비교한다.
warmup 뒤 ABBA(old/new/new/old)3회, CPU sample당3회 호출, 각경로6표본의 중앙값이다.
CPU는 process_time, memory는 tracemalloc 단일호출 peak이며 입력 fixture는 측정 전에 만든다.
원본 입력과 전체 반환값 동등성을 매 fixture에서 확인한다.

Python3.12.13/WSL x86_64의 [전체 표본](../benchmarks/results/source-record-py312.json):

| fixture | old CPU/호출 | new CPU/호출 | 감소 | old Python peak | new Python peak |
|---|---:|---:|---:|---:|---:|
| OpenMeteo hourly336 | 3.00695ms | 1.98108ms | 34.1% | 146624B | 145264B |
| raw6000 | 155.19044ms | 94.79249ms | 38.9% | 10572926B | 10277678B |

peak 감소는 각각 약0.93%/2.79%로 작다. canonical JSON 직렬화·반환 payload 비용은 그대로다.
이 값은 source-record helper의 synthetic workload이며 전체 수집 run/ARM/DB/host CPU 또는
RSS 개선율을 뜻하지 않는다. payload형태·cache·환경에 따라 달라지며 실제 배포/수집 실측은 별도다.
제품 전 in-memory prototype으로 개선을 먼저 확인했으며 이후 실제 변경 함수로 다시 측정했다.

재현 예(동일 interpreter로 두 경로를 측정):

    git show dde76a2e0356cfb69f23c95a2aff7c78c5ca738d:src/kortravelweather/providers/base.py > /tmp/source-record-before.py
    python3.12 -B benchmarks/provider_source_record.py --baseline /tmp/source-record-before.py --candidate src/kortravelweather/providers/base.py

## 회귀와 한계

- 이전 make_source_record의 같은 source로 중복 전체순회 회귀가 RED임을 직접 확인했다.
- 새10회귀는 legacy oracle와 전체 반환값/key/원본 불변을 비교하고,
  raw public key redaction·중첩 credential·URL·Unicode·날짜/시간·Decimal·status 호환을 검증한다.
- 새 회귀와 기존 provider fixture/샘플링을 포함한 DB없는 core50 PASS.
  root conftest를 읽지 않고 --noconftest와 명시한 순수 test만 실행했다.
- Ruff 전체 PASS, frontend type-check 및 기존202 tests PASS.
- full PostgreSQL suite는 root 전용 후속 gate다. 이번 agent는 DB/Docker/SSH/provider/SDK 운영 호출0.
  frontend·dependency/lock 바이트는 바꾸지 않았다.
- 초기 벤치 보조코드의 SyntaxError/누락 sys import와 초기 own 모형 명칭 오류는 별도 이력으로 보존했다.
  제품/성능 성공으로 집계하지 않으며 최종3.12 결과만 위 표에 사용했다.
- merge/배포는 독립2인 리뷰·CI·root 검증 뒤 별도다.
