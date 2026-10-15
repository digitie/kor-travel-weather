# 공용 UI 고정 산출물

토큰 원천: [kor-travel-common](https://github.com/digitie/kor-travel-common/tree/50d2db4c7c7e0154dd863defb62d4b9fa3b5bec3), commit `50d2db4c7c7e0154dd863defb62d4b9fa3b5bec3`.
`npm run build` 후 `npm pack ./packages/tokens`, `npm pack ./packages/ui`로 생성했다.
각 archive의 GPL LICENSE·NOTICE·THIRD_PARTY_NOTICES와 package metadata를 유지한다.
registry 발행 대신 이 개발 후보를 고정하며 추후 이관 시 버전과 lock을 함께 갱신한다.

| 산출물 | SHA-256 |
|---|---|
| kor-travel-tokens-0.1.0.tgz | `22b7613085e55885987ce05720a178b65ac649b097763db650b8c7b8b4acaa84` |
| kor-travel-ui-0.1.0-dev.6.tgz | `e4945d01d9eb89ed505a95b551899fd0ecf41be66c9ee6b76246701350447e6d` |

UI dev.6는 Common `091d4fe1dda8eacf43b453ad39b4f2e42e9e80fc`에서 `npm pack`으로 만들었다.
UI 소스는 main `efcd671207d7f9e2fb01251bab6854a8d543279a`와 동일하다.
실행 검색·상태 필터·50행 페이지·상세를 포함하며 인증과 API는 소비 앱이 소유한다.
