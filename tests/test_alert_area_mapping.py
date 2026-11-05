from datetime import datetime

from kortravelweather.alert_area_mapping import (
    area_index,
    area_mapping,
    candidate_areas,
    known_bjd,
    scope_at,
)
from kortravelweather.models import KST


def test_official_busans_whole_districts_do_not_cross_groups():
    areas = area_index()
    assert areas["L1082500"]["bjd_prefixes"] == ["26290", "26350", "26500", "26710"]
    assert "L1082500" in candidate_areas("2635010100")
    assert "L1082700" not in candidate_areas("2635010100")
    assert "L1082700" in candidate_areas("2611010100")
    assert "L1082500" not in candidate_areas("2611010100")


def test_official_hierarchy_separates_same_named_gwangju_and_goseong():
    areas = area_index()
    assert all(code.startswith("41") for code in areas["L1013400"]["bjd_prefixes"])
    assert areas["L1013400"]["bjd_prefixes"]
    assert areas["L1130100"]["bjd_prefixes"] == ["29"]
    assert all(code.startswith("51") for code in areas["L1022200"]["bjd_prefixes"])
    assert all(code.startswith("48") for code in areas["L1082300"]["bjd_prefixes"])
    assert areas["L1022200"]["parent_code"] != areas["L1082300"]["parent_code"]


def test_partial_or_ambiguous_provincial_coverage_stays_unknown():
    assert area_index()["L1110100"]["scope_status"] == "unknown"
    for area in area_index().values():
        if any(word in area["area_name"] for word in ["산지", "평지"]):
            assert area["scope_status"] == "unknown"
    assert len(area_mapping()["areas"]) == 413


def test_bjd_namespace_and_official_administration_are_required():
    assert known_bjd("26") and known_bjd("26350") and known_bjd("2635010100")
    assert not known_bjd("11B10101") and not known_bjd("L1082500")
    assert not known_bjd("99999") and not known_bjd("42190")
    assert candidate_areas("99999") == []
    assert candidate_areas("L1082500") == []


def test_mapping_is_only_valid_during_official_registry_interval():
    assert scope_at("L1082500", datetime(2026, 11, 6, tzinfo=KST))
    assert scope_at("L1082500", datetime(2024, 5, 1, tzinfo=KST)) is None
    assert scope_at("L1082500", None) is None
    assert scope_at("UNKNOWN", datetime(2026, 11, 6, tzinfo=KST)) is None


def test_source_evidence_is_official_and_byte_identified():
    sources = area_mapping()["sources"]
    assert sources["zones"]["sha256"] == (
        "9e79115c1a56759c4434c7c2a3e210c1ac099b3bec0c9b99e66a3ca48fa055b7"
    )
    assert sources["guide"]["sha256"] == (
        "81ca3c6ca75e8e7cc46c3510c18307e78eb9a3cc21b17805b8e14b42c109deb2"
    )
    assert sources["juso"]["sha256"] == (
        "901afdd83f1a2dfdbd85eca4223aae2cef8fceb7692277c067d8779613d23039"
    )
    assert all(
        area["bjd_prefixes"] for area in area_index().values() if area["scope_status"] == "verified"
    )


def test_unverified_dong_suffix_is_unknown_even_with_real_district_prefix():
    assert not known_bjd("2635099999")
    assert candidate_areas("2635099999") == []


def test_current_administrative_mapping_does_not_certify_2020():
    assert scope_at("L1020000", datetime(2020, 11, 5, tzinfo=KST)) is None


def test_canonical_aggregate_codes_are_separate_from_individual_bjd():
    assert known_bjd("2600000000") and known_bjd("2635000000")
    assert candidate_areas("2600000000") == candidate_areas("26")
    assert candidate_areas("2635000000") == candidate_areas("26350")
