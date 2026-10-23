"""DTO가 일부 원문 행을 제외하거나 장소가 페이지 경계에 걸리는 경우를 검증한다."""

from types import SimpleNamespace

import pytest
from khoa import Observatory, Page, parse_beach_index_place

from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_sdk_pages import BeachPageAssembly, SdkPageReceipt


def _place(period):
    return parse_beach_index_place(Observatory(
        id="beach", name="대천", latitude=36.31, longitude=126.513,
        legal_dong_code="0012345678", road_address="원문 주소",
    ), [{"bbchNm": "대천", "lat": 36.31, "lot": 126.513,
         "predcYmd": "2026-10-24", "predcNoonSeCd": period, "avgArtmp": "17.2"}])


def test_khoa_one_place_split_across_pages_is_assembled_with_raw_and_address():
    assembly = BeachPageAssembly()
    assembly.add(Page(items=(_place("오전"),), total_count=2, page_no=1, num_of_rows=1))
    with pytest.raises(ExportArtifactError):
        assembly.finish()
    assembly.add(Page(items=(_place("오후"),), total_count=2, page_no=2, num_of_rows=1))
    places = assembly.finish()
    assert len(places) == 1 and len(places[0].forecasts) == 2
    assert places[0].raw == {"rows": _place("오전").raw["rows"] + _place("오후").raw["rows"]}
    assert places[0].road_address == "원문 주소" and places[0].legal_dong_code == "0012345678"


@pytest.mark.parametrize("change", [
    {"total_count": 3}, {"page_no": 3}, {"num_of_rows": 2}, {"raw_count": 0},
])
def test_total_page_size_and_raw_count_drift_are_unconfirmed(change):
    receipt = SdkPageReceipt()
    receipt.add(SimpleNamespace(total_count=2, page_no=1, num_of_rows=1), raw_count=1)
    fields = {"total_count": 2, "page_no": 2, "num_of_rows": 1, "raw_count": 1, **change}
    raw_count = fields.pop("raw_count")
    with pytest.raises(ExportArtifactError):
        receipt.add(SimpleNamespace(**fields), raw_count=raw_count)


@pytest.mark.parametrize("count", [None, 0, -1, 25001, True])
def test_unknown_empty_or_unbounded_envelope_is_not_complete(count):
    with pytest.raises(ExportArtifactError):
        SdkPageReceipt().add(
            SimpleNamespace(total_count=count, page_no=1, num_of_rows=100), raw_count=1,
        )


def test_krex_partial_parser_success_cannot_pass_as_complete_envelope():
    # 원문2행 중 정상 DTO1개만 SDK가 반환한 경우: 단일 DTO 재해석과 별개로 실패.
    with pytest.raises(ExportArtifactError):
        SdkPageReceipt().add(
            SimpleNamespace(total_count=2, page_no=1, num_of_rows=100), raw_count=1,
        )


def test_khoa_same_slot_replayed_on_two_pages_is_not_silently_overwritten():
    assembly = BeachPageAssembly()
    for number in (1, 2):
        assembly.add(Page(items=(_place("오전"),), total_count=2, page_no=number, num_of_rows=1))
    with pytest.raises(ExportArtifactError):
        assembly.finish()


def test_rejected_page_poisoned_receipt_cannot_later_claim_completeness():
    receipt = SdkPageReceipt()
    page = SimpleNamespace(total_count=1, page_no=1, num_of_rows=10)
    receipt.add(page, raw_count=1)
    assert receipt.complete
    with pytest.raises(ExportArtifactError):
        receipt.add(SimpleNamespace(total_count=1, page_no=2, num_of_rows=10), raw_count=0)
    assert not receipt.complete
