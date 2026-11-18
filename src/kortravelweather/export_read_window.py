"""요청의 다음 실제 출력 구간만 읽는 bounded DB 창. 파일 backend는 기존 경로 유지."""

from .export_artifacts import ExportArtifactError
from .export_database_artifacts import MAX_ARTIFACT_READ_BATCH, MAX_ARTIFACT_READ_BYTES


class ArtifactReadWindow:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.bulk = getattr(artifacts, "read_many", None)
        self.points = {}
        self.receipts = {}

    def prepare(self, first, candidates, *, remaining_reads):
        if (not callable(self.bulk) or first["artifact_digest"] in self.points
                or remaining_reads <= 0):
            return
        refs, seen, size = [], set(), 0
        for ref in candidates():
            digest = ref["artifact_digest"]
            if digest in seen:
                continue
            if (len(refs) >= min(MAX_ARTIFACT_READ_BATCH, remaining_reads)
                    or size + ref["byte_count"] > MAX_ARTIFACT_READ_BYTES):
                break
            refs.append(ref)
            seen.add(digest)
            size += ref["byte_count"]
        self.points.clear()
        self.receipts.clear()
        if refs:
            self.points.update(self.bulk(refs))
            self.receipts.update({ref["artifact_digest"]: (ref["byte_count"], ref["point_count"])
                                  for ref in refs})

    def read(self, ref):
        digest = ref["artifact_digest"]
        if digest in self.points:
            if self.receipts[digest] != (ref["byte_count"], ref["point_count"]):
                raise ExportArtifactError("같은 artifact의 receipt가 서로 다릅니다.")
            return self.points[digest]
        return self.artifacts.read_shared(
            digest, byte_count=ref["byte_count"], point_count=ref["point_count"],
        )


def current_candidates(scopes, start, *, points, max_scopes, ref_start=0):
    observed = 0
    for offset, scope in enumerate(scopes[start:start + max_scopes]):
        for ref in scope["refs"][ref_start if offset == 0 else 0:]:
            yield ref
            observed += ref["point_count"]
        if observed >= points:
            return


def forecast_candidates(scopes, start, position, *, locations, max_scopes, since):
    observed = 0
    for offset, scope in enumerate(scopes[start:start + max_scopes]):
        if since is not None:
            from datetime import datetime
            if datetime.fromisoformat(scope["published_at"]) <= since:
                continue
        keys = scope["location_ids"][position if offset == 0 else 0:]
        for key in keys:
            refs = [ref for ref in scope["refs"] if key in ref["location_ids"]]
            if len(refs) != 1:
                return  # 실제 소비자가 같은 위치에 도달하면 기존 계약대로 실패한다.
            yield refs[0]
            observed += 1
            if observed >= locations:
                return
