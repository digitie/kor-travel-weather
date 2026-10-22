# 수집 요청·게시 병행 구조

## 목표와 불변 조건

KMA 격자/중기 수집은 최대 두 격자를 선행 요청하고 기존 순서대로 소비한다.
DB 게시자는 하나이며 기존 source lineage, 고유 키/upsert, 실행 소유권 fencing,
격자별 중복 요청 제거, 위치별 게시 묶음과 전체 실행 fact/row 예산을 유지한다.
전체 Dagster 동시 실행 수와 기존 provider 실행 그룹 한도는 변경하지 않는다.

## 메모리·실패 경계

- 선행 요청/완료 대기 합계 최대 두 격자다. 전체 격자별 task를 한꺼번에 만들지 않는다.
- 격자당 raw source JSON은 UTF-8 기준 8MiB, 원천 행과 정규화 fact는 각각 5,000개 이하다.
  이는 대기 응답의 크기 제한이며 Python 객체 전체 RSS나 provider 내부 일시적 할당의 상한은 아니다.
- 소비자가 DB에 게시하는 동안 추가 요청은 슬롯 수를 넘겨 시작하지 않는다.
  기존 게시 staging은 fact 2,500개·source 25개와 위치별 chunk 규칙을 유지한다.
- 전체 실행 예산은 소비할 때 다시 검사하므로 선행 요청이 예산을 우회하지 못한다.
- fetch 실패, DB 게시 실패, 취소 시 모든 미소비 task를 cancel/gather하고 provider context를 닫는다.
  먼저 commit된 chunk는 보존하고 실패 실행에는 실제 commit 수를 남긴다.
- source JSON 바이트 상한 초과는 조용히 자르지 않고 실패로 기록한다.

## 검증과 배포

두 요청의 겹침, 소비자 backpressure, 순서가 뒤집힌 provider 실패, DB 실패 시 회수,
응답 크기 초과, 전체 예산/기존 lineage 회귀를 테스트한다.
전용 PostgreSQL 전체 테스트와 프런트 검사, 두 독립 리뷰, live UI/API를 통과한 뒤 배포한다.
code-server 교체 전 이 location의 instigator를 기록·일시 중지하고 활성 worker를 비운다.
공용 daemon은 계속 실행하고 교체 실패 시에도 원래 instigator 상태를 복원한다.
배포 전후 같은 수집 범위의 wall time·CPU·RSS/cgroup 메모리·실패율과 API 지연을 비교한다.
아직 실측이 끝나지 않았으므로 CPU/RAM 감소를 달성했다고 판정하지 않는다.
