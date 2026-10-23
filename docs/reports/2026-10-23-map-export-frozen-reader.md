# Map export W1/W2 frozen REST reader

상태: 구현·검증 중. 운영 설정은 기본OFF이며 전국/전체provider/W3/W4/W5 수용 완료가 아니다.

- W1 location과 W2 승인된 KMA 실황을 실제 REST로 제공하는 opt-in reader다. 기존 service header 인증을 유지하고 승인 generation vector/catalog를 동일MVCC에서 고정한다. 기존 mutable current/history/provider API를 재조회하지 않는다.
- cursor는 기존 token에서 분리한 HMAC key로 서명하고 resource/limit/snapshot/위치에 묶는다. TTL15분 만료는410/첫페이지 재시작, 변조/조건 변경은422다. head가 두 번 바뀌어도 기존 cursor의 data vector는 고정된다. generation reader 참조는 FK로 보존한다.
- 한 페이지는5000항목/4MiB 안에서 중단하고 다음cursor를 준다. 파일은 같은fd의 hash/byte/point를 검증하며 파일 I/O 중 PG연결을 반환한다. reader concurrency2, artifact guard 획득대기2초다. 전체 wall deadline/느린디스크 중단이나 GC완료를 보장한다는 뜻은 아니다.
- ETag에는 고정data/vector/page와 현재 source_status를 포함하고 응답 generated_at은 제외한다. If-None-Match는weak/목록을 지원하고304는본문이없다. gzip은협상(q=0존중)후적용한다.
- KMA의 필수8실황category(T1H/RN1/UUU/VVV/REH/PTY/VEC/WSD)를 증명하는 별도 coverage policy namespace를 사용한다. SDKpagination완결이어도 필수category가누락되면 export만실패시키고 이전head와 원본fact성공을보존한다. 기존nonempty내부계약을전국완결로간주하지않는다.
- 미구현 provider를 빼고 전체정상200으로 내보내지 않는다. W2는현재명시`provider=python-kma-api`또는KMA만설정한환경을지원하며, 다른provider가필요한전체조회는503이다. W1도활성location전체에strict성공head가없으면503이다. 현재W3/W4/W5는구현완료가아니다.
- 새수집실패·검증불충분·봉인됐으나승격되지않은후속원천은이전집합을stale로표시한다. freshness는조회clock뿐아니라원천baseage도검사한다. 누락/손상artifact는빈200대신503이다.
- 법정동/시도/시군구는기존common reverse address의현재coordinate_key와일치할때만쓴다. geo/외부주소API를호출하지않는다.

이전기본reader 집중18PASS71.07초, migration포함별도21회귀PASS였으며 strict8category/최종source상태변경후의전체/집중/CI·2적대리뷰·실제TCP API/UI는진행예정이다. 비용gate/GC·전국초기적재·운영활성화는미완이다.

## 적대 리뷰와 이전 성공 유지 보강

- ETag에 snapshot handle의 epoch(snapshot_id)를 포함한다. 같은data/vector라도 TTL후 새cursor가발급되면304로옛만료cursor를재사용하지않도록200/새ETag를준다. clock+16분·동일manifest·새cursor정상/옛cursor410회귀를추가한다.
- 새catalog scope가아직검증되지않으면이전승인manifest를stale로유지한다. readerTTL과승인data보존기간을분리하고만료된handle은새TTL handle로다시고정한다. 기존frozen cursor도현재catalog roster불일치를stale로표시한다. 최초승인집합이없으면503이다.
