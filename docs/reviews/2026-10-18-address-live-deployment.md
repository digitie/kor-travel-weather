# Weather #115 운영 배포·주소 갱신 수용

2026-10-18 제품 10b59dc3e7ea614ab08f5b0a8a7bba71b6c35afe를 Odroid에 배포했다.
공용 webserver의 영속 예약 mutation을 먼저 적용한 뒤 Weather API·code server·웹을 순차 교체했다.
빌드·이미지·배포 파일과 비공개 백업은 NVMe를 사용했다. 기존 healthcheck·실행 인자·자원 설정을
유지했고 정상 기동, 다른 서비스의 생명주기 및 기존 대기열 보존을 확인했다. Daemon은 OFF다.

## 운영 DB와 실제 제공자

마이그레이션 0021_reverse_addresses 적용 후 READ ONLY 조회로 테이블 2개와 보조 인덱스 3개를
확인했다. 기존 활성 위치 한 곳에 실제 VWorld/Kakao resolver를 실행해 processed1/requests1/failed0,
정상 파생 주소·확인일·다음 갱신일 저장을 확인했다. 검사 전 sidecar는 0/0행, 이후 각각 1행이다.
실제 성공 제공자는 VWorld다. Kakao fallback 실호출은 이번 검사에서 수행하지 않았다.
확인일 2026-10-18T06:43:46.099939Z와 다음 갱신일 2027-04-18T06:43:46.099939Z를
조회해 KST 달력 6개월과 일치함을 확인했다. 자동 tick 실행 검증과는 구별한다.

검사 중 SQL guard는 sidecar 두 테이블 외 쓰기를 거절했다. 원천 위치 1,450행의 전체 해시가
전후 동일했다. 기상 fact 전체를 재해시한 검사는 아니며 원천 주소·좌표·격자를 치환하지 않는다.

## 실제 UI

- 기존 운영 UI 36개: WSL Chromium에서 36 PASS. 로그인·지도 marker·목록·이력·Dagster native
  identity·실패 상세·503/네트워크 오류 복구·320/375/414/768/1440 화면·로그아웃을 포함한다.
- 새 주소 기능 10개: n150 Chromium에서 10 PASS. 실제 로그인·summary/파생 주소,
  1365/390 화면, 미인증 401·잘못된 Origin 403, 수동 QUEUED 202,
  동일 요청 ID의 동일 run 재사용·다른 요청의 중복 409·로그아웃을 확인했다.
- 새 주소 기능 응답은 fixture가 아니다. 기존 UI 오류 복구 항목은 의도적인 네트워크 장애 주입이다.
- n150에서 기존 native identity SSH helper를 실행할 연결이 없어 기존 36개는 WSL에서 수행했다.
  최초 33 PASS/3 FAIL은 연결 치환 후 남은 옛 target 파일 assertion 때문이었다. 해당 하니스 오류만
  수정한 새 실행에서 같은 36개 기준을 통과했다. 첫 실패를 삭제하거나 제품 성공으로 세지 않았다.
- 수동 예약은 daemon OFF 상태의 QUEUED 수용이다. 자체 생성 run 하나만 job/location/request tag를
  대조한 뒤 native SAFE_TERMINATE로 취소했고, 다른 queued 3개의 ID는 보존했다.

## 정확 산출물과 근거

| 역할 | 실제 이미지 SHA256 |
| --- | --- |
| API | ba2844c89b6d651adfa910e6c665fc9b1866caac9a506688ec791921b7b8e79a |
| Code server | 99459fa40ed9ad4c97027e135fcc867dc17c30ace143dcfe6fc5107531653ff5 |
| Web | 1faf937af51cd3d8acb9fdf774592190c5d810736ea9d33da41aa4224fd36f09 |

운영 종합 보고서 SHA256은 8a3d728ce597fc8e008179d1f9cf9a335a8d11749ce2ab549f92b2a8d92e29e6,
근거 manifest는 ffab920a6feca8980f466e869549c851fcf2276cdfec8c7234ac64902341326d다.
제공자·달력 보조 증거 SHA256은 3252eda0273e4d671bd6620c80ddc95713f387964f833303a1e09f17098ae1a3다.
제품의 두 독립 리뷰와 정확 CI는 [기존 종결 기록](2026-10-18-native-address-final.md)에 보존했다.
이 문서 PR은 운영 결과만 기록하며 사용자 요청에 따라 CI를 재실행하지 않는다.

자동 6개월 tick, 전체 1,450곳의 provider sweep, daemon ON 수집 완주, 전후 CPU·RSS 개선은
아직 운영 수용하지 않았다. 단일 대상 성공을 전체 대상·쿼터·모든 fallback 성공으로 확대하지 않는다.
Track 1·2의 남은 운영 수용 후 수집 CPU·메모리 개선을 진행한다.
