# EvilCharts ECharts 사용

공식 [EvilCharts](https://github.com/legions-developer/evilcharts)의 ECharts 엔진 시간별 선 그래프를 사용한다.
Recharts 엔진은 설치하지 않는다. Apache ECharts6.1.0과 motion14.13.0을 정확 버전으로 고정하며,
공식 합성 컴포넌트 소스6개를 `components/evilcharts`에 둔다.

- 원문 URL·registry/원본/설치본 SHA와 경로 변경은 `evilcharts-echarts.json`에 기록한다.
- MIT 원문은 `evilcharts-LICENSE`에 보존한다.
- import 경로와 client 경계를 앱에 맞췄다. 생성 CSS는 HTML 삽입 대신 style 텍스트 자식으로 전달한다.
- 공식 Children.forEach 슬롯 탐색은 유지한다. 해당 두 벤더 구간만 lint 예외 이유를 명시한다.
- 날씨 그래프를 표시할 때만 동적으로 불러온다. 0℃·영하를 보존하며 단위 불일치·누락·2시간 초과 공백을 선으로 잇지 않는다.
- 원래 시간별 수치 표도 유지한다. 그래프 로딩과 미제공 상태를 구분한다.

이 기록은 실제 브라우저·운영 성능 수용을 뜻하지 않는다. 빌드·배포는 별도 담당 범위다.
