from __future__ import annotations

from datetime import date

import pytest

from airkorea import Pollutant, StatsDataGubun, StatsSearchCondition
from airkorea.client import SUPPORTED_ENDPOINTS, AirKoreaClient
from airkorea.exceptions import AirKoreaParseError
from tests.conftest import FakeResponse, FakeSession, payload


def test_supported_endpoints_covers_all_airkorea_openapi_groups() -> None:
    endpoints = {
        endpoint
        for service_endpoints in SUPPORTED_ENDPOINTS.values()
        for endpoint in service_endpoints
    }

    assert len(SUPPORTED_ENDPOINTS) == 11
    assert len(endpoints) == 19
    assert "getCtprvnMesureLIst" in endpoints
    assert "getMsrstnAcctoRMmrg" in endpoints
    assert "getYlwsndAdvsryOccrrncInfo" in endpoints
    assert "getUlfptcaAlarmInfo" in endpoints
    assert "getSvckeyDalyStats" in endpoints
    assert "getMinuDustFrcstDspth50Over" in endpoints


def test_statistics_methods_map_params_and_models() -> None:
    session = FakeSession(
        [
            FakeResponse(
                json_data=payload(
                    {
                        "dataTime": "2026-04-30 13:00",
                        "itemCode": "PM10",
                        "dataGubun": "HOUR",
                        "seoul": "35",
                        "busan": "-",
                    }
                )
            ),
            FakeResponse(
                json_data=payload(
                    {
                        "cityName": "Gangnam-gu",
                        "sidoName": "Seoul",
                        "pm10Value": "31",
                    }
                )
            ),
            FakeResponse(
                json_data=payload(
                    {
                        "msurDt": "2026-04-29",
                        "msrstnName": "Jongno-gu",
                        "pm25Value": "18",
                    }
                )
            ),
            FakeResponse(
                json_data=payload(
                    {
                        "msurDt": "2026-04-01",
                        "msrstnName": "Jongno-gu",
                        "pm25Value": "21",
                    }
                )
            ),
        ]
    )
    client = AirKoreaClient(service_key="KEY", session=session, retries=0)

    sido = client.sido_average_stats(
        item_code=Pollutant.PM25,
        data_gubun=StatsDataGubun.DAILY,
        search_condition=StatsSearchCondition.WEEK,
    )
    cities = client.city_average_stats("Seoul", item_code="PM10")
    daily = client.station_daily_stats(date(2026, 4, 1), "2026-04-30", station_name="Jongno-gu")
    monthly = client.station_monthly_stats("20260401", "20260430")

    assert sido[0].item_code == "PM10"
    assert sido[0].region_values["seoul"] == 35.0
    assert sido[0].region_values["busan"] is None
    assert cities[0].city_name == "Gangnam-gu"
    assert daily[0].station_name == "Jongno-gu"
    assert daily[0].pm25_value == 18.0
    assert monthly[0].pm25_value == 21.0
    assert sido[0].item_code_enum is Pollutant.PM10
    assert session.calls[0].url.endswith("/ArpltnStatsSvc/getCtprvnMesureLIst")
    assert session.calls[0].params["itemCode"] == "PM25"
    assert session.calls[0].params["dataGubun"] == "DAILY"
    assert session.calls[1].url.endswith("/ArpltnStatsSvc/getCtprvnMesureSidoLIst")
    assert session.calls[2].url.endswith("/ArpltnStatsSvc/getMsrstnAcctoRDyrg")
    assert session.calls[2].params["inqBginDt"] == "20260401"
    assert session.calls[2].params["inqEndDt"] == "20260430"
    assert session.calls[3].url.endswith("/ArpltnStatsSvc/getMsrstnAcctoRMmrg")


def test_remaining_airkorea_services_map_params_and_models() -> None:
    session = FakeSession(
        [
            FakeResponse(
                json_data=payload(
                    {
                        "sn": "1",
                        "dataDate": "2026-04-01",
                        "districtName": "Chungnam",
                        "moveName": "Dangjin",
                        "issueTime": "15:00",
                        "issueVal": "0.133",
                        "clearTime": "16:00",
                        "clearVal": "0.101",
                        "maxVal": "0.133",
                        "issueLvl": "1",
                    }
                )
            ),
            FakeResponse(json_data=payload({"sn": "2", "dataDate": "2026-04-02"})),
            FakeResponse(
                json_data=payload(
                    {
                        "sn": "3",
                        "dataDate": "2026-04-03",
                        "districtName": "Gyeonggi",
                        "moveName": "South",
                        "itemCode": "PM25",
                        "issueGbn": "advisory",
                        "issueDate": "2026-04-03",
                        "issueTime": "03:00",
                        "issueVal": "76",
                        "clearDate": "2026-04-03",
                        "clearTime": "13:00",
                        "clearVal": "28",
                    }
                )
            ),
            FakeResponse(
                json_data=payload(
                    {
                        "conectDe": "2026-04-30",
                        "conectOprtinNm": "station info",
                        "conectCo": "12",
                    }
                )
            ),
            FakeResponse(
                json_data=payload(
                    {
                        "dataTime": "2026-04-30 11:00",
                        "informData": "2026-04-30",
                        "informOverall": "PM2.5 over 50",
                        "informGrade": "Nationwide: high",
                    }
                )
            ),
            FakeResponse(
                json_data=payload(
                    {
                        "stationName": "Jongno-gu",
                        "stationCode": "111123",
                        "dataTime": "2026-04-30 13:00",
                        "mangName": "urban",
                        "khaiValue": "74",
                        "khaiGrade": "2",
                        "mainPollutant": "PM2.5",
                    }
                )
            ),
            FakeResponse(
                json_data=payload(
                    {
                        "msrmtYmd": "20220103",
                        "msrmtTm": "0100",
                        "msrstnNm": "s0002",
                        "pm10Value": "33",
                        "pm25Value": "12",
                    }
                )
            ),
            FakeResponse(
                json_data=payload(
                    {
                        "msrstnNm": "Jeungpyeong",
                        "msrstnEngNm": "Jeungpyeong",
                        "dataTime": "2026-04-30 13:00",
                        "khaiGrade": "2",
                        "pm10Value": "35",
                    }
                )
            ),
            FakeResponse(
                json_data=payload(
                    {
                        "msrstnNm": "Sangdae",
                        "msrstnEngNm": "Sangdae",
                        "roadNmAddr": "Korean road",
                        "roadNmAddrEng": "English road",
                        "dmX": "35.180",
                        "dmY": "128.107",
                    }
                )
            ),
        ]
    )
    client = AirKoreaClient(service_key="KEY", session=session, retries=0)

    ozone = client.ozone_advisories(year=2026)
    yellow_dust = client.yellow_dust_advisories(year="2026")
    alarms = client.dust_alarms(2026, item_code=Pollutant.PM25)
    traffic = client.traffic_stats(date(2026, 4, 30))
    high = client.high_pm25_forecasts("2026-04-30")
    cai = client.cai_measurements(station_name="Jongno-gu")
    background = client.background_concentrations(date(2022, 1, 3), "s0002")
    english_measurements = client.english_measurements(station_name="Jeungpyeong")
    english_stations = client.english_stations(station_name="Sangdae", road_address="Korean")

    assert ozone[0].kind == "ozone"
    assert ozone[0].issue_value == 0.133
    assert yellow_dust[0].kind == "yellow_dust"
    assert alarms[0].item_code == "PM25"
    assert alarms[0].item_code_enum is Pollutant.PM25
    assert alarms[0].clear_value == 28.0
    assert traffic[0].count == 12
    assert high[0].overall == "PM2.5 over 50"
    assert cai[0].station_code == "111123"
    assert cai[0].khai_grade == 2
    assert cai[0].khai_grade_enum is not None
    assert background[0].station_name == "s0002"
    assert background[0].pm25_value == 12.0
    assert english_measurements[0].station_name_english == "Jeungpyeong"
    assert english_stations[0].road_address_english == "English road"
    assert session.calls[0].url.endswith(
        "/OzYlwsndOccrrncInforInqireSvc/getOzAdvsryOccrrncInfo"
    )
    assert session.calls[1].url.endswith(
        "/OzYlwsndOccrrncInforInqireSvc/getYlwsndAdvsryOccrrncInfo"
    )
    assert session.calls[2].params["itemCode"] == "PM25"
    assert "ServiceKey" in session.calls[3].params
    assert "serviceKey" not in session.calls[3].params
    assert session.calls[6].params["msrmtYmd"] == "20220103"
    assert session.calls[7].params["msrstnNm"] == "Jeungpyeong"
    assert session.calls[8].url.endswith("/atmstMsrstnInfoEngNm/getList")


def test_malformed_new_api_item_raises_parse_error() -> None:
    session = FakeSession([FakeResponse(json_data=payload({"dataTime": "not-a-date"}))])
    client = AirKoreaClient(service_key="KEY", session=session, retries=0)

    with pytest.raises(AirKoreaParseError):
        client.sido_average_stats(item_code="PM10")
