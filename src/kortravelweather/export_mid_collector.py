"""중기 지역 원천 하나의 사전 승인 계획. native 저장 의미는 바꾸지 않는다."""

import logging
from dataclasses import dataclass

from sqlalchemy import select

from .export_collector import KmaNowcastExportCollector, _PrepareRejected, failure_reason
from .export_mid import MidScopeDescriptor
from .export_receipts import ExportAttempt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedMidExport:
    identity: ExportAttempt
    descriptor: MidScopeDescriptor


class MidExportCollector(KmaNowcastExportCollector):
    def prepare(self, *, descriptor: MidScopeDescriptor):
        try:
            runs = self.heads.metadata.tables["weather_sync_runs"]
            with self.heads._transaction() as session:
                orchestrator = session.scalar(
                    select(runs.c.orchestrator_run_id).where(runs.c.run_id == self.run_id)
                )
            identity = self._begin_declared(
                descriptor, execution_mode="dagster" if orchestrator else "native"
            )
            return PreparedMidExport(identity, descriptor)
        except _PrepareRejected as rejected:
            self._reject(rejected.identity, "PREPARE_FAILED", rejected.__cause__)
            return None
        except Exception as exc:
            logger.warning(
                "mid_export_prepare_deferred run_id=%s reason=%s",
                self.run_id, failure_reason(exc),
            )
            return None

    def _bind_source(self, prepared, record):
        self.heads.bind_mid_response(prepared.identity, record)
