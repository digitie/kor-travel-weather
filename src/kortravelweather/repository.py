"""SQLAlchemy 기반 weather repository.

원본의 ``infra/*_repo.py`` raw SQL 경계를 단순화해, 도메인 model과 저장소를
분리했다. SQLite는 개발/fixture용이고 PostgreSQL은 같은 테이블 계약으로
운영할 수 있다. weather fact는 ``value_id``(identity hash)를 primary key로
사용하므로 재수집은 멱등 upsert가 된다.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    desc,
    event,
    func,
    nullslast,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from .models import SyncRun, WeatherLocation, WeatherValue, kst_now
from .settings import WeatherSettings, get_settings


class Base(DeclarativeBase):
    pass


class AwareDateTime(TypeDecorator[datetime]):
    """SQLite에서도 timezone-aware datetime을 round-trip하는 타입.

    SQLite는 timezone 정보를 저장하지 않으므로 UTC naive 값으로 저장하고,
    읽을 때 UTC tzinfo를 복원한다. PostgreSQL에서는 native timestamptz를
    사용하되 결과가 naive인 드라이버도 방어적으로 UTC를 부착한다.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        return dialect.type_descriptor(DateTime(timezone=dialect.name == "postgresql"))

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime은 timezone-aware여야 합니다.")
        if dialect.name == "sqlite":
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class WeatherLocationRow(Base):
    __tablename__ = "weather_locations"
    __table_args__ = (
        Index("ix_weather_locations_enabled_region", "enabled", "region_code"),
        Index("ix_weather_locations_coordinates", "latitude", "longitude"),
    )

    location_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    latitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    nx: Mapped[int | None] = mapped_column(Integer)
    ny: Mapped[int | None] = mapped_column(Integer)
    region_code: Mapped[str | None] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)


class SourceRecordRow(Base):
    """provider raw response lineage."""

    __tablename__ = "weather_source_records"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "dataset_key",
            "source_entity_type",
            "source_entity_id",
            "raw_payload_hash",
            name="uq_weather_source_records_identity",
        ),
        Index("ix_weather_source_records_dataset_fetched", "dataset_key", "fetched_at"),
    )

    source_record_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    dataset_key: Mapped[str] = mapped_column(String(160), nullable=False)
    source_entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    raw_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)


class WeatherValueRow(Base):
    __tablename__ = "weather_values"
    __table_args__ = (
        UniqueConstraint(
            "location_id",
            "provider",
            "dataset_key",
            "weather_domain",
            "forecast_style",
            "metric_key",
            "target_at",
            "source_record_key",
            name="uq_weather_values_identity",
        ),
        Index("ix_weather_values_location_time", "location_id", "valid_at", "observed_at"),
        Index("ix_weather_values_location_target_known", "location_id", "target_at", "known_at"),
        Index("ix_weather_values_dataset_metric", "dataset_key", "metric_key"),
    )

    value_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    location_id: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("weather_locations.location_id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    dataset_key: Mapped[str] = mapped_column(String(160), nullable=False)
    weather_domain: Mapped[str] = mapped_column(String(120), nullable=False)
    forecast_style: Mapped[str] = mapped_column(String(40), nullable=False)
    timeline_bucket: Mapped[str | None] = mapped_column(String(40))
    metric_key: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_name: Mapped[str | None] = mapped_column(String(200))
    source_metric_key: Mapped[str | None] = mapped_column(String(80))
    source_metric_name: Mapped[str | None] = mapped_column(String(200))
    value_number: Mapped[Any | None] = mapped_column(Numeric(14, 4))
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(32))
    severity: Mapped[str | None] = mapped_column(String(64))
    issued_at: Mapped[datetime | None] = mapped_column(AwareDateTime())
    valid_at: Mapped[datetime | None] = mapped_column(AwareDateTime())
    valid_from: Mapped[datetime | None] = mapped_column(AwareDateTime())
    valid_until: Mapped[datetime | None] = mapped_column(AwareDateTime())
    observed_at: Mapped[datetime | None] = mapped_column(AwareDateTime())
    target_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    known_at: Mapped[datetime | None] = mapped_column(AwareDateTime())
    normalization_version: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    collected_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    source_record_key: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("weather_source_records.source_record_key", ondelete="RESTRICT"),
        nullable=False,
    )


class SyncRunRow(Base):
    __tablename__ = "weather_sync_runs"
    __table_args__ = (Index("ix_weather_sync_runs_started", "started_at"),)

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    dataset_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime())
    locations_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    grids_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    values_loaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return
    path_text = database_url.removeprefix("sqlite:///")
    if path_text in {":memory:", ""}:
        return
    path = Path(path_text)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _metric_source_key(value: WeatherValue) -> str:
    """Legacy/custom DTO도 안정적인 local lineage를 갖도록 한다.

    Dagster는 실제 KMA response hash를 명시적으로 전달한다. 이 fallback은
    수동 fixture나 API 테스트에서만 사용하며 전체 response인 것처럼 가장하지
    않도록 ``metric_row`` entity type으로 기록한다.
    """
    canonical = json.dumps(
        [value.provider, value.dataset_key, value.location_id, value.identity(), value.payload],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return "sr_local_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:48]


class WeatherRepository:
    """동기 SQLAlchemy repository.

    API의 짧은 read/write와 Dagster의 batch upsert만 수행하므로 호출 경계는
    작고 명시적이다. 장시간 provider I/O는 repository 밖 Dagster가 소유한다.
    """

    def __init__(self, database_url: str) -> None:
        _ensure_sqlite_parent(database_url)
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        normalized_url = database_url
        if normalized_url.startswith("postgresql://"):
            normalized_url = "postgresql+psycopg://" + normalized_url.removeprefix("postgresql://")
        self.engine: Engine = create_engine(normalized_url, future=True, connect_args=connect_args)
        if normalized_url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        self._session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def _location_model(self, row: WeatherLocationRow) -> WeatherLocation:
        return WeatherLocation(
            location_id=row.location_id,
            name=row.name,
            latitude=float(row.latitude),
            longitude=float(row.longitude),
            nx=row.nx,
            ny=row.ny,
            region_code=row.region_code,
            enabled=row.enabled,
            metadata=dict(row.metadata_json or {}),
        )

    def _value_model(self, row: WeatherValueRow) -> WeatherValue:
        return WeatherValue(
            location_id=row.location_id,
            provider=row.provider,
            dataset_key=row.dataset_key,
            weather_domain=row.weather_domain,
            forecast_style=row.forecast_style,
            timeline_bucket=row.timeline_bucket,
            metric_key=row.metric_key,
            metric_name=row.metric_name,
            source_metric_key=row.source_metric_key,
            source_metric_name=row.source_metric_name,
            value_number=row.value_number,
            value_text=row.value_text,
            unit=row.unit,
            severity=row.severity,
            issued_at=row.issued_at,
            valid_at=row.valid_at,
            valid_from=row.valid_from,
            valid_until=row.valid_until,
            observed_at=row.observed_at,
            target_at=row.target_at,
            known_at=row.known_at,
            normalization_version=row.normalization_version,
            payload=dict(row.payload or {}),
            collected_at=row.collected_at,
            source_record_key=row.source_record_key,
        )

    def _sync_model(self, row: SyncRunRow) -> SyncRun:
        return SyncRun(
            run_id=row.run_id,
            provider=row.provider,
            dataset_key=row.dataset_key,
            status=row.status,
            started_at=row.started_at,
            finished_at=row.finished_at,
            locations_total=row.locations_total,
            grids_fetched=row.grids_fetched,
            values_loaded=row.values_loaded,
            error=row.error,
        )

    def upsert_location(self, location: WeatherLocation) -> WeatherLocation:
        now = kst_now()
        with self._session_factory.begin() as session:
            row = session.get(WeatherLocationRow, location.location_id)
            if row is None:
                row = WeatherLocationRow(
                    location_id=location.location_id, created_at=now, updated_at=now
                )
                session.add(row)
            row.name = location.name
            row.latitude = location.latitude
            row.longitude = location.longitude
            row.nx = location.nx
            row.ny = location.ny
            row.region_code = location.region_code
            row.enabled = location.enabled
            row.metadata_json = location.metadata
            row.updated_at = now
        return location

    def get_location(self, location_id: str) -> WeatherLocation | None:
        with self._session_factory() as session:
            row = session.get(WeatherLocationRow, location_id)
            return self._location_model(row) if row else None

    def list_locations(
        self,
        *,
        enabled_only: bool = False,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WeatherLocation]:
        with self._session_factory() as session:
            stmt = (
                select(WeatherLocationRow)
                .order_by(WeatherLocationRow.name)
                .limit(limit)
                .offset(offset)
            )
            if enabled_only:
                stmt = stmt.where(WeatherLocationRow.enabled.is_(True))
            if search:
                needle = f"%{search.strip()}%"
                stmt = stmt.where(
                    WeatherLocationRow.name.ilike(needle)
                    | WeatherLocationRow.location_id.ilike(needle)
                )
            return [self._location_model(row) for row in session.scalars(stmt).all()]

    def _record_source_session(self, session: Session, record: Mapping[str, Any]) -> None:
        source_record_key = str(record["source_record_key"])
        provider = str(record["provider"])
        dataset_key = str(record["dataset_key"])
        source_entity_type = str(record["source_entity_type"])
        source_entity_id = str(record["source_entity_id"])
        payload = dict(record["payload"])
        fetched = record.get("fetched_at") or kst_now()
        raw_hash = _payload_hash(payload)
        if self.engine.dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:source_key))"),
                {"source_key": source_record_key},
            )
        row = session.get(SourceRecordRow, source_record_key)
        if row is not None and (
            row.provider != provider
            or row.dataset_key != dataset_key
            or row.source_entity_type != source_entity_type
            or row.source_entity_id != source_entity_id
            or row.raw_payload_hash != raw_hash
        ):
            raise ValueError(f"immutable source record 충돌: {source_record_key}")
        if row is None:
            session.add(
                SourceRecordRow(
                    source_record_key=source_record_key,
                    provider=provider,
                    dataset_key=dataset_key,
                    source_entity_type=source_entity_type,
                    source_entity_id=source_entity_id,
                    raw_payload_hash=raw_hash,
                    payload=payload,
                    fetched_at=fetched,
                    imported_at=kst_now(),
                )
            )

    def _insert_value_session(self, session: Session, value: WeatherValue) -> bool:
        source_key = value.source_record_key or _metric_source_key(value)
        canonical_target = (
            value.target_at
            or value.valid_at
            or value.observed_at
            or value.issued_at
            or value.collected_at
        )
        value_id = value.identity_key(source_key, target_at=canonical_target)
        if self.engine.dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:source_key))"),
                {"source_key": source_key},
            )
        source = session.get(SourceRecordRow, source_key)
        if source is None:
            # Explicit response records should be inserted by Dagster first;
            # this fallback keeps hand-authored fixtures usable without claiming
            # their row payload is the full provider response.
            session.add(
                SourceRecordRow(
                    source_record_key=source_key,
                    provider=value.provider,
                    dataset_key=value.dataset_key,
                    source_entity_type="metric_row",
                    source_entity_id=value.location_id,
                    raw_payload_hash=_payload_hash(value.payload),
                    payload=value.payload,
                    fetched_at=value.known_at or value.collected_at,
                    imported_at=kst_now(),
                )
            )
        elif source.provider != value.provider or source.dataset_key != value.dataset_key:
            raise ValueError(f"immutable source record 충돌: {source_key}")
        row = session.get(WeatherValueRow, value_id)
        if row is not None:
            if (
                row.payload != value.payload
                or row.value_number != value.value_number
                or row.value_text != value.value_text
            ):
                raise ValueError(f"immutable weather fact 충돌: {value_id}")
            return False
        row = WeatherValueRow(value_id=value_id)
        session.add(row)
        row.location_id = value.location_id
        row.provider = value.provider
        row.dataset_key = value.dataset_key
        row.weather_domain = value.weather_domain
        row.forecast_style = value.forecast_style.value
        row.timeline_bucket = value.timeline_bucket.value if value.timeline_bucket else None
        row.metric_key = value.metric_key
        row.metric_name = value.metric_name
        row.source_metric_key = value.source_metric_key
        row.source_metric_name = value.source_metric_name
        row.value_number = value.value_number
        row.value_text = value.value_text
        row.unit = value.unit
        row.severity = value.severity
        row.issued_at = value.issued_at
        row.valid_at = value.valid_at
        row.valid_from = value.valid_from
        row.valid_until = value.valid_until
        row.observed_at = value.observed_at
        row.target_at = canonical_target
        row.known_at = value.known_at or value.collected_at
        row.normalization_version = value.normalization_version
        row.payload = value.payload
        row.collected_at = value.collected_at
        row.source_record_key = source_key
        return True

    def ingest_batch(
        self,
        *,
        source_records: list[Mapping[str, Any]] | None = None,
        values: list[WeatherValue] | None = None,
    ) -> int:
        """원천 record와 normalized facts를 한 transaction으로 publish한다."""
        records = source_records or []
        facts = values or []
        if not records and not facts:
            return 0
        with self._session_factory.begin() as session:
            for record in records:
                self._record_source_session(session, record)
            # pending source rows must be visible to FK checks/value lookup.
            session.flush()
            inserted = sum(self._insert_value_session(session, value) for value in facts)
        return inserted

    def upsert_values(self, values: list[WeatherValue]) -> int:
        # Historical method name is retained for callers; semantics are now
        # append-only insert/no-op replay, never an UPDATE.
        return self.ingest_batch(values=values)

    def record_source(
        self,
        *,
        source_record_key: str,
        provider: str,
        dataset_key: str,
        source_entity_type: str,
        source_entity_id: str,
        payload: dict[str, Any],
        fetched_at: datetime | None = None,
    ) -> None:
        """원천 응답을 보존해 weather fact와 raw lineage를 연결한다."""
        self.ingest_batch(
            source_records=[
                {
                    "source_record_key": source_record_key,
                    "provider": provider,
                    "dataset_key": dataset_key,
                    "source_entity_type": source_entity_type,
                    "source_entity_id": source_entity_id,
                    "payload": payload,
                    "fetched_at": fetched_at or kst_now(),
                }
            ]
        )

    @staticmethod
    def _ranked_current_ids(session: Session, stmt: Any) -> Any:
        """logical weather point별 최신 known/source revision id를 반환한다."""
        ranked = select(
            WeatherValueRow.value_id.label("value_id"),
            func.row_number()
            .over(
                partition_by=(
                    WeatherValueRow.location_id,
                    WeatherValueRow.provider,
                    WeatherValueRow.dataset_key,
                    WeatherValueRow.weather_domain,
                    WeatherValueRow.forecast_style,
                    WeatherValueRow.metric_key,
                    WeatherValueRow.target_at,
                ),
                order_by=(
                    nullslast(desc(WeatherValueRow.known_at)),
                    nullslast(desc(WeatherValueRow.source_record_key)),
                    desc(WeatherValueRow.value_id),
                ),
            )
            .label("revision_rank"),
        ).select_from(WeatherValueRow)
        # ``stmt`` is a select(WeatherValueRow) with all public filters applied.
        ranked = ranked.where(*stmt._where_criteria).subquery("current_weather_revision")
        return ranked

    def latest_values(self, location_id: str, *, limit: int = 100) -> list[WeatherValue]:
        with self._session_factory() as session:
            timestamp = func.coalesce(
                WeatherValueRow.target_at,
                WeatherValueRow.valid_at,
                WeatherValueRow.observed_at,
                WeatherValueRow.issued_at,
            )
            base = select(WeatherValueRow).where(WeatherValueRow.location_id == location_id)
            ranked = self._ranked_current_ids(session, base)
            stmt = (
                select(WeatherValueRow)
                .join(ranked, WeatherValueRow.value_id == ranked.c.value_id)
                .where(ranked.c.revision_rank == 1)
                .order_by(desc(timestamp), desc(WeatherValueRow.known_at))
                .limit(limit)
            )
            return [self._value_model(row) for row in session.scalars(stmt).all()]

    def timeline(
        self,
        location_id: str,
        *,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        dataset_key: str | None = None,
        metric_key: str | None = None,
        limit: int = 500,
        include_revisions: bool = False,
    ) -> list[WeatherValue]:
        with self._session_factory() as session:
            timestamp = func.coalesce(
                WeatherValueRow.target_at,
                WeatherValueRow.valid_at,
                WeatherValueRow.observed_at,
                WeatherValueRow.issued_at,
            )
            stmt = select(WeatherValueRow).where(WeatherValueRow.location_id == location_id)
            if from_at is not None:
                stmt = stmt.where(timestamp >= from_at)
            if to_at is not None:
                stmt = stmt.where(timestamp <= to_at)
            if dataset_key:
                stmt = stmt.where(WeatherValueRow.dataset_key == dataset_key)
            if metric_key:
                stmt = stmt.where(WeatherValueRow.metric_key == metric_key)
            if not include_revisions:
                ranked = self._ranked_current_ids(session, stmt)
                stmt = stmt.join(ranked, WeatherValueRow.value_id == ranked.c.value_id).where(
                    ranked.c.revision_rank == 1
                )
            stmt = stmt.order_by(timestamp, WeatherValueRow.metric_key).limit(limit)
            return [self._value_model(row) for row in session.scalars(stmt).all()]

    def nearest_locations(
        self, latitude: float, longitude: float, *, radius_km: float, limit: int = 20
    ) -> list[tuple[WeatherLocation, float]]:
        """활성 위치를 읽어 Haversine 거리로 정렬한다.

        운영 PostgreSQL에서는 latitude/longitude 인덱스 또는 PostGIS projection을
        추가할 수 있다. API 계약과 저장 포맷은 좌표계를 고정하므로 소비자 영향 없이
        query plan을 교체할 수 있다.
        """
        candidates = self.list_locations(enabled_only=True, limit=10000)
        earth_km = 6371.0088
        lat1 = math.radians(latitude)
        result: list[tuple[WeatherLocation, float]] = []
        for candidate in candidates:
            d_lat = math.radians(candidate.latitude - latitude)
            d_lon = math.radians(candidate.longitude - longitude)
            lat2 = math.radians(candidate.latitude)
            a = (
                math.sin(d_lat / 2) ** 2
                + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
            )
            distance = earth_km * 2 * math.asin(math.sqrt(a))
            if distance <= radius_km:
                result.append((candidate, distance))
        result.sort(key=lambda item: item[1])
        return result[:limit]

    def start_sync_run(
        self, *, provider: str, dataset_key: str, locations_total: int = 0
    ) -> SyncRun:
        run = SyncRun(
            run_id=f"run_{uuid.uuid4().hex}",
            provider=provider,
            dataset_key=dataset_key,
            status="running",
            started_at=kst_now(),
            locations_total=locations_total,
        )
        with self._session_factory.begin() as session:
            session.add(SyncRunRow(**run.model_dump()))
        return run

    def finish_sync_run(
        self,
        run_id: str,
        *,
        status: str,
        grids_fetched: int = 0,
        values_loaded: int = 0,
        error: str | None = None,
    ) -> SyncRun:
        with self._session_factory.begin() as session:
            row = session.get(SyncRunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            row.status = status
            row.finished_at = kst_now()
            row.grids_fetched = grids_fetched
            row.values_loaded = values_loaded
            row.error = error
            result = self._sync_model(row)
        return result

    def list_sync_runs(self, *, limit: int = 50) -> list[SyncRun]:
        with self._session_factory() as session:
            stmt = select(SyncRunRow).order_by(desc(SyncRunRow.started_at)).limit(limit)
            return [self._sync_model(row) for row in session.scalars(stmt).all()]


def repository_from_settings(settings: WeatherSettings | None = None) -> WeatherRepository:
    return WeatherRepository((settings or get_settings()).database_url)
