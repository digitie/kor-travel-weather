"""후보는 가벼운 복합 키로 비교하고 승자의 원본 fact만 hydrate한다."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
from sqlalchemy.sql.elements import BinaryExpression
from sqlalchemy.sql.selectable import Join

from kortravelweather.repository import WeatherRepository, WeatherValueRow

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)


def _row(value_id, *, known_at=NOW, location_id="one", metric_key="TEMP", source="source"):
    return WeatherValueRow(
        value_id=value_id, known_at=known_at, location_id=location_id,
        metric_key=metric_key, provider="fixture", dataset_key="fixture",
        weather_domain="weather", forecast_style="observed",
        value_number=Decimal("20"), target_at=NOW, collected_at=NOW,
        normalization_version="fixture", source_record_key=source,
        payload={"raw": value_id, "nested": {"preserved": True}},
    )


def _key(row):
    return SimpleNamespace(**{
        field: getattr(row, field)
        for field in ("location_id", "metric_key", "known_at", "source_record_key", "value_id")
    })


def _run(observed, *, fallback=(), facts=(), alerts=(), locations=("one",)):
    captured = []
    candidates = iter((observed, fallback))

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, stmt):
            # 이 단계에서 payload/전체 ORM을 선택하면 실제 반환 행 수와 별개로 실패한다.
            assert list(stmt.selected_columns.keys()) == [
                "location_id", "metric_key", "known_at", "source_record_key", "value_id",
            ]
            captured.append(("candidate", stmt))
            return SimpleNamespace(all=lambda: [_key(row) for row in next(candidates)])

        def scalars(self, stmt):
            joins = [value for value in stmt.get_final_froms() if isinstance(value, Join)]
            if any(getattr(value.right, "name", "") == "marker_winner_keys" for value in joins):
                captured.append(("winner", stmt))
                params = stmt.compile(dialect=PGDialect_psycopg()).params
                pairs = set(zip(
                    params["marker_winner_value_ids"],
                    params["marker_winner_known_ats"],
                    strict=True,
                ))
                rows = [
                    row for row in reversed(facts)
                    if (row.value_id, row.known_at) in pairs
                ]
            elif any(
                getattr(value.right, "name", "") == "marker_fact_keys"
                for value in joins
            ):
                # 이전 구현과의 출력/원본 비교에도 같은 fixture를 사용한다.
                captured.append(("candidate_orm", stmt))
                rows = list(next(candidates))
            else:
                assert any(
                    getattr(value.right, "name", "") == "limited_marker_alert_values"
                    for value in joins
                )
                captured.append(("alerts", stmt))
                rows = list(alerts)
            return SimpleNamespace(all=lambda: rows)

    repo = WeatherRepository.__new__(WeatherRepository)
    repo._session_factory = Session
    converted = []
    original = repo._value_model

    def convert(row):
        converted.append((row.value_id, row.known_at))
        return original(row)

    repo._value_model = convert
    result = repo.marker_values_many(locations, now=NOW)
    return result, captured, converted


def test_marker_only_winners_hydrate_and_raw_lineage_is_preserved():
    candidates = [
        _row(str(index), known_at=NOW - timedelta(minutes=4 - index))
        for index in range(4)
    ]
    fallback = _row("fallback", metric_key="SKY")
    result, queries, converted = _run(
        candidates, fallback=[fallback], facts=[*candidates, fallback],
    )
    assert converted == [("3", candidates[-1].known_at), ("fallback", NOW)]
    assert [row.metric_key for row in result["one"]] == ["TEMP", "SKY"]
    assert result["one"][0].payload == candidates[-1].payload
    assert result["one"][0].source_record_key == candidates[-1].source_record_key
    assert [kind for kind, _ in queries] == ["candidate", "candidate", "winner", "alerts"]


def test_marker_winner_keeps_python_tie_order_and_current_preference():
    # DB locale의 정렬 결과를 가정하지 않고 기존 Python tuple 비교를 그대로 쓴다.
    rows = [_row("winner", source="Z"), _row("other", source="a")]
    result, queries, converted = _run(rows, facts=rows)
    assert converted == [("other", NOW)]
    assert result["one"][0].source_record_key == "a"
    fallback = queries[1][1].compile(dialect=PGDialect_psycopg()).params
    assert ("one", "TEMP") not in set(zip(
        fallback["marker_missing_locations"], fallback["marker_missing_metrics"], strict=True,
    ))


def test_marker_winner_rejoin_is_exact_composite_pair_not_uuid_only():
    winner = _row("same", known_at=NOW - timedelta(days=1))
    shadow = _row("same", known_at=NOW, location_id="unrequested")
    result, queries, converted = _run([winner], facts=[winner, shadow])
    assert converted == [("same", winner.known_at)]
    assert set(result) == {"one"}
    assert result["one"][0].known_at == winner.known_at
    query = next(stmt for kind, stmt in queries if kind == "winner")
    join = next(value for value in query.get_final_froms() if isinstance(value, Join))
    assert {
        (part.left.key, part.right.key)
        for part in join.onclause.get_children()
        if isinstance(part, BinaryExpression)
    } == {("value_id", "value_id"), ("known_at", "known_at")}


def test_marker_winner_hydration_preserves_selection_order():
    rows = [_row("sky", metric_key="SKY"), _row("temp")]
    result, _, converted = _run(rows, facts=rows)
    assert [row.metric_key for row in result["one"]] == ["SKY", "TEMP"]
    assert converted == [("sky", NOW), ("temp", NOW)]


def test_marker_disappeared_winner_is_not_replaced_by_another_partition():
    winner = _row("same", known_at=NOW - timedelta(days=1))
    shadow = _row("same", known_at=NOW)
    result, _, converted = _run([winner], facts=[shadow])
    assert result == {"one": []}
    assert converted == []


def test_marker_alerts_keep_full_payload_and_existing_reducer():
    alert = _row("alert", metric_key="ALERT")
    alert.value_number = None
    alert.value_text = "호우주의보 발표"
    alert.weather_domain = "weather_alert"
    alert.valid_until = NOW + timedelta(days=36500)
    alert.payload = {"stn_id": "fixture", "seq": "1", "raw": {"kept": True}}
    result, queries, converted = _run([], alerts=[alert])
    assert result["one"][0].payload == alert.payload
    assert converted == [("alert", NOW)]
    assert [kind for kind, _ in queries] == ["candidate", "candidate", "alerts"]


def test_marker_winner_query_structure_cache_reused_for_different_pair_counts():
    one = _row("one")
    two = _row("two", location_id="two", known_at=NOW - timedelta(days=1))
    _, first, _ = _run([one], facts=[one])
    _, second, _ = _run([one, two], facts=[one, two], locations=("one", "two"))
    a = next(stmt for kind, stmt in first if kind == "winner")
    b = next(stmt for kind, stmt in second if kind == "winner")
    assert a._generate_cache_key().key == b._generate_cache_key().key
    a_params = a.compile(dialect=PGDialect_psycopg()).params
    b_params = b.compile(dialect=PGDialect_psycopg()).params
    assert a_params != b_params
    assert len(b_params["marker_winner_value_ids"]) == len(b_params["marker_winner_known_ats"]) == 2
