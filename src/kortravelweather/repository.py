"""SQLAlchemy 기반 weather repository.

원본의 ``infra/*_repo.py`` raw SQL 경계를 단순화해, 도메인 model과 저장소를
분리했다. 저장소는 PostgreSQL을 유일한 지원 데이터베이스로 사용한다.
weather fact는 ``value_id``(identity hash)를 primary key로 사용하므로
재수집은 멱등 append-only insert가 된다.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    case,
    column,
    create_engine,
    delete,
    desc,
    func,
    nullslast,
    or_,
    select,
    text,
    true,
    update,
    values,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from .metrics import observe_stale_recovered, observe_sync_finished, observe_sync_started
from .models import SyncRun, WeatherLocation, WeatherValue, kst_now
from .settings import WeatherSettings, get_settings


class Base(DeclarativeBase):
    pass


class AwareDateTime(TypeDecorator[datetime]):
    """PostgreSQL ``timestamptz``를 애플리케이션의 aware datetime으로 노출한다."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime은 timezone-aware여야 합니다.")
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class WeatherLocationRow(Base):
    __tablename__ = "weather_locations"
    __table_args__ = (
        Index("ix_weather_locations_enabled_region", "enabled", "region_code"),
        Index("ix_weather_locations_coordinates", "latitude", "longitude"),
        CheckConstraint("latitude >= 33 AND latitude <= 43", name="ck_weather_locations_latitude"),
        CheckConstraint(
            "longitude >= 124 AND longitude <= 132", name="ck_weather_locations_longitude"
        ),
        CheckConstraint("nx IS NULL OR (nx >= 1 AND nx <= 300)", name="ck_weather_locations_nx"),
        CheckConstraint("ny IS NULL OR (ny >= 1 AND ny <= 300)", name="ck_weather_locations_ny"),
    )

    location_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    latitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    nx: Mapped[int | None] = mapped_column(Integer)
    ny: Mapped[int | None] = mapped_column(Integer)
    region_code: Mapped[str | None] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict, server_default=text("'{}'")
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
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
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
        Index(
            "ix_weather_values_marker_lookup",
            "location_id",
            "metric_key",
            "known_at",
            "source_record_key",
            "value_id",
            postgresql_where=text(
                "metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY')"
            ),
        ),
        CheckConstraint(
            "value_number IS NOT NULL OR value_text IS NOT NULL",
            name="ck_weather_values_has_value",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from",
            name="ck_weather_values_valid_window",
        ),
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
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    collected_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    source_record_key: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("weather_source_records.source_record_key", ondelete="RESTRICT"),
        nullable=False,
    )


_MARKER_METRIC_KEYS = ("TEMP", "T1H", "TMP", "WEATHER_CODE", "SKY", "PTY")
_MARKER_CANDIDATE_LIMIT = 4


class SyncRunRow(Base):
    __tablename__ = "weather_sync_runs"
    __table_args__ = (
        Index("ix_weather_sync_runs_started", "started_at"),
        Index("ix_weather_sync_runs_heartbeat", "heartbeat_at"),
        # A provider/dataset may have at most one live run.  The application
        # checks first for a useful error message, while this partial unique
        # index closes the check-then-insert race between workers.
        Index(
            "uq_weather_sync_runs_active",
            "provider",
            "dataset_key",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    dataset_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(AwareDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime())
    locations_total: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    grids_fetched: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    mid_groups_fetched: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    requests_fetched: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    values_loaded: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error: Mapped[str | None] = mapped_column(Text)


class SyncRunSourceRow(Base):
    """한 실행이 관측한 immutable source response association."""

    __tablename__ = "weather_sync_run_sources"

    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("weather_sync_runs.run_id", ondelete="RESTRICT"), primary_key=True
    )
    source_record_key: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("weather_source_records.source_record_key", ondelete="RESTRICT"),
        primary_key=True,
    )
    recorded_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)


class ProviderCredentialRow(Base):
    """Encrypted provider credential override managed by the admin API.

    The table intentionally contains only ciphertext and non-sensitive
    lookup metadata.  Provider keys are never persisted in plaintext and the
    ORM row is never returned across an API boundary.
    """

    __tablename__ = "weather_provider_credentials"

    provider: Mapped[str] = mapped_column(String(120), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)


class AdminSessionRevocationRow(Base):
    """Durable logout markers for signed admin UI sessions.

    The browser session value itself is never stored.  A SHA-256 digest is
    sufficient for an exact lookup and keeps database dumps free of bearer
    tokens.  Expired markers are removed on lookup/write.
    """

    __tablename__ = "weather_admin_session_revocations"

    session_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)


def _credential_fernet(encryption_key: str | None) -> Fernet:
    """Build a Fernet instance without exposing key material in exceptions."""
    if not encryption_key:
        raise RuntimeError("provider credential encryption key가 설정되지 않았습니다.")
    try:
        return Fernet(encryption_key.encode("ascii"))
    except (UnicodeError, TypeError, ValueError) as exc:
        raise RuntimeError("provider credential encryption key가 올바르지 않습니다.") from exc


def normalize_provider_credential(api_key: str) -> str:
    """Normalize and bound an admin-supplied credential before encryption."""
    normalized = api_key.strip()
    if len(normalized) < 8:
        raise ValueError("provider api key는 8자 이상이어야 합니다.")
    if len(normalized) > 4096:
        raise ValueError("provider api key는 4096자를 초과할 수 없습니다.")
    return normalized


def provider_credential_fingerprint(api_key: str) -> str:
    """Return a non-reversible SHA-256 fingerprint for display/auditing."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def provider_credential_last4(api_key: str) -> str | None:
    """Return a safe suffix without exposing short credentials verbatim."""
    return api_key[-4:] if len(api_key) >= 8 else None


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _canonical_datetime(value: datetime | None) -> datetime | None:
    """Normalize equivalent aware instants before DB comparison/identity."""
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("datetime은 timezone-aware여야 합니다.")
    return value.astimezone(UTC)


def _canonical_row_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        return value
    return _canonical_datetime(value)


def _metric_source_key(value: WeatherValue) -> str:
    """Legacy/custom DTO도 안정적인 local lineage를 갖도록 한다.

    Dagster는 실제 KMA response hash를 명시적으로 전달한다. 이 fallback은
    수동 fixture나 API 테스트에서만 사용하며 전체 response인 것처럼 가장하지
    않도록 ``metric_row`` entity type으로 기록한다.
    """
    identity = list(value.identity())
    if identity[6] is not None:
        identity[6] = identity[6].astimezone(UTC).isoformat()
    canonical = json.dumps(
        [value.provider, value.dataset_key, value.location_id, identity, value.payload],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return "sr_local_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:48]


def install_immutability_triggers(engine: Engine) -> None:
    """Block direct UPDATE/DELETE of source and weather fact history."""
    if engine.dialect.name != "postgresql":
        raise RuntimeError("weather repository는 PostgreSQL만 지원합니다.")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION weather_immutable_row() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              RAISE EXCEPTION '%% is immutable', TG_TABLE_NAME;
            END; $$;
            """
        )
        for table in ("weather_source_records", "weather_values"):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
            connection.exec_driver_sql(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION weather_immutable_row()"
            )


class WeatherRepository:
    """동기 SQLAlchemy repository.

    API의 짧은 read/write와 Dagster의 batch upsert만 수행하므로 호출 경계는
    작고 명시적이다. 장시간 provider I/O는 repository 밖 Dagster가 소유한다.
    """

    def __init__(self, database_url: str) -> None:
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("WeatherRepository는 PostgreSQL DSN만 지원합니다.")
        normalized_url = database_url
        if normalized_url.startswith("postgresql://"):
            normalized_url = "postgresql+psycopg://" + normalized_url.removeprefix("postgresql://")
        self.engine: Engine = create_engine(normalized_url, future=True)

        self._session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        install_immutability_triggers(self.engine)

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
            heartbeat_at=row.heartbeat_at,
            finished_at=row.finished_at,
            locations_total=row.locations_total,
            grids_fetched=row.grids_fetched,
            mid_groups_fetched=row.mid_groups_fetched,
            requests_fetched=row.requests_fetched,
            values_loaded=row.values_loaded,
            error=row.error,
        )

    def _lock_location_session(self, session: Session, location_id: str) -> None:
        """Serialize anchor mutation and fact publication for one location."""
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:location_scope))"),
            {"location_scope": f"location:{location_id}"},
        )
        session.execute(
            select(WeatherLocationRow.location_id)
            .where(WeatherLocationRow.location_id == location_id)
            .with_for_update()
        ).scalar_one_or_none()

    def upsert_location(self, location: WeatherLocation) -> WeatherLocation:
        now = kst_now()
        with self._session_factory.begin() as session:
            self._lock_location_session(session, location.location_id)
            row = session.get(WeatherLocationRow, location.location_id)
            if row is None:
                row = WeatherLocationRow(
                    location_id=location.location_id, created_at=now, updated_at=now
                )
                session.add(row)
            else:
                coordinate_changed = any(
                    current != incoming
                    for current, incoming in (
                        (float(row.latitude), location.latitude),
                        (float(row.longitude), location.longitude),
                        (row.nx, location.nx),
                        (row.ny, location.ny),
                    )
                )
                if (
                    coordinate_changed
                    and session.scalar(
                        select(WeatherValueRow.value_id)
                        .where(WeatherValueRow.location_id == location.location_id)
                        .limit(1)
                    )
                    is not None
                ):
                    raise ValueError(
                        "fact가 있는 location의 좌표/grid는 변경할 수 없습니다. "
                        "새 location_id를 사용하세요."
                    )
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

    def create_location(self, location: WeatherLocation) -> WeatherLocation:
        """Insert a new catalog row without an update-on-conflict path."""
        now = kst_now()
        try:
            with self._session_factory.begin() as session:
                self._lock_location_session(session, location.location_id)
                if session.get(WeatherLocationRow, location.location_id) is not None:
                    raise ValueError(f"location_id가 이미 존재합니다: {location.location_id}")
                session.add(
                    WeatherLocationRow(
                        location_id=location.location_id,
                        name=location.name,
                        latitude=location.latitude,
                        longitude=location.longitude,
                        nx=location.nx,
                        ny=location.ny,
                        region_code=location.region_code,
                        enabled=location.enabled,
                        metadata_json=location.metadata,
                        created_at=now,
                        updated_at=now,
                    )
                )
        except IntegrityError as exc:
            raise ValueError(f"location_id가 이미 존재합니다: {location.location_id}") from exc
        return location

    def patch_location(self, location_id: str, changes: Mapping[str, Any]) -> WeatherLocation:
        """Apply an admin patch atomically without overwriting concurrent fields."""
        with self._session_factory.begin() as session:
            self._lock_location_session(session, location_id)
            row = session.get(WeatherLocationRow, location_id)
            if row is None:
                raise KeyError(location_id)
            current = self._location_model(row)
            patch = dict(changes)
            if patch.get("metadata") is None and "metadata" in patch:
                patch["metadata"] = {}
            updated = WeatherLocation.model_validate({**current.model_dump(), **patch})
            coordinate_changed = any(
                getattr(current, field) != getattr(updated, field)
                for field in ("latitude", "longitude", "nx", "ny")
            )
            if (
                coordinate_changed
                and session.scalar(
                    select(WeatherValueRow.value_id)
                    .where(WeatherValueRow.location_id == location_id)
                    .limit(1)
                )
                is not None
            ):
                raise ValueError(
                    "fact가 있는 location의 좌표/grid는 변경할 수 없습니다. "
                    "새 location_id를 사용하세요."
                )
            row.name = updated.name
            row.latitude = updated.latitude
            row.longitude = updated.longitude
            row.nx = updated.nx
            row.ny = updated.ny
            row.region_code = updated.region_code
            row.enabled = updated.enabled
            row.metadata_json = updated.metadata
            row.updated_at = kst_now()
            return updated

    def ensure_location_grid(
        self,
        location_id: str,
        *,
        nx: int,
        ny: int,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> WeatherLocation:
        """Persist a derived KMA grid without replaying a stale catalog row."""
        with self._session_factory.begin() as session:
            self._lock_location_session(session, location_id)
            row = session.get(WeatherLocationRow, location_id)
            if row is None:
                raise KeyError(location_id)
            if latitude is not None and float(row.latitude) != latitude:
                raise ValueError("location 좌표가 변경되어 grid 계산 결과를 적용할 수 없습니다.")
            if longitude is not None and float(row.longitude) != longitude:
                raise ValueError("location 좌표가 변경되어 grid 계산 결과를 적용할 수 없습니다.")
            if (row.nx, row.ny) == (nx, ny):
                return self._location_model(row)
            if row.nx is not None and row.nx != nx:
                raise ValueError("location의 기존 KMA grid는 변경할 수 없습니다.")
            if row.ny is not None and row.ny != ny:
                raise ValueError("location의 기존 KMA grid는 변경할 수 없습니다.")
            # Legacy/admin rows may have only one half of the optional pair.
            # Fill the missing coordinate while preserving the existing half.
            row.nx = nx if row.nx is None else row.nx
            row.ny = ny if row.ny is None else row.ny
            row.updated_at = kst_now()
            return self._location_model(row)

    def get_location(self, location_id: str) -> WeatherLocation | None:
        with self._session_factory() as session:
            row = session.get(WeatherLocationRow, location_id)
            return self._location_model(row) if row else None

    def has_values(self, location_id: str) -> bool:
        """위치 anchor를 변경해도 되는지 확인하는 최소 projection."""
        with self._session_factory() as session:
            return (
                session.scalar(
                    select(WeatherValueRow.value_id)
                    .where(WeatherValueRow.location_id == location_id)
                    .limit(1)
                )
                is not None
            )

    def list_locations(
        self,
        *,
        enabled_only: bool = False,
        search: str | None = None,
        limit: int | None = 100,
        offset: int = 0,
    ) -> list[WeatherLocation]:
        with self._session_factory() as session:
            # Stable tie-breaker keeps offset pagination deterministic when
            # many anchors share a display name.
            stmt = select(WeatherLocationRow).order_by(
                WeatherLocationRow.name, WeatherLocationRow.location_id
            )
            if enabled_only:
                stmt = stmt.where(WeatherLocationRow.enabled.is_(True))
            if search:
                needle = f"%{search.strip()}%"
                stmt = stmt.where(
                    WeatherLocationRow.name.ilike(needle)
                    | WeatherLocationRow.location_id.ilike(needle)
                )
            if limit is not None:
                stmt = stmt.limit(limit)
            if offset:
                stmt = stmt.offset(offset)
            return [self._location_model(row) for row in session.scalars(stmt).all()]

    def count_locations(
        self,
        *,
        enabled_only: bool = False,
        search: str | None = None,
    ) -> int:
        with self._session_factory() as session:
            stmt = select(func.count()).select_from(WeatherLocationRow)
            if enabled_only:
                stmt = stmt.where(WeatherLocationRow.enabled.is_(True))
            if search:
                needle = f"%{search.strip()}%"
                stmt = stmt.where(
                    WeatherLocationRow.name.ilike(needle)
                    | WeatherLocationRow.location_id.ilike(needle)
                )
            return int(session.scalar(stmt) or 0)

    def _record_source_session(self, session: Session, record: Mapping[str, Any]) -> None:
        source_record_key = str(record["source_record_key"])
        provider = str(record["provider"])
        dataset_key = str(record["dataset_key"])
        source_entity_type = str(record["source_entity_type"])
        source_entity_id = str(record["source_entity_id"])
        payload = dict(record["payload"])
        fetched = record.get("fetched_at") or kst_now()
        raw_hash = _payload_hash(payload)
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

    @staticmethod
    def _validate_source_lineage(
        session: Session, source: SourceRecordRow, value: WeatherValue
    ) -> None:
        """Ensure a known KMA response entity can serve this location.

        A grid response is intentionally fanned out to every catalog anchor on
        that grid.  Other response entities are location-scoped and must name
        the same anchor; unknown generic entity types remain extensible.
        """
        if source.source_entity_type not in {
            "weather_response",
            "kma_grid",
            "airkorea_station",
        }:
            return
        entity_id = source.source_entity_id
        if entity_id == value.location_id:
            return
        if entity_id.startswith("kma-alert:"):
            expected_station = entity_id.split(":", 1)[1]
            payload_station = value.payload.get("stn_id", value.payload.get("stnId"))
            if payload_station is not None and str(payload_station).strip() == expected_station:
                return
            raise ValueError(
                f"source record 특보 관측소가 fact와 일치하지 않습니다: "
                f"{entity_id} -> {payload_station!r}"
            )
        if entity_id.startswith(("mid-land:", "mid-temperature:")):
            expected_region = entity_id.split(":", 1)[1]
            payload_region = value.payload.get("reg_id", value.payload.get("regId"))
            if payload_region is not None and str(payload_region).strip() == expected_region:
                return
            raise ValueError(
                f"source record 중기 지역이 fact와 일치하지 않습니다: "
                f"{entity_id} -> {payload_region!r}"
            )
        if entity_id.startswith("grid:"):
            parts = entity_id.split(":")
            if len(parts) >= 3:
                try:
                    source_nx, source_ny = int(parts[1]), int(parts[2])
                except ValueError:
                    source_nx = source_ny = -1
                location = session.get(WeatherLocationRow, value.location_id)
                if location is not None and (location.nx, location.ny) == (source_nx, source_ny):
                    return
        raise ValueError(
            f"source record entity가 location과 일치하지 않습니다: "
            f"{source.source_entity_id} -> {value.location_id}"
        )

    def _insert_value_session(self, session: Session, value: WeatherValue) -> bool:
        self._lock_location_session(session, value.location_id)
        source_key = value.source_record_key or _metric_source_key(value)
        canonical_target = _canonical_datetime(
            value.target_at
            or value.valid_at
            or value.observed_at
            or value.issued_at
            or value.collected_at
        )
        value_id = value.identity_key(source_key, target_at=canonical_target)
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:source_key))"),
            {"source_key": source_key},
        )
        explicit_source = value.source_record_key is not None
        source = session.get(SourceRecordRow, source_key)
        if source is None:
            if explicit_source:
                raise ValueError(f"source record를 먼저 기록해야 합니다: {source_key}")
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
            canonical_known = _canonical_datetime(value.known_at or value.collected_at)
            canonical_collected = _canonical_datetime(value.collected_at)
        elif source.provider != value.provider or source.dataset_key != value.dataset_key:
            raise ValueError(f"immutable source record 충돌: {source_key}")
        else:
            self._validate_source_lineage(session, source, value)
            # A response key is stable across re-fetches. Preserve the first
            # source observation clock so a later transport timestamp cannot
            # turn an identical response into a false immutable conflict.
            canonical_known = _canonical_datetime(source.fetched_at)
            canonical_collected = _canonical_datetime(source.fetched_at)
        row = session.get(WeatherValueRow, value_id)
        if row is not None:
            expected = {
                "location_id": value.location_id,
                "provider": value.provider,
                "dataset_key": value.dataset_key,
                "weather_domain": value.weather_domain,
                "forecast_style": value.forecast_style.value,
                "timeline_bucket": value.timeline_bucket.value if value.timeline_bucket else None,
                "metric_key": value.metric_key,
                "metric_name": value.metric_name,
                "source_metric_key": value.source_metric_key,
                "source_metric_name": value.source_metric_name,
                "value_number": value.value_number,
                "value_text": value.value_text,
                "unit": value.unit,
                "severity": value.severity,
                "issued_at": _canonical_datetime(value.issued_at),
                "valid_at": _canonical_datetime(value.valid_at),
                "valid_from": _canonical_datetime(value.valid_from),
                "valid_until": _canonical_datetime(value.valid_until),
                "observed_at": _canonical_datetime(value.observed_at),
                "target_at": canonical_target,
                "known_at": canonical_known,
                "normalization_version": value.normalization_version,
                "payload": value.payload,
                "collected_at": canonical_collected,
                "source_record_key": source_key,
            }
            actual = {
                key: (
                    _canonical_row_datetime(getattr(row, key))
                    if key in {
                        "issued_at",
                        "valid_at",
                        "valid_from",
                        "valid_until",
                        "observed_at",
                        "target_at",
                        "known_at",
                        "collected_at",
                    }
                    else getattr(row, key if key != "payload" else "payload")
                )
                for key in expected
            }
            if actual != expected:
                changed = sorted(key for key in expected if actual[key] != expected[key])
                raise ValueError(f"immutable weather fact 충돌: {value_id} ({', '.join(changed)})")
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
        row.issued_at = _canonical_datetime(value.issued_at)
        row.valid_at = _canonical_datetime(value.valid_at)
        row.valid_from = _canonical_datetime(value.valid_from)
        row.valid_until = _canonical_datetime(value.valid_until)
        row.observed_at = _canonical_datetime(value.observed_at)
        row.target_at = canonical_target
        row.known_at = canonical_known
        row.normalization_version = value.normalization_version
        row.payload = value.payload
        row.collected_at = canonical_collected
        row.source_record_key = source_key
        return True

    def _ingest_batch_session(
        self,
        session: Session,
        records: list[Mapping[str, Any]],
        facts: list[WeatherValue],
    ) -> int:
        # Lock and validate every referenced run before source/fact work.
        # Reconciliation and finish use the same row-level lock/conditional
        # transition, so a terminal run cannot publish after ownership loss.
        run_rows: dict[str, SyncRunRow] = {}
        for record in records:
            run_id = record.get("run_id")
            if run_id is None:
                continue
            run_key = str(run_id)
            if run_key in run_rows:
                continue
            run = session.execute(
                select(SyncRunRow)
                .where(SyncRunRow.run_id == run_key)
                .with_for_update()
            ).scalar_one_or_none()
            if run is None:
                raise ValueError(f"sync run을 찾을 수 없습니다: {run_id}")
            if run.status != "running":
                raise ValueError(f"sync run이 이미 종료되었습니다: {run_id}")
            run_rows[run_key] = run
        for record in records:
            self._record_source_session(session, record)
        # pending source rows must be visible to FK checks/value lookup.
        session.flush()
        for record in records:
            run_id = record.get("run_id")
            if run_id is not None:
                source_key = str(record["source_record_key"])
                run = run_rows[str(run_id)]
                source_provider = str(record["provider"])
                source_dataset = str(record["dataset_key"])
                dataset_matches = run.dataset_key == source_dataset or (
                    run.dataset_key == "kma_weather_bundle" and source_dataset.startswith("kma_")
                )
                if run.provider != source_provider or not dataset_matches:
                    raise ValueError(
                        "sync run과 source record의 provider/dataset이 일치하지 않습니다."
                    )
                existing = session.get(
                    SyncRunSourceRow, {"run_id": str(run_id), "source_record_key": source_key}
                )
                if existing is None:
                    session.add(
                        SyncRunSourceRow(
                            run_id=str(run_id),
                            source_record_key=source_key,
                            recorded_at=kst_now(),
                        )
                    )
        # Acquire location locks in a stable order before inserting facts;
        # concurrent batches that touch several anchors cannot deadlock by
        # taking the same advisory/row locks in opposite orders.
        for location_id in sorted({value.location_id for value in facts}):
            self._lock_location_session(session, location_id)
        return sum(self._insert_value_session(session, value) for value in facts)

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
            return self._ingest_batch_session(session, records, facts)

    def publish_and_finish(
        self,
        *,
        run_id: str,
        source_records: list[Mapping[str, Any]],
        values: list[WeatherValue],
        grids_fetched: int,
        mid_groups_fetched: int = 0,
        requests_fetched: int = 0,
        status: str = "success",
        error: str | None = None,
    ) -> tuple[int, SyncRun]:
        """Publish facts and terminalize their run in one transaction.

        Keeping the run row lock until the conditional terminal transition
        prevents stale-run recovery from observing a half-published success.
        """
        finished: SyncRun
        loaded: int
        with self._session_factory.begin() as session:
            loaded = self._ingest_batch_session(session, source_records, values)
            result = session.execute(
                update(SyncRunRow)
                .where(SyncRunRow.run_id == run_id, SyncRunRow.status == "running")
                .values(
                    status=status,
                    finished_at=kst_now(),
                    grids_fetched=grids_fetched,
                    mid_groups_fetched=mid_groups_fetched,
                    requests_fetched=requests_fetched,
                    values_loaded=loaded,
                    error=error,
                )
            )
            if result.rowcount != 1:
                raise RuntimeError("sync run ownership was lost before publish completion")
            row = session.get(SyncRunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            finished = self._sync_model(row)
        observe_sync_finished(
            finished.provider,
            finished.dataset_key,
            status=finished.status,
            requests=requests_fetched,
            sources=len(source_records),
            values=loaded,
        )
        return loaded, finished

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
        return ranked.where(*stmt._where_criteria).subquery("current_weather_revision")

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
                .order_by(
                    case(
                        (
                            WeatherValueRow.forecast_style.in_(
                                ("observed", "nowcast")
                            ),
                            0,
                        ),
                        else_=1,
                    ),
                    desc(timestamp),
                    desc(WeatherValueRow.known_at),
                )
                .limit(limit)
            )
            return [self._value_model(row) for row in session.scalars(stmt).all()]

    def latest_values_many(
        self,
        location_ids: Sequence[str],
        *,
        limit_per_location: int = 100,
        weather_domain: str | None = None,
        metric_keys: Sequence[str] | None = None,
    ) -> dict[str, list[WeatherValue]]:
        """Fetch current projections for several locations in one query."""
        if not location_ids:
            return {}
        if limit_per_location <= 0:
            raise ValueError("limit_per_location은 양수여야 합니다.")
        with self._session_factory() as session:
            timestamp = func.coalesce(
                WeatherValueRow.target_at,
                WeatherValueRow.valid_at,
                WeatherValueRow.observed_at,
                WeatherValueRow.issued_at,
            )
            base = select(WeatherValueRow).where(WeatherValueRow.location_id.in_(location_ids))
            if weather_domain is not None:
                base = base.where(WeatherValueRow.weather_domain == weather_domain)
            if metric_keys is not None:
                normalized_metric_keys = tuple(dict.fromkeys(metric_keys))
                if not normalized_metric_keys:
                    return {location_id: [] for location_id in location_ids}
                base = base.where(WeatherValueRow.metric_key.in_(normalized_metric_keys))
            ranked = self._ranked_current_ids(session, base)
            limited_ids = (
                select(
                    WeatherValueRow.value_id.label("value_id"),
                    func.row_number()
                    .over(
                        partition_by=WeatherValueRow.location_id,
                        order_by=(
                            case(
                                (
                                    WeatherValueRow.forecast_style.in_(
                                        ("observed", "nowcast")
                                    ),
                                    0,
                                ),
                                else_=1,
                            ),
                            desc(timestamp),
                            desc(WeatherValueRow.known_at),
                            desc(WeatherValueRow.source_record_key),
                            desc(WeatherValueRow.value_id),
                        ),
                    )
                    .label("location_rank"),
                )
                .select_from(WeatherValueRow)
                .join(ranked, WeatherValueRow.value_id == ranked.c.value_id)
                .where(ranked.c.revision_rank == 1)
                .subquery("limited_current_values")
            )
            current = (
                select(WeatherValueRow)
                .join(limited_ids, WeatherValueRow.value_id == limited_ids.c.value_id)
                .where(limited_ids.c.location_rank <= limit_per_location)
                .order_by(
                    WeatherValueRow.location_id,
                    case(
                        (
                            WeatherValueRow.forecast_style.in_(
                                ("observed", "nowcast")
                            ),
                            0,
                        ),
                        else_=1,
                    ),
                    desc(timestamp),
                    desc(WeatherValueRow.known_at),
                )
            )
            result: dict[str, list[WeatherValue]] = {
                location_id: [] for location_id in location_ids
            }
            for row in session.scalars(current).all():
                values = result.setdefault(row.location_id, [])
                values.append(self._value_model(row))
            return result

    def marker_values_many(
        self,
        location_ids: Sequence[str],
        *,
        limit_per_location: int = 80,
    ) -> dict[str, list[WeatherValue]]:
        """Return the small current projection needed to render map markers.

        A marker only needs a weather condition/temperature and advisories;
        loading every air-quality and forecast metric makes a nationwide map
        refresh scan millions of append-only revisions.  Keep this allowlist
        in one repository boundary so the API cannot accidentally turn a
        marker request into a full fact-history query.
        """
        if not location_ids:
            return {}
        if limit_per_location <= 0:
            raise ValueError("limit_per_location은 양수여야 합니다.")
        # PostgreSQL's DISTINCT ON can use the marker lookup index to read
        # one recent row per location/metric without ranking all append-only
        # revisions.  Keep the generic window-query fallback for SQLite and
        # other test dialects.
        with self._session_factory() as session:
            if session.get_bind().dialect.name != "postgresql":
                return self.latest_values_many(
                    location_ids,
                    limit_per_location=limit_per_location,
                    metric_keys=(*_MARKER_METRIC_KEYS, "ALERT"),
                )

            # A forecast response can be ingested after the current response
            # and therefore have a larger ``known_at``.  Selecting the newest
            # row by that column alone would make a future forecast appear as
            # the marker's current condition.  Keep one value per provider /
            # metric (across current/forecast datasets), preferring
            # A forecast response can be ingested after the current response
            # and therefore have a larger ``known_at``.  Selecting the newest
            # row by that column alone would make a future forecast appear as
            # the marker's current condition.  The marker only needs one
            # representative row per location/metric; the full all-provider
            # bundle remains available from ``/resolve``.  Fetch a handful of
            # observed/nowcast candidates through a LATERAL index-only lookup,
            # then choose the newest candidate in Python.  The explicit metric
            # predicate is intentionally repeated inside the lateral query so
            # PostgreSQL can prove the partial marker index predicate even
            # though the metric value is supplied by a VALUES row.
            requested_locations = values(
                column("location_id", String), name="marker_locations"
            ).data([(location_id,) for location_id in location_ids]).alias(
                "marker_locations"
            )
            requested_metrics = values(
                column("metric_key", String), name="marker_metrics"
            ).data([(metric_key,) for metric_key in _MARKER_METRIC_KEYS]).alias(
                "marker_metrics"
            )

            def candidate_ids(*, observed_only: bool) -> Any:
                predicates = [
                    WeatherValueRow.location_id == requested_locations.c.location_id,
                    WeatherValueRow.metric_key == requested_metrics.c.metric_key,
                    WeatherValueRow.metric_key.in_(_MARKER_METRIC_KEYS),
                ]
                if observed_only:
                    predicates.append(
                        WeatherValueRow.forecast_style.in_(
                            ("observed", "nowcast")
                        )
                    )
                candidates = (
                    select(WeatherValueRow.value_id.label("value_id"))
                    .where(*predicates)
                    .order_by(
                        nullslast(desc(WeatherValueRow.known_at)),
                        desc(WeatherValueRow.source_record_key),
                        desc(WeatherValueRow.value_id),
                    )
                    .limit(_MARKER_CANDIDATE_LIMIT)
                    .lateral()
                    .alias("marker_candidates")
                )
                return select(candidates.c.value_id).select_from(
                    requested_locations.join(requested_metrics, true()).join(
                        candidates, true()
                    )
                )

            def fetch_candidates(candidate_query: Any) -> list[WeatherValueRow]:
                return list(
                    session.scalars(
                        select(WeatherValueRow).where(
                            WeatherValueRow.value_id.in_(candidate_query)
                        )
                    ).all()
                )

            observed_rows = fetch_candidates(candidate_ids(observed_only=True))
            selected: dict[tuple[str, str], WeatherValueRow] = {}
            for row in observed_rows:
                pair = (row.location_id, row.metric_key)
                previous = selected.get(pair)
                if previous is None or (
                    row.known_at or datetime.min.replace(tzinfo=UTC),
                    row.source_record_key,
                    row.value_id,
                ) > (
                    previous.known_at or datetime.min.replace(tzinfo=UTC),
                    previous.source_record_key,
                    previous.value_id,
                ):
                    selected[pair] = row

            # Forecast-only anchors are uncommon, but still need an icon.  Do
            # a bounded fallback only for missing location/metric pairs so the
            # normal observed path never scans their long forecast history.
            missing_pairs = [
                (location_id, metric_key)
                for location_id in location_ids
                for metric_key in _MARKER_METRIC_KEYS
                if (location_id, metric_key) not in selected
            ]
            if missing_pairs:
                missing_locations = values(
                    column("location_id", String),
                    column("metric_key", String),
                    name="marker_missing_pairs",
                ).data(missing_pairs).alias("marker_missing_pairs")
                fallback_candidates = (
                    select(WeatherValueRow.value_id.label("value_id"))
                    .where(
                        WeatherValueRow.location_id
                        == missing_locations.c.location_id,
                        WeatherValueRow.metric_key == missing_locations.c.metric_key,
                        WeatherValueRow.metric_key.in_(_MARKER_METRIC_KEYS),
                    )
                    .order_by(
                        nullslast(desc(WeatherValueRow.known_at)),
                        desc(WeatherValueRow.source_record_key),
                        desc(WeatherValueRow.value_id),
                    )
                    .limit(_MARKER_CANDIDATE_LIMIT)
                    .lateral()
                    .alias("marker_fallback_candidates")
                )
                fallback_query = select(
                    fallback_candidates.c.value_id
                ).select_from(
                    missing_locations.join(fallback_candidates, true())
                )
                for row in fetch_candidates(fallback_query):
                    pair = (row.location_id, row.metric_key)
                    previous = selected.get(pair)
                    if previous is None or (
                        row.known_at or datetime.min.replace(tzinfo=UTC),
                        row.source_record_key,
                        row.value_id,
                    ) > (
                        previous.known_at or datetime.min.replace(tzinfo=UTC),
                        previous.source_record_key,
                        previous.value_id,
                    ):
                        selected[pair] = row

            current_rows = list(selected.values())
            alert_station = func.coalesce(
                WeatherValueRow.payload["stn_id"].as_string(),
                WeatherValueRow.payload["stnId"].as_string(),
                WeatherValueRow.value_id,
            )
            alert_sequence = func.coalesce(
                WeatherValueRow.payload["seq"].as_string(),
                WeatherValueRow.payload["tmSeq"].as_string(),
                WeatherValueRow.value_id,
            )
            alert_ranked = (
                select(
                    WeatherValueRow.value_id.label("value_id"),
                    WeatherValueRow.location_id.label("location_id"),
                    WeatherValueRow.target_at.label("target_at"),
                    WeatherValueRow.known_at.label("known_at"),
                    func.row_number()
                    .over(
                        partition_by=(
                            WeatherValueRow.location_id,
                            WeatherValueRow.provider,
                            WeatherValueRow.dataset_key,
                            WeatherValueRow.weather_domain,
                            WeatherValueRow.metric_key,
                            WeatherValueRow.target_at,
                            alert_station,
                            alert_sequence,
                        ),
                        order_by=(
                            nullslast(desc(WeatherValueRow.known_at)),
                            desc(WeatherValueRow.target_at),
                            desc(WeatherValueRow.source_record_key),
                            desc(WeatherValueRow.value_id),
                        ),
                    )
                    .label("revision_rank"),
                )
                .where(
                    WeatherValueRow.location_id.in_(location_ids),
                    or_(
                        WeatherValueRow.weather_domain == "weather_alert",
                        WeatherValueRow.metric_key == "ALERT",
                    ),
                )
                .subquery("marker_alert_values")
            )
            alert_limited = (
                select(
                    alert_ranked.c.value_id,
                    func.row_number()
                    .over(
                        partition_by=alert_ranked.c.location_id,
                        order_by=(
                            desc(alert_ranked.c.target_at),
                            nullslast(desc(alert_ranked.c.known_at)),
                            desc(alert_ranked.c.value_id),
                        ),
                    )
                    .label("location_rank"),
                )
                .where(alert_ranked.c.revision_rank == 1)
                .subquery("limited_marker_alert_values")
            )
            alerts = (
                select(WeatherValueRow)
                .join(alert_limited, WeatherValueRow.value_id == alert_limited.c.value_id)
                .where(alert_limited.c.location_rank <= min(limit_per_location, 20))
                .order_by(WeatherValueRow.location_id, desc(WeatherValueRow.target_at))
            )
            result: dict[str, list[WeatherValue]] = {
                location_id: [] for location_id in location_ids
            }
            for row in current_rows:
                result.setdefault(row.location_id, []).append(self._value_model(row))
            for row in session.scalars(alerts).all():
                result.setdefault(row.location_id, []).append(self._value_model(row))
            return result

    def timeline_many(
        self, location_ids: Sequence[str], *, limit_per_location: int = 500
    ) -> dict[str, list[WeatherValue]]:
        """Return current projections for forecast/alert bundle queries in one SQL read."""
        if not location_ids:
            return {}
        if limit_per_location <= 0:
            raise ValueError("limit_per_location은 양수여야 합니다.")
        with self._session_factory() as session:
            timestamp = func.coalesce(
                WeatherValueRow.target_at,
                WeatherValueRow.valid_at,
                WeatherValueRow.observed_at,
                WeatherValueRow.issued_at,
            )
            base = select(WeatherValueRow).where(WeatherValueRow.location_id.in_(location_ids))
            ranked = self._ranked_current_ids(session, base)
            limited_ids = (
                select(
                    WeatherValueRow.value_id.label("value_id"),
                    func.row_number()
                    .over(
                        partition_by=WeatherValueRow.location_id,
                        order_by=(
                            desc(timestamp),
                            desc(WeatherValueRow.known_at),
                            desc(WeatherValueRow.source_record_key),
                            desc(WeatherValueRow.value_id),
                        ),
                    )
                    .label("location_rank"),
                )
                .select_from(WeatherValueRow)
                .join(ranked, WeatherValueRow.value_id == ranked.c.value_id)
                .where(ranked.c.revision_rank == 1)
                .subquery("limited_timeline_values")
            )
            current = (
                select(WeatherValueRow)
                .join(limited_ids, WeatherValueRow.value_id == limited_ids.c.value_id)
                .where(limited_ids.c.location_rank <= limit_per_location)
                .order_by(
                    WeatherValueRow.location_id,
                    desc(timestamp),
                    desc(WeatherValueRow.known_at),
                )
            )
            result: dict[str, list[WeatherValue]] = {
                location_id: [] for location_id in location_ids
            }
            for row in session.scalars(current).all():
                values = result.setdefault(row.location_id, [])
                values.append(self._value_model(row))
            return result

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
        candidates = self.list_locations(enabled_only=True, limit=None)
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
        started_at = kst_now()
        run = SyncRun(
            run_id=f"run_{uuid.uuid4().hex}",
            provider=provider,
            dataset_key=dataset_key,
            status="running",
            started_at=started_at,
            heartbeat_at=started_at,
            locations_total=locations_total,
        )
        try:
            with self._session_factory.begin() as session:
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:run_scope))"),
                    {"run_scope": f"{provider}:{dataset_key}"},
                )
                self._reconcile_stale_sync_runs_session(session)
                active = session.scalar(
                    select(SyncRunRow.run_id)
                    .where(
                        SyncRunRow.provider == provider,
                        SyncRunRow.dataset_key == dataset_key,
                        SyncRunRow.status == "running",
                    )
                    .limit(1)
                )
                if active is not None:
                    raise RuntimeError(f"동일 provider/dataset 실행이 이미 진행 중입니다: {active}")
                session.add(SyncRunRow(**run.model_dump()))
        except IntegrityError as exc:
            raise RuntimeError(
                "동일 provider/dataset 실행이 이미 진행 중입니다 (concurrent insert)."
            ) from exc
        observe_sync_started(provider, dataset_key)
        return run

    def reconcile_stale_sync_runs(self, *, max_age_minutes: int = 180) -> int:
        """프로세스 중단으로 남은 running row를 failed로 회수한다."""
        with self._session_factory.begin() as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('weather_sync_reconcile'))")
            )
            recovered = self._reconcile_stale_sync_runs_session(
                session, max_age_minutes=max_age_minutes
            )
        observe_stale_recovered(recovered)
        return recovered

    @staticmethod
    def _reconcile_stale_sync_runs_session(
        session: Session, *, max_age_minutes: int = 180
    ) -> int:
        cutoff = kst_now() - timedelta(minutes=max_age_minutes)
        stale_rows = session.scalars(
            select(SyncRunRow).where(
                SyncRunRow.status == "running",
                func.coalesce(SyncRunRow.heartbeat_at, SyncRunRow.started_at) < cutoff,
            )
        ).all()
        result = session.execute(
            update(SyncRunRow)
            .where(
                SyncRunRow.status == "running",
                func.coalesce(SyncRunRow.heartbeat_at, SyncRunRow.started_at) < cutoff,
            )
            .values(
                status="failed",
                finished_at=kst_now(),
                error="stale sync run recovered after worker interruption",
            )
        )
        recovered = int(result.rowcount or 0)
        for row in stale_rows[:recovered]:
            observe_sync_finished(row.provider, row.dataset_key, status="failed")
        return recovered

    def heartbeat_sync_run(self, run_id: str) -> bool:
        """Refresh a running sync lease using an atomic status check."""
        with self._session_factory.begin() as session:
            result = session.execute(
                update(SyncRunRow)
                .where(SyncRunRow.run_id == run_id, SyncRunRow.status == "running")
                .values(heartbeat_at=kst_now())
            )
            return result.rowcount == 1

    def finish_sync_run(
        self,
        run_id: str,
        *,
        status: str,
        grids_fetched: int = 0,
        mid_groups_fetched: int = 0,
        requests_fetched: int = 0,
        values_loaded: int = 0,
        error: str | None = None,
    ) -> SyncRun:
        transitioned = False
        with self._session_factory.begin() as session:
            result = session.execute(
                update(SyncRunRow)
                .where(SyncRunRow.run_id == run_id, SyncRunRow.status == "running")
                .values(
                    status=status,
                    finished_at=kst_now(),
                    grids_fetched=grids_fetched,
                    mid_groups_fetched=mid_groups_fetched,
                    requests_fetched=requests_fetched,
                    values_loaded=values_loaded,
                    error=error,
                )
            )
            transitioned = result.rowcount == 1
            row = session.get(SyncRunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            # Conditional UPDATE is the ownership/CAS boundary. If another
            # worker reconciled this run first, return its terminal row intact.
            finished = self._sync_model(row)
        if transitioned:
            observe_sync_finished(
                finished.provider,
                finished.dataset_key,
                status=finished.status,
                requests=requests_fetched,
                values=values_loaded,
            )
        return finished

    def list_sync_runs(self, *, limit: int = 50) -> list[SyncRun]:
        with self._session_factory() as session:
            stmt = select(SyncRunRow).order_by(desc(SyncRunRow.started_at)).limit(limit)
            return [self._sync_model(row) for row in session.scalars(stmt).all()]

    def get_sync_run(self, run_id: str) -> SyncRun | None:
        with self._session_factory() as session:
            row = session.get(SyncRunRow, run_id)
            return self._sync_model(row) if row else None

    def list_sync_run_sources(self, run_id: str) -> list[str]:
        with self._session_factory() as session:
            stmt = (
                select(SyncRunSourceRow.source_record_key)
                .where(SyncRunSourceRow.run_id == run_id)
                .order_by(SyncRunSourceRow.source_record_key)
            )
            return list(session.scalars(stmt).all())

    def get_source_record(self, source_record_key: str) -> dict[str, Any] | None:
        """관리자/재처리 경계에서 immutable 원천 응답을 읽는다."""
        with self._session_factory() as session:
            row = session.get(SourceRecordRow, source_record_key)
            if row is None:
                return None
            return {
                "source_record_key": row.source_record_key,
                "provider": row.provider,
                "dataset_key": row.dataset_key,
                "source_entity_type": row.source_entity_type,
                "source_entity_id": row.source_entity_id,
                "raw_payload_hash": row.raw_payload_hash,
                "payload": dict(row.payload or {}),
                "fetched_at": row.fetched_at,
                "imported_at": row.imported_at,
            }

    @staticmethod
    def _provider_credential_metadata(row: ProviderCredentialRow) -> dict[str, Any]:
        """Return only safe metadata; never include the encrypted value."""
        return {
            "provider": row.provider,
            "fingerprint": row.fingerprint,
            "last4": row.last4,
            "updated_at": row.updated_at,
        }

    def list_provider_credential_metadata(self) -> list[dict[str, Any]]:
        """List encrypted provider overrides without decrypting them."""
        with self._session_factory() as session:
            rows = session.scalars(
                select(ProviderCredentialRow).order_by(ProviderCredentialRow.provider)
            ).all()
            return [self._provider_credential_metadata(row) for row in rows]

    def get_provider_credential_metadata(self, provider: str) -> dict[str, Any] | None:
        """Read one provider override's safe metadata without decrypting it."""
        with self._session_factory() as session:
            row = session.get(ProviderCredentialRow, provider)
            return self._provider_credential_metadata(row) if row else None

    def set_provider_credential(
        self, provider: str, api_key: str, encryption_key: str | None
    ) -> dict[str, Any]:
        """Encrypt and atomically upsert one provider credential override."""
        normalized = normalize_provider_credential(api_key)
        fernet = _credential_fernet(encryption_key)
        ciphertext = fernet.encrypt(normalized.encode("utf-8")).decode("ascii")
        now = kst_now().astimezone(UTC)
        metadata = {
            "provider": provider,
            "fingerprint": provider_credential_fingerprint(normalized),
            "last4": provider_credential_last4(normalized),
            "updated_at": now,
        }
        with self._session_factory.begin() as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:credential_scope))"),
                {"credential_scope": f"provider-credential:{provider}"},
            )
            row = session.get(ProviderCredentialRow, provider)
            if row is None:
                session.add(
                    ProviderCredentialRow(
                        provider=provider,
                        ciphertext=ciphertext,
                        fingerprint=metadata["fingerprint"],
                        last4=metadata["last4"],
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                row.ciphertext = ciphertext
                row.fingerprint = metadata["fingerprint"]
                row.last4 = metadata["last4"]
                row.updated_at = now
        return metadata

    def delete_provider_credential(self, provider: str) -> bool:
        """Delete only a database override; environment settings are untouched."""
        with self._session_factory.begin() as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:credential_scope))"),
                {"credential_scope": f"provider-credential:{provider}"},
            )
            row = session.get(ProviderCredentialRow, provider)
            if row is None:
                return False
            session.delete(row)
            return True

    def get_provider_credential(
        self, provider: str, encryption_key: str | None
    ) -> str | None:
        """Decrypt one override for the provider-construction boundary only.

        Callers must use the returned value immediately to construct a client;
        this method is intentionally not used by API response code.
        """
        with self._session_factory() as session:
            row = session.get(ProviderCredentialRow, provider)
            if row is None:
                return None
            fernet = _credential_fernet(encryption_key)
            try:
                return fernet.decrypt(row.ciphertext.encode("ascii")).decode("utf-8")
            except (InvalidToken, UnicodeError, ValueError) as exc:
                raise RuntimeError(
                    "provider credential을 복호화할 수 없습니다. encryption key를 확인하세요."
                ) from exc

    @staticmethod
    def _session_digest(session_value: str) -> str:
        if not session_value or len(session_value) > 4096:
            raise ValueError("admin session 값이 올바르지 않습니다.")
        return hashlib.sha256(session_value.encode("utf-8")).hexdigest()

    def revoke_admin_session(self, session_value: str, *, ttl_seconds: int = 8 * 60 * 60) -> None:
        """Persist a logout marker without storing the signed bearer token."""
        digest = self._session_digest(session_value)
        now = kst_now().astimezone(UTC)
        expires_at = now + timedelta(seconds=max(1, min(ttl_seconds, 24 * 60 * 60)))
        with self._session_factory.begin() as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
                {"scope": f"admin-session-revocation:{digest}"},
            )
            session.execute(
                delete(AdminSessionRevocationRow).where(
                    AdminSessionRevocationRow.expires_at <= now
                )
            )
            row = session.get(AdminSessionRevocationRow, digest)
            if row is None:
                session.add(
                    AdminSessionRevocationRow(
                        session_digest=digest,
                        expires_at=expires_at,
                        created_at=now,
                    )
                )
            elif row.expires_at < expires_at:
                row.expires_at = expires_at

    def is_admin_session_revoked(self, session_value: str) -> bool:
        """Check and lazily remove one durable logout marker."""
        digest = self._session_digest(session_value)
        now = kst_now().astimezone(UTC)
        with self._session_factory.begin() as session:
            row = session.get(AdminSessionRevocationRow, digest)
            if row is None:
                return False
            if row.expires_at <= now:
                session.delete(row)
                return False
            return True


def repository_from_settings(settings: WeatherSettings | None = None) -> WeatherRepository:
    return WeatherRepository((settings or get_settings()).database_url)
