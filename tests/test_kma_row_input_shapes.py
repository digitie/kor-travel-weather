"""KMA 행의 raw 우선순위·별칭·오류 계약을 확인한다."""

from collections import UserDict
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from kortravelweather.providers.kma import KmaForecastRow, KmaNowcastRow


@pytest.fixture(params=[KmaForecastRow, KmaNowcastRow])
def row_type(request):
    return request.param


def fields(row_type):
    result = dict(base_date="20261119", base_time="1100", nx="60", ny=127, category="TMP")
    if row_type is KmaForecastRow:
        result.update(fcst_date="20261119", fcst_time="1200", fcst_value=0)
    else:
        result["obsr_value"] = 0
    return result


def camel_fields(values):
    aliases = {
        "base_date": "baseDate", "base_time": "baseTime", "fcst_date": "fcstDate",
        "fcst_time": "fcstTime", "fcst_value": "fcstValue", "obsr_value": "obsrValue",
    }
    return {aliases.get(key, key): value for key, value in values.items()}


@pytest.mark.parametrize("shape", ["mapping", "userdict", "attributes", "raw"])
def test_all_input_shapes_preserve_aliases_numeric_coercion_and_zero(row_type, shape):
    values = fields(row_type)
    aliases = camel_fields(values)
    inputs = {
        "mapping": aliases,
        "userdict": UserDict(aliases),
        "attributes": SimpleNamespace(**aliases),
        "raw": SimpleNamespace(raw=UserDict(aliases), category="IGNORED"),
    }
    expected = {key: str(value) for key, value in values.items()}
    expected.update(nx=60, ny=127)
    assert asdict(row_type.from_raw(inputs[shape])) == expected


@pytest.mark.parametrize("shape", ["mapping", "attributes"])
def test_none_snake_alias_falls_back_to_camel_alias(row_type, shape):
    values = {**camel_fields(fields(row_type)), "base_date": None, "base_time": None}
    source = values if shape == "mapping" else SimpleNamespace(**values)
    assert row_type.from_raw(source).base_date == "20261119"
    assert row_type.from_raw(source).base_time == "1100"


def test_mapping_fields_do_not_fall_back_to_object_attributes(row_type):
    class MappingWithAttributes(UserDict):
        base_date = "20261119"

    values = camel_fields(fields(row_type))
    del values["baseDate"]
    with pytest.raises(ValueError, match="필수 필드 누락: base_date"):
        row_type.from_raw(MappingWithAttributes(values))


def test_nonmapping_raw_keeps_original_object_fields(row_type):
    source = SimpleNamespace(**fields(row_type), raw=["not", "a", "mapping"])
    assert row_type.from_raw(source).base_date == "20261119"


def test_snake_alias_precedes_camel_even_for_empty_string(row_type):
    values = {**camel_fields(fields(row_type)), "base_date": ""}
    assert row_type.from_raw(values).base_date == ""


def test_invalid_grid_keeps_numeric_conversion_error(row_type):
    values = {**fields(row_type), "nx": "not-an-integer"}
    with pytest.raises(ValueError, match="invalid literal"):
        row_type.from_raw(values)


def test_attribute_reader_propagates_provider_property_error(row_type):
    class BrokenProvider:
        @property
        def base_date(self):
            raise RuntimeError("provider property error")

    with pytest.raises(RuntimeError, match="provider property error"):
        row_type.from_raw(BrokenProvider())
