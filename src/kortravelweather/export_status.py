"""수집 terminal/다음 예정 시각을 작은 상태표로 투영한다.

원천 파싱·발표 시각 선택·cron 계산은 하지 않는다. 소유자가 검증한 scope와 시각을
전달하며, 수집 terminal의 CAS가 성공한 transaction/savepoint 안에서 호출한다.
API는 이 표만 읽고 fact 이력이나 Dagster workspace를 다시 조회하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import (
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    MetaData,
    String,
    Table,
    case,
    or_,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import insert

STYLES = frozenset({"observed", "nowcast", "ultra_short", "short", "mid", "external"})
TERMINALS = frozenset({"success", "partial", "failed", "abandoned", "cancelled"})
MAX_STATUS_SCOPES = 128
Terminal = Literal["success", "partial", "failed", "abandoned", "cancelled"]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("상태 시각에는 timezone이 필요합니다.")
    return value.astimezone(UTC)


@dataclass(frozen=True, order=True, slots=True)
class SourceKey:
    provider: str
    dataset_key: str
    forecast_style: str

    def __post_init__(self) -> None:
        if (
            not 1 <= len(self.provider) <= 120
            or not 1 <= len(self.dataset_key) <= 160
            or self.forecast_style not in STYLES
            or any(ord(c) < 32 for c in self.provider + self.dataset_key)
        ):
            raise ValueError("서비스 상태 scope가 올바르지 않습니다.")


@dataclass(frozen=True, slots=True)
class SourceState:
    key: SourceKey
    last_status: str | None = None
    last_run_id: str | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_success_at: datetime | None = None
    source_collected_at: datetime | None = None
    last_failed_at: datetime | None = None
    next_scheduled_at: datetime | None = None

    @property
    def last_attempt_failed(self) -> bool:
        return self.last_status is not None and self.last_status != "success"


def export_status_table(metadata: MetaData) -> Table:
    if "weather_export_source_status" in metadata.tables:
        return metadata.tables["weather_export_source_status"]
    return Table(
        "weather_export_source_status",
        metadata,
        Column("provider", String(120), primary_key=True),
        Column("dataset_key", String(160), primary_key=True),
        Column("forecast_style", String(40), primary_key=True),
        Column("last_run_id", String(64)),
        Column("last_started_at", DateTime(timezone=True)),
        Column("last_finished_at", DateTime(timezone=True)),
        Column("last_status", String(32)),
        Column("last_success_at", DateTime(timezone=True)),
        Column("source_collected_at", DateTime(timezone=True)),
        Column("last_failed_at", DateTime(timezone=True)),
        Column("schedule_evaluated_at", DateTime(timezone=True)),
        Column("next_scheduled_at", DateTime(timezone=True)),
        CheckConstraint(
            "forecast_style IN ('observed','nowcast','ultra_short','short','mid','external')",
            name="ck_weather_export_source_status_style",
        ),
        CheckConstraint(
            "last_status IS NULL OR last_status IN "
            "('success','partial','failed','abandoned','cancelled')",
            name="ck_weather_export_source_status_terminal",
        ),
    )


class ExportSourceStatuses:
    """접속/transaction의 수명은 호출자가 소유한다. 내부 commit은 없다."""

    def __init__(self, metadata: MetaData):
        self.table = export_status_table(metadata)

    @staticmethod
    def _identity(key: SourceKey) -> dict[str, str]:
        return {
            "provider": key.provider,
            "dataset_key": key.dataset_key,
            "forecast_style": key.forecast_style,
        }

    def record_terminal(
        self,
        connection: Connection,
        key: SourceKey,
        *,
        run_id: str,
        started_at: datetime,
        finished_at: datetime,
        status: Terminal,
        source_collected_at: datetime | None = None,
    ) -> bool:
        started, finished = _utc(started_at), _utc(finished_at)
        collected = _utc(source_collected_at) if source_collected_at is not None else None
        if (
            not 1 <= len(run_id) <= 64
            or status not in TERMINALS
            or finished < started
            or (status == "success" and collected is None)
            or (collected is not None and collected > finished)
        ):
            raise ValueError("수집 terminal 상태/시각이 올바르지 않습니다.")
        values = {
            **self._identity(key),
            "last_run_id": run_id,
            "last_started_at": started,
            "last_finished_at": finished,
            "last_status": status,
            "last_success_at": finished if status == "success" else None,
            "source_collected_at": collected if status == "success" else None,
            "last_failed_at": finished if status != "success" else None,
        }
        statement = insert(self.table).values(values)
        old, new = self.table.c, statement.excluded
        newer = or_(
            old.last_started_at.is_(None),
            tuple_(new.last_started_at, new.last_run_id)
            > tuple_(old.last_started_at, old.last_run_id),
        )
        result = connection.execute(statement.on_conflict_do_update(
            index_elements=[old.provider, old.dataset_key, old.forecast_style],
            set_={
                "last_run_id": new.last_run_id,
                "last_started_at": new.last_started_at,
                "last_finished_at": new.last_finished_at,
                "last_status": new.last_status,
                "last_success_at": case(
                    (new.last_status == "success", new.last_success_at),
                    else_=old.last_success_at,
                ),
                "source_collected_at": case(
                    (new.last_status == "success", new.source_collected_at),
                    else_=old.source_collected_at,
                ),
                "last_failed_at": case(
                    (new.last_status != "success", new.last_failed_at),
                    else_=old.last_failed_at,
                ),
            },
            where=newer,
        ).returning(self.table.c.provider))
        return result.scalar_one_or_none() is not None

    def record_schedule(
        self,
        connection: Connection,
        key: SourceKey,
        *,
        evaluated_at: datetime,
        next_scheduled_at: datetime | None,
    ) -> bool:
        evaluated = _utc(evaluated_at)
        upcoming = _utc(next_scheduled_at) if next_scheduled_at is not None else None
        if upcoming is not None and upcoming <= evaluated:
            raise ValueError("다음 예정 시각은 평가 시각보다 뒤여야 합니다.")
        statement = insert(self.table).values({
            **self._identity(key),
            "schedule_evaluated_at": evaluated,
            "next_scheduled_at": upcoming,
        })
        old, new = self.table.c, statement.excluded
        result = connection.execute(statement.on_conflict_do_update(
            index_elements=[old.provider, old.dataset_key, old.forecast_style],
            set_={
                "schedule_evaluated_at": new.schedule_evaluated_at,
                "next_scheduled_at": new.next_scheduled_at,
            },
            where=or_(
                old.schedule_evaluated_at.is_(None),
                new.schedule_evaluated_at > old.schedule_evaluated_at,
            ),
        ).returning(self.table.c.provider))
        return result.scalar_one_or_none() is not None

    def read(
        self,
        connection: Connection,
        keys: tuple[SourceKey, ...],
        *,
        as_of: datetime,
    ) -> tuple[SourceState, ...]:
        now = _utc(as_of)
        if not 1 <= len(keys) <= MAX_STATUS_SCOPES or len(set(keys)) != len(keys):
            raise ValueError("상태 scope는 중복 없이 1~128개여야 합니다.")
        identity = tuple_(
            self.table.c.provider, self.table.c.dataset_key, self.table.c.forecast_style
        )
        rows = {
            SourceKey(row.provider, row.dataset_key, row.forecast_style): row
            for row in connection.execute(select(self.table).where(
                identity.in_([(k.provider, k.dataset_key, k.forecast_style) for k in keys])
            )).mappings()
        }

        def optional_time(value):
            return _utc(value) if value is not None else None

        result = []
        for key in sorted(keys):
            row = rows.get(key)
            if row is None:
                result.append(SourceState(key))
                continue
            upcoming = optional_time(row.next_scheduled_at)
            result.append(SourceState(
                key=key,
                last_status=row.last_status,
                last_run_id=row.last_run_id,
                last_started_at=optional_time(row.last_started_at),
                last_finished_at=optional_time(row.last_finished_at),
                last_success_at=optional_time(row.last_success_at),
                source_collected_at=optional_time(row.source_collected_at),
                last_failed_at=optional_time(row.last_failed_at),
                next_scheduled_at=upcoming if upcoming is not None and upcoming > now else None,
            ))
        return tuple(result)
