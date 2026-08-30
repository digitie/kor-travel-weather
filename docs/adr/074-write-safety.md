# ADR-074: write safety

source record와 weather fact는 application guard만 믿지 않고 SQLite/PostgreSQL
DB trigger로 UPDATE/DELETE를 차단한다. admin location은 hard delete하지 않고
disable한다. fact가 있는 anchor의 좌표/grid 수정은 새 `location_id`를 요구한다.
Dagster는 full stage 후 complete manifest를 단일 transaction으로 publish한다.
