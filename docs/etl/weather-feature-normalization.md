# Weather normalization

`metric_key`는 KMA category의 안정적인 대문자 key다. `metric_name`과 `unit`은
catalog metadata이고 숫자 소비자를 위해 `value_number`, qualifier/코드 보존을
위해 `value_text`를 병행한다. `valid_from`/`valid_until`은 중기 기간을, 단일
시각은 `target_at`/`valid_at`을 사용한다. 공개 consumer는 provider 원문이
아니라 이 정규화 필드에 의존해야 한다.
