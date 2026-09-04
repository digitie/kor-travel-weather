import type { Feature, FeatureCollection, Point } from "geojson";

export type WeatherCondition = "sunny" | "cloudy" | "rainy" | "snowy" | "storm";

export type WeatherClusterMarker = {
  id: string;
  lngLat: [number, number];
  temperature: number | null;
  condition: WeatherCondition;
  alertCount?: number;
  selected?: boolean;
  ariaLabel: string;
  title?: string;
  onClick?: () => void;
};

export type WeatherClusterProperties = {
  marker_id: string;
  condition: WeatherCondition;
  temperature: number | null;
  alert_count: number;
  selected: boolean;
  label: string;
};

export type WeatherClusterFeature = Feature<Point, WeatherClusterProperties>;
export type WeatherClusterData = FeatureCollection<Point, WeatherClusterProperties>;

/**
 * Build the point source consumed by MapLibre's native `cluster:true` source.
 * Invalid coordinates are ignored at the map boundary so one malformed API row
 * cannot prevent the remaining weather markers from rendering.
 */
export function buildWeatherClusterData(
  markers: ReadonlyArray<WeatherClusterMarker>,
): WeatherClusterData {
  const features: WeatherClusterFeature[] = [];
  for (const marker of markers) {
    const [longitude, latitude] = marker.lngLat;
    if (
      !Number.isFinite(longitude) ||
      !Number.isFinite(latitude) ||
      longitude < -180 ||
      longitude > 180 ||
      latitude < -90 ||
      latitude > 90
    ) {
      continue;
    }
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [longitude, latitude] },
      properties: {
        marker_id: marker.id,
        condition: marker.condition,
        temperature: marker.temperature,
        alert_count: Math.max(0, marker.alertCount ?? 0),
        selected: marker.selected === true,
        label: marker.title ?? marker.ariaLabel,
      },
    });
  }
  return { type: "FeatureCollection", features };
}
