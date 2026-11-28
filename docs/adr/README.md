# Architecture decision records

이 디렉터리가 weather의 아키텍처 결정 정본이다. 예전 `docs/decisions.md`의 목록과 본문은 여기로
옮겼다.

## 언제 ADR을 쓰는가

아래 셋 중 하나라도 해당하면 ADR을 쓴다.

- **비용:** 6개월 뒤 이 결정을 뒤집으려면 며칠 이상 걸리거나 데이터 이행·삭제가 필요하다.
- **영향 범위:** 여러 모듈이나 다른 저장소(Map, PinVi, Manager)의 작업 방식을 제약한다.
- **대안:** 뚜렷한 대안이 있었고, 단점을 감수하고 현재 방식을 골랐다.

다음은 ADR로 쓰지 않는다.

| 내용 | 기록할 곳 |
| --- | --- |
| 필드명, DTO 형식 | `packages/kor-travel-weather-api/openapi.json` |
| 상수·timeout·batch 크기·인덱스 조정 | 코드 주석, 해당 주제 문서 |
| 성능 개선, 버그 수정의 이유 | PR 본문, 커밋 메시지 |
| 큰 기능의 흐름과 세부 스펙 | `docs/design/` (그중 핵심 분기점 1~2개만 ADR로 뗀다) |
| 측정·감사·리뷰 결과 | `docs/reports/`, `docs/reviews/`, `docs/audits/` (시점 기록) |

## 양식

한 결정에 파일 하나, 한 화면 분량으로 쓴다. 파일명은 `NNN-짧은-주제.md`다.

```markdown
# ADR-NNN: 결정을 한 문장으로

- 상태: 제안 | 채택 | 대체됨(ADR-MMM)
- 결정일: YYYY-MM-DD

## 맥락
## 결정
## 검토한 대안
## 결과
```

채택된 ADR의 결정을 바꿀 때는 본문을 고치지 않고 새 ADR을 쓴 뒤 기존 ADR의 상태를
`대체됨`으로 바꾼다. 오타·링크 수정과 운영 값의 위치 안내는 그대로 고친다.

## 번호

- 089 이하는 원본 `kor-travel-map`의 ADR 번호를 그대로 쓴다. weather 경계에 맞게 적용한 내용만 둔다.
- 101 이상은 이 저장소 고유 번호다. Map에도 같은 번호의 다른 ADR이 있다.
- 다른 저장소의 결정은 항상 저장소명을 붙여 부른다(`Map ADR-114`, `Manager ADR-58`). 이 저장소는
  그 내용을 복사하지 않고 링크만 둔다.

## 목록

### Map ADR을 weather에 적용한 것

| ADR | 결정 | 파일 |
| --- | --- | --- |
| 002 | 비동기 provider I/O와 API/repository 경계 | Map 정본 |
| 020 | API/backend와 admin frontend를 별도 package로 유지 | Map 정본 |
| 048 | `/v1`, `{data,meta}` envelope, request id, pagination | Map 정본 |
| 062 | 소비자와 무관한 weather history, 3년 보존 목표 | Map 정본 |
| 072 | `known_at`/`target_at` bitemporal 축 | [072](072-weather-bitemporal.md) |
| 074 | append-only write safety, 삭제 대신 비활성화 | [074](074-write-safety.md) |
| 089 | current summary fact reference와 source revision | [089](089-current-summary-fact-reference.md) |

### weather 고유 ADR

| ADR | 결정 | 상태 |
| --- | --- | --- |
| [101](101-kma-alert-dataset.md) | KMA 특보를 정식 weather dataset으로 수집한다 | 채택 |
| [102](102-daily-summaries-stale-not-503.md) | W5 전체 목록은 미승인 원천 때문에 503을 내지 않는다 | 채택 |
| [103](103-export-snapshot-handle-retention.md) | 오래 요청되지 않은 export snapshot handle은 지운다 | 채택 |
| [104](104-export-success-snapshot-promotion.md) | 서비스 export는 성공한 수집만 불변 snapshot으로 승격한다 | 채택 |
| [105](105-kma-short-forecast-retention.md) | KMA 단기예보는 지난 대상시각 기준으로 정리하고 미래 예보는 보호한다 | 채택 |
| [106](106-provider-quota-bounds-collection.md) | 외부 provider 수집 범위는 무료 quota 안에서 정한다 | 채택 |

### 다른 저장소의 결정 (링크만)

| 결정 | weather에 미치는 영향 |
| --- | --- |
| Manager ADR-58 | weather 스택을 odroid(`192.168.1.13`)에서 운영한다. 정본은 kor-travel-docker-manager `docs/decisions.md` |
| Manager ADR-69 | 공용 Dagster instance의 호스트별 동시 실행 상한 |
| Map ADR-105 | Map DB에 날씨를 저장하지 않는다(Map ADR-114가 Valkey로 부분 대체) |
| Map ADR-114 | Map은 Weather export를 읽어 Valkey 세대로 교체한다. weather 쪽 결정은 [ADR-104](104-export-success-snapshot-promotion.md) |

## ADR이 아닌 소유자 운영 결정 (기록)

- **2026-10-27 "권장대로" (대체됨).** export 목표 일시중지, 외부 날씨 current/forecast job 분리,
  artifact GC를 dry_run으로 시작해 관찰 뒤 delete로 전환. 2026-11-02에 소유자가 export를
  재개했고 current/forecast job 분리(W-EXP-SPLIT-01)를 적용했다. GC 제품 기본은 delete로 바뀌었다
  (`docs/tasks.md` W-EXP-GC-01). "run 성공 뒤에만 승격" 원칙은 [ADR-104](104-export-success-snapshot-promotion.md)로 옮겼다.
