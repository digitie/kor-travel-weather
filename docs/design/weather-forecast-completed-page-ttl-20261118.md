# 느린 첫 예보와 조건부 페이지 재사용 수명

기준 main: `9a2062d03727bac682452209b1df94c7b0c8dcb2`.

## 운영 근거와 로컬 재현

기존 QA의 forecast limit=200 첫 요청은58.996초, 조건부304는55.505초였다. 조건부 응답은
x-export-read=generated였고 첫 요청과 같은128 chunk/123,671,293바이트/17 batch SELECT를
읽었다. 조건부 stage에는 freeze 2회가 있었다. limit=2000 조건부 요청은5.391초에
reused/payload0이었으므로 항상 payload가 필요한 계약으로 볼 수 없다.
이 QA에는 snapshot/head 식별자가 없어 운영의 유일한 원인을 확정하지 않는다.
이번 작업에서는 운영 HTTP/PG 관측을 추가하지 않았다.

기존 코드는 완성 본문의 deadline도 최초 selection_checked_after+60에 묶었다. 로컬 실제 PG
fixture와 fake clock으로 첫 생성59초+다음 metadata5초를 주면 같은 handle/ETag라도 deadline을
넘어 전체 payload를 다시 읽었다. 생성65초는 PageReuseExpired/503이었다. 실제 시간 대기나
대량 payload/limit2000 반복 없이 재현했다.

## 최소 수정

- 첫 예보 본문은 완성 뒤 최대60초 보관한다. current/daily/cursor의 기존 승인 TTL은 바꾸지 않는다.
- 첫 예보 hit는 매번 새 source/snapshot/health metadata를 확인한다. 같은 handle/digest/status인
  경우에도 기존 snapshot·삭제 epoch·plan/generation·실제 artifact 존재/크기를 검산한다.
  검산된 새 proof의60초 기한을 사용하며 이전 proof의 timestamp만 바꿔 연장하지 않는다.
- 첫 생성 자체가60초를 넘었다면 완성 시 같은 metadata/proof 검산을 다시 통과해야 한다.
  중간에 head가 바뀌면 기존 본문을200/304로 반환하지 않는다.
- cache hit는 본문 deadline을 갱신하지 않는다. metadata 실패/유실/만료/상태 변경은 기존처럼
  무효화하거나 실패하며 이전 성공을 대신 내지 않는다.
- 예보 target/snapshot expiry를 payload 읽기 전의 wall/monotonic 쌍에 고정한다. 기존처럼
  계산 후 monotonic에 과거 잔여시간을 더해 생성 시간만큼 business 경계가 밀리지 않게 한다.
  본문 deadline은 이 절대 경계보다 길어질 수 없다.

> 2026-11-24 갱신(호출 검토 A5): 위 hit 검산은 조건부 요청에만 쓰였고, `If-None-Match`가 없는
> 첫 예보는 캐시 항목을 강제로 버렸다(`force`). 이제 비조건부 첫 예보도 같은 hit 검산(새 freeze·
> snapshot/digest·건강도·삭제 epoch)을 거쳐 본문을 재사용한다. 근거와 계약은
> [map-service-exports.md](map-service-exports.md) §3.2.

## 검증과 남은 비용

회귀111개 통과, 추가 head 변경 case를 포함한 최종 slow-build API5개 통과(4개는 회귀와 중복),
Ruff/diff 검사 통과. 같은 handle의59/65초 생성 후 조건부304에서 단건/batch payload 읽기를
모두 금지해0회를 확인했다. 생성 중 실제 새 source가 publish되면503/무cache이며,20초 생성과
30초 target/snapshot 경계에서는 deadline이50/80초로 밀리지 않고30초다.
source prepare/cohort 실패 즉시 stale 판정, current 승인 만료, metadata 실패/만료 proof 거절,
기존 TTL/liveness/GC/파일 변조 검산도 포함한다.

첫 forecast와 continuation의128 chunk/123MB 읽기량은 이번 변경에서 그대로다. metadata45SQL
비용도 최적화했다고 주장하지 않는다. DB/publisher/파서·예산·의존성·UI/DTO/DDL은 바꾸지 않았다.
새 PR CI를 시작하며 전체 로컬 suite나 운영 export 부하 시험은 반복하지 않는다. 운영 지연·RSS 및
실제 cheap304 수용은 부모가 Transport 작업을 마친 뒤 별도로 확인해야 한다.
