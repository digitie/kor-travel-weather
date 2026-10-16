# Weather86~106 최종 merge 준비 독립 감사 D

판정: **현재 HOLD — #87 제품 두 독립 FULL 검토와 #106 최신 HEAD Python CI가 남음.**
새 코드 결함을 주장하는 감사가 아니다. 원 리뷰 binding/현재 Git 계보/CI/머지 충돌을 확인했다. source·PR·원 WT·운영 변경0이며 제품별 기존 리뷰를 새 독립 제품 리뷰 수로 합산하지 않는다. D는106 backend 작성자이므로106 독립 승인 인원에서 제외한다.

## 현재 확인한 것

- GitHub main은 f60d2c21a98973aca62e5623b26488430c850514. PR86~106 모두 OPEN/DRAFT/MERGEABLE이다. GitHub submitted review는 전부0이며, 이를 로컬 독립 원문이 없다는 뜻으로 해석하지 않는다.
- 86~105 exact current HEAD는 python/frontend 모두 SUCCESS다. 106 최신39e26fcdf17bc24473277fec28370e1a781e54a6는 2026-10-16T16:14:17Z 조회에서 frontend SUCCESS, python IN_PROGRESS/UNSTABLE이다. source582 CI SUCCESS를 docs39e CI SUCCESS로 대신하지 않는다. [정확106 CI](https://github.com/digitie/kor-travel-weather/actions/runs/38929861022)
- 모든21개 HEAD는39e26fc의 실제 ancestor다. 원 소스를 변경하지 않고 자기 임시 bare object DB에서 현재main→86→…→106 각각 merge-tree와 가상 merge commit을 만들었다. **21단계 전부 conflict0이며 최종 tree 8b9c4745df6a8ddd39a150ba36d1dd0a440df702는39e26fc tree와 exact**다. 이전 감사의86 ancestry 누락/103 미완 가설은 현재 상태가 아니다.
- source582→39e26fc 변경12경로는 docs/evidence뿐이다. product/tests/dependencies byte는 보존됐다.96 aec→441,102074f→327,10308373→77의 차이도 각각 문서·공개 증거 전용이다.

## 실제 남은 차단 항목

1. **#87 acc8c71 제품 검토 증거**: 공개 검색에서 산악 gate/streaming normalization/anchor lock 제품의 두 독립 FULL 원문을 찾지 못했다. weather87-controller B/Geo postfix는 운영 Env 보존 제어기 검토이므로 제품 검토로 합산하지 않았다. root도 같은 미확인을 확인하고 A/D 신규 FULL을 배정했다. 미확인을 '과거 반드시 미검토'로 단정하지 않는다. 이 감사 뒤 D는 별도 immutable 제품 검토를 수행하며 A 원문을 읽지 않는다.
2. **#106 exact latest CI 종료**: Python terminal SUCCESS를 확인해야 한다. 현재 진행 중인 상태는 실패가 아니지만 green도 아니다.
3. 전21개가 draft다. 앞 두 항목 해소 후 root가 순서대로 main retarget/ready/mergecommit을 수행한다. retarget 후 required checks/리뷰 정책이 바뀌면 실제 GitHub 상태에 따라 처리한다. branch protection의 강제 규칙은 본 감사에서 조회하지 않았다.

이외 현재 공개 evidence에서 새 OPEN P1/P2는 발견하지 않았다. 오래된 PR본문이 '리뷰 대기/live 실패'라고 적은 것은 아래 실제 후속 원문과 구분한다. 최신 closure 링크로 PR 설명을 정산하는 문서 작업은 필요하지만 과거 전체 suite/옛 제품을 전부 재검증하자는 의미가 아니다.

## 현재 HEAD와 독립 binding

| PR | exact HEAD 앞12자 | 현재 CI | 독립 제품 계보 |
| --- | --- | --- | --- |
| [86](https://github.com/digitie/kor-travel-weather/pull/86) | 969cf51370e0 | 2 SUCCESS | B/C 최종+기존 delta 계승 |
| [87](https://github.com/digitie/kor-travel-weather/pull/87) | acc8c71fb2df | 2 SUCCESS | 미확인 → A/D 별도 FULL 신규 배정 |
| [88](https://github.com/digitie/kor-travel-weather/pull/88) | 974a76ba560c | 2 SUCCESS | B/Geo |
| [89](https://github.com/digitie/kor-travel-weather/pull/89) | e25e6ca7bc7c | 2 SUCCESS | B/Geo |
| [90](https://github.com/digitie/kor-travel-weather/pull/90) | 40caeaba5752 | 2 SUCCESS | A/ManagerB + docs closure |
| [91](https://github.com/digitie/kor-travel-weather/pull/91) | 32c63ddc5f4c | 2 SUCCESS | A/D |
| [92](https://github.com/digitie/kor-travel-weather/pull/92) | ad619efdd449 | 2 SUCCESS | C/D |
| [93](https://github.com/digitie/kor-travel-weather/pull/93) | ee434cb63b19 | 2 SUCCESS | A postfix/D |
| [94](https://github.com/digitie/kor-travel-weather/pull/94) | 388866903ce9 | 2 SUCCESS | C/D fixture closure |
| [95](https://github.com/digitie/kor-travel-weather/pull/95) | 984d50f272f7 | 2 SUCCESS | C/D |
| [96](https://github.com/digitie/kor-travel-weather/pull/96) | 441d7abf9d17 | 2 SUCCESS | A/D aec + 문서전용441 |
| [97](https://github.com/digitie/kor-travel-weather/pull/97) | 1c968f324e20 | 2 SUCCESS | C/D 통합1c968 |
| [98](https://github.com/digitie/kor-travel-weather/pull/98) | fd737d56a043 | 2 SUCCESS | C/D + pin-test closure |
| [99](https://github.com/digitie/kor-travel-weather/pull/99) | f1d59f79bb72 | 2 SUCCESS | A/D + 최종 docs |
| [100](https://github.com/digitie/kor-travel-weather/pull/100) | 43efb28d87ea | 2 SUCCESS | A/D + 최종 docs |
| [101](https://github.com/digitie/kor-travel-weather/pull/101) | 916715d7ca94 | 2 SUCCESS | A/C |
| [102](https://github.com/digitie/kor-travel-weather/pull/102) | 327eadf39499 | 2 SUCCESS | C/D + docs closure |
| [103](https://github.com/digitie/kor-travel-weather/pull/103) | 77b4606bed8b | 2 SUCCESS | A/D FULL→future→index→test |
| [104](https://github.com/digitie/kor-travel-weather/pull/104) | 0acb11fc94d1 | 2 SUCCESS | A/D + 통합 scope |
| [105](https://github.com/digitie/kor-travel-weather/pull/105) | 270a4e5388cd | 2 SUCCESS | D/C |
| [106](https://github.com/digitie/kor-travel-weather/pull/106) | 39e26fcdf17b | FE SUCCESS / Python 진행 | A/C (D 작성자 제외) |

표의 전체 원문 경로·각 SHA256·검토 HEAD 토큰은 동명 review-bindings.safe.json에 보존했다. 원문 본문을 읽는 것은 이번 독립 제품 판정이 끝난 뒤의 증거 통합 감사이며 새 제품 리뷰로 합산하지 않는다. 86 최종 B/C는969 식별자 delta이고 기존 BFF/provider/worker 검토 계보를 계승한다.91 초기 str-subclass P2,93 limiter P2,94 문서/fixture P2,103 future P2의 실패 및 closure 역사도 현재 판정과 분리했다.

현재 Git에 보관된102/103/104/105/106 evidence17개 MD의 SHA를 직접 계산했다. selected 원문과 같은 hash를 확인했으며,103의 초기 FULL/future는 closure 표의 hash와 연결된다. frontend105는 D+C, backend106은 A+C이고 author A/D 자료 두 개를 독립 review로 세지 않는다. 상세 [현재 통합 공개 검증 문서](https://github.com/digitie/kor-travel-weather/blob/39e26fcdf17bc24473277fec28370e1a781e54a6/docs/reviews/2026-10-17-weather-marker-performance.md)에 공개 원문 링크가 있다.

## merge 순서

**86 → 87 → 88 → 89 → 90 → 91 → 92 → 93 → 94 → 95 → 96 → 97 → 98 → 99 → 100 → 101 → 102 → 103 → 104 → 105 → 106**.

각 PR을 순서대로 main으로 retarget한 뒤 기존 branch와 commit을 유지하는 merge commit을 사용한다. squash/rebase/조기 branch deletion을 피한다.90/91은89에서 갈라졌지만92에 이미 두 계보가 포함된다.102/103도101의 형제지만 현재 순차 병합 모형은 clean이며104가 통합 계보를 보유한다.86의5개 frontend 기능은97에서 보존됐고 현재86 자체 ancestry도 포함되므로 다시 포팅하지 않는다.106 한 개만 merge하고 부모를 수동 close하는 방법은21개 모두의 실제 MERGED 완료와 다르다.

최종 main 제품 tree가 시험한582와 같은지, 문서까지39e와 같은지는 별도 비교한다. 이번 모형은 현재 frozen heads의 clean 결과이며 그 사이 다른 main/feature 변경이 있으면 이 결과를 그대로 새 입력에 적용하지 않는다. 제품 source를 바꾸지 않는 retarget·문서 정산만으로 과거 전체 회귀를21회 반복할 근거는 없다.

## 운영 증거의 출처와 한계

root가 보고한 actual582 API/CS/UI 설치, 전체628 pytest/frontend202/type/lint/Ruff, R25 동일36케이스36PASS/0FAIL·25초·692identity·12정상200은 **root 관측 및 현재 공개 문서에 기록된 결과**다. D는 실제 runtime/브라우저/DB/이미지·private 증거를 새로 읽지 않았으므로 이를 본인 독립 운영 PASS로 발급하지 않는다. 공개 R25/시간 JSON이 Git에 추가된 것은 확인했지만 원 실제 세션을 재실행하지 않았다.

R25는 초기 단기예보 삭제 전에 수행됐다. 전체 수집 job/freshness·후속 retention 실제 삭제/보존·90초 이상 같은 서비스 집합의 idleCPU 비교는 별도 운영 범위다. liveUI 통과를 whole-host CPU 개선으로 바꾸지 않는다. 이전 R24 및 index/제어기 실패는 삭제하지 않고 각 계보로 남긴다. 이 부가 운영 범위를 소스 merge 준비와 혼동하여 새로운 무한 gate로 확대하지 않는다.

## 근거와 실행 범위

기존 weather-draft-stack-merge-plan-a-r1-20261016.md를 읽고 그 오래된 snapshot을 이번 현재 상태로 갱신했다. 본 감사의 GH/public JSON, git.safe.json, review-bindings.safe.json, final-check.safe.json 및 safe.json은 같은 prefix로 별도 저장했다. 모든 해시는 safe.json에 결박했다. 새 full test·코드수정·PR mutation·SSH·PG·Docker·SDK·private/auth/prod proc 접근은0이며, Git simulation은 자기 bare scratch에만 썼다. 모든 도구 terminal.
