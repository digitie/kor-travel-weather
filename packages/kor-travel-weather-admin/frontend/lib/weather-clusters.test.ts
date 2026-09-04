import { describe, expect, it } from "vitest";

import { buildWeatherClusterData, type WeatherClusterMarker } from "./weather-clusters";

describe("buildWeatherClusterData", () => {
  it("keeps valid weather points and their marker state", () => {
    const markers: WeatherClusterMarker[] = [
      {
        id: "seoul",
        lngLat: [126.978, 37.5665],
        temperature: 24.5,
        condition: "sunny",
        alertCount: 2,
        selected: true,
        ariaLabel: "서울: 맑음 날씨 보기",
        title: "서울",
      },
    ];

    expect(buildWeatherClusterData(markers)).toEqual({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: { type: "Point", coordinates: [126.978, 37.5665] },
          properties: {
            marker_id: "seoul",
            condition: "sunny",
            temperature: 24.5,
            alert_count: 2,
            selected: true,
            label: "서울",
          },
        },
      ],
    });
  });

  it("ignores malformed coordinates without dropping other stations", () => {
    const markers: WeatherClusterMarker[] = [
      {
        id: "bad",
        lngLat: [Number.NaN, 37],
        temperature: null,
        condition: "cloudy",
        ariaLabel: "bad",
      },
      {
        id: "busan",
        lngLat: [129.0756, 35.1796],
        temperature: 21,
        condition: "rainy",
        ariaLabel: "부산: 비 날씨 보기",
      },
    ];

    const data = buildWeatherClusterData(markers);
    expect(data.features).toHaveLength(1);
    expect(data.features[0].properties.marker_id).toBe("busan");
  });
});
