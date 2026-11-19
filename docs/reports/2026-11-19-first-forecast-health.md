# 예보 첫 페이지 승인 메타데이터 중복 제거 — 2026-11-19

## 문제와 변경

`49dc2be`의 일반 예보 첫 페이지는 RR freeze에서 전체 승인 위치·plan·head·owner·run·cohort를 확인한 뒤 본문 생성 후 health에서 같은 승인을 다시 읽었다. 조건부 첫 페이지에는 이미 동일 요청의 fresh health proof를 재사용하는 경로가 있다.

동일 경로를 일반 첫 페이지에도 적용한다. 매 issue/재시도 전에 proof를 비우고 이전 승인·capacity fallback이면 폐기한다. 본문 생성 **뒤 실제 현재 시각**이 승인 시각 이상이고 모든 위치의 다음 판정 경계 미만일 때만 그 RR의 결과를 쓴다. 경계 동시 도달·clock rollback·proof 부재는 기존 full health다. cursor는 이 proof를 넘기지 않는다. 상위 page cache의 snapshot identity·삭제 epoch·세대/plan·artifact 존재·원래 deadline 검산도 유지한다. 새로운 전역 캐시·TTL·의존성·schema·응답 필드는 없다.

## 검증

실제 PostgreSQL에서 집중45건 통과(59.07초)했다. 새10건은 short/ultra_short × limit1/200/2000의 전체 본문·ETag·cursor, 본문 도중 freshness/expiry/역행 clock, 첫 성공 후 재시도/fallback의 실패 상태를 검증한다. 기존 조건부19건과 catalog 투영16건도 포함했다. 기존 대규모 timing 시험1건은 아래 독립 교대 실험으로 대체해 선택하지 않았으며 CI 전체 실행은 별도다.

기존 경계 시험의 내부 함수 호출 단정은 실제 full 승인 재조회도 허용하도록 수정했다. 진단에서 경계 전 proof는 폐기되고 경계 뒤 새 RR 승인이 `stale`을 반환했지만, 이후 그 새 proof를 사용해 특정 full-health 함수 호출만 없어진 경우를 확인했다. 새 단정은 경계 뒤 실제 승인 시각·현재 clock·다음 경계를 모두 대조하며 역행 clock은 여전히 full-health 호출을 요구한다. 기존 HTTP·본문·ETag 단정은 유지한다.

첫 shared local fixture 실행은240초 harness timeout으로 중단돼 통과로 세지 않았다. 그 실행의 전용 임시 DB만 제거했으며 shared fixture는 보존했다. 정확히 같은 기존 PostgreSQL 이미지를 사용한 별도 임시 tmpfs fixture에서 위45건과 아래 실험을 완료했다. raw 출력은 별도로 보존했고 운영 DB·수집·API 요청은0건이다.

## 같은 입력의 첫 HTTP 응답 비교

기존 Git49dc의 `_page_guarded` 메서드와 새 메서드를 같은1,452개 위치 fixture에서5쌍 교대로 실행했다. 매 요청은 새 앱의 빈 page cache에서 시작하며 DB·프로세스 descriptor memo는 강제로 cold로 만들지 않았다. limit200, 실제 출력144개 위치의85,596byte 논리 본문과 ETag가10회 모두 같았다. 입력 artifact byte 상한에 따른 출력 제한도 유지됐다.

| 관측 | 기존 | 변경 |
| --- | ---: | ---: |
| 중앙 HTTP 시간 | 297.245ms | 260.158ms |
| 중앙 테스트/API 프로세스 CPU | 186.304ms | 155.626ms |
| SQL 문장 수 | 최초43, 이후42 | 31 |
| 전체 승인 집합 읽기 | 2 | 1 |

이 로컬 fixture에서는 중앙 HTTP12.48%, 프로세스 CPU16.47%가 줄었다. PostgreSQL 서버 CPU는 이 CPU 수치에 포함하지 않았다. 운영 첫 조회의1초 목표, SQL503의 영구 해소, 전체36페이지 적재 시간·신선도 회복은 **미검증**이다. 새 실제 이미지 배포 후 호스트 경합과 함께 검증한다. 현재49dc 이미지 배포는 이 후속 후보 때문에 대기시키지 않는다.

## 리뷰·배포 경계

독립 A/B 좁은 source 리뷰는 제품·두 시험 파일의 정확한 SHA256에 결박해 P1/P2 지적0건으로 통과했다. reviewer는 시험/운영 호출을 실행하지 않았다. 본문·신선도·fallback 계약은 운영 시간 전체를 선형화한 보장이 아니라 동일 RR에서 관측한 승인 상태이며, 새 전역 lease로 연장하지 않는다. 정확 PR CI·merge·정상 cached image/native/SDK·실제 요청 수용은 후속이다. Map 소스·CI·이미지·스케줄은 변경하지 않는다.
