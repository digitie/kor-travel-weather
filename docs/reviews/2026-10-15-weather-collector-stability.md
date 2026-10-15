# Weather 수집 안정성과 자원 사용 개선

## 산악 관측의 기온 편향 제거

산악 client의 최신 10분 관측 선택은 기존에 2m 기온 하나만 확인했다.
기온이 없고 풍속·습도·10m 기온·강수량만 있는 유효 관측도 이전 시각으로
되돌아가거나 빈 결과가 될 수 있었다. 정규화가 이미 지원하는 관측 필드 중
하나라도 값이 있으면 그 최신 시각을 사용하도록 바꿨다. 0은 유효한 값이며
모든 필드가 None인 관측소 목록에는 기존의 유한한 이전 시각 조회를 유지한다.
원천 raw·source_record_key·좌표 규칙·원천 client의 public API는 바꾸지 않았다.

원본 f60에서 새 회귀는 4 FAIL/2 PASS였다. 수정 후 관련 regional suite38 PASS,
Ruff와 diff 검사 PASS다. 테스트는 별도 소유 localhost PostgreSQL16의 전용 DB를
사용하며 운영/다른 작업자의 테스트 DB를 사용하지 않았다.

이 필터 결함은 코드와 로컬 재현으로 확인한 것이다. 운영의 산악 수집 지연을
이 결함 하나로 확정하거나 CPU 절감률·live 검증 완료를 주장하지 않는다.
executor 변경, provider job 분리, cadence 변경은 이 수정에 포함하지 않는다.

## Regional 정규화 메모리 제한

원천 응답은 기존 record 상한으로 받되, 정규화한 source/value는 전체 run만큼
쌓지 않고 현재 게시 chunk만 보관한다. 기존 50위치/5000값 경계와 전체 max_values를
그대로 사용한다. 한 원천 관측의 metric set은 나누지 않으므로 단일 관측 자체가
chunk값 상한보다 크면 그 한 관측은 온전히 게시한다. 최대 메모리의 단위는
현재 chunk와 다음 관측 하나이며, 원천 client가 반환한 응답 목록은 여전히 메모리에 남는다.

입력 순서와 전체 budget의 절단 위치를 유지한다. 같은 위치의 서로 다른 관측이
입력에서 떨어져 있으면 다른 transaction으로 게시할 수 있지만 각 source와 그
모든 metric은 같은 transaction이다. 늦은 원천 변환/게시 실패 때 이전 chunk는
남고 sync run은 failed로 저장량을 보존한다. 재수집은 기존 unique/upsert와 lineage
계약으로 중복을 막는다. 전체 위치 재정렬이나 두 번 정규화는 하지 않는다.

원본에 새 회귀2개를 적용하면 2 FAIL이었다. 수정 후 regional·chunk suite47 PASS다.
실제 전용 PostgreSQL16에서 첫 게시가 전체 정규화 전에 발생함, 동일 위치의 다른
raw 관측의 chunk 분리, 중간 실패의 부분 게시·failed 저장량, 재수집 후 source3개와
fact27개(9 metric씩)의 중복 없는 결과를 확인했다. 기존 전체 max_values·0값·빈 결과
회귀와 Ruff/diff 검사도 통과했다.

동일 synthetic 산악2000관측×9 metric=18000값을 실제 정규화 함수로 3회씩 비교했다.
원천 입력은 측정 전에 만들고 DB는 저장 없이 동일 hash/count를 확인하는 sink로 대체했다.
sources2000·values18000·최종 digest가 전후 동일했다.

| 측정 항목(3회 중앙값) | 이전 | 변경 후 |
| --- | --- | --- |
| tracemalloc peak bytes | 39,663,286 | 1,027,869 |
| wall time 초 | 1.690 | 1.686 |
| process CPU time 초 | 1.690 | 1.686 |

이는 정규화 중 추적한 Python 할당의 peak이며 운영 RSS·DB 지연·원천 HTTP 시간의
측정이 아니다. CPU 차이는 작아 CPU 개선으로 판정하지 않는다. 별도 비공개
재현 도구와 원본 측정3회 값은 인계 증거에 보존한다.

## 공용 anchor의 게시 잠금과 catalog 잠금 분리

기존 fact 게시가 location advisory exclusive와 행 FOR UPDATE를 잡아, 같은 anchor에
서로 다른 원천·시각의 fact를 쓰는 작업도 전체 transaction 동안 직렬화됐다.
게시 경로와 skip-locked 게시는 advisory shared + 행 FOR KEY SHARE를 사용한다.
catalog upsert/create/patch/grid 변경은 기존 exclusive + FOR UPDATE를 유지한다.
소스 record의 배타 잠금, fact/projection unique/upsert, 정렬된 잠금 획득 순서와
기존 유한 rollback/retry 예산은 유지한다. SQL schema·pool·메모리·동시성 설정은 바꾸지 않는다.

동일 source/projection 자체가 겹치면 해당 고유 행의 직렬화는 여전히 필요하다.
이 변경은 서로 독립적인 게시가 공유 anchor만으로 기다리는 경우를 줄이는 것이다.
앱의 catalog 변경 경로는 advisory 규칙을 따르며, 이를 우회한 임의 SQL 쓰기까지
동일한 행 갱신 배타성을 보장하지 않는다. FK key 삭제 보호는 행 KEY SHARE가 유지한다.

실제 PostgreSQL에서 첫 publisher를 commit 직전 정지해 두고, 두 번째 독립 fact를
게시하는 새 회귀가 원본에서는 실패하고 수정 후 통과했다. catalog 4경로의 배타 대기는
thread 시작 여부 대신 실제 pg_locks의 ungranted advisory로 확인한다.
shared 캐시가 exclusive 승격이나 다음 transaction을 대신하지 않는지, shared publisher가
skip 이유가 되지 않는지, FK key 삭제 보호와 기존 admin 충돌 skip을 검증한다.
실제 운영 job의 lock 실패율·처리량·CPU 개선과 live E2E는 아직 검증하지 않았다.
