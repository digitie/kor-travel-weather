import { describe, expect, it } from "vitest";

import {
  DagsterRun,
  STALLED_RUN_THRESHOLD_SECONDS,
  formatElapsed,
  isStalledRun,
  jobLabel,
  runElapsedSeconds,
} from "./dagster";

function run(overrides: Partial<DagsterRun>): DagsterRun {
  return {
    runId: "11111111-1111-1111-1111-111111111111",
    status: "STARTED",
    jobName: "open_meteo_weather_job",
    startTime: 0,
    endTime: null,
    errorMessage: null,
    ...overrides,
  };
}

describe("jobLabel", () => {
  it("translates a known job name into its Korean description", () => {
    expect(jobLabel("kma_weather_job")).toBe("기상청 단기예보 수집");
  });

  it("names the provider for each external collection job", () => {
    // One job per external provider: the label has to say which source is
    // late, which the single "external weather" job it replaced could not.
    expect(jobLabel("open_meteo_weather_job")).toBe("Open-Meteo 날씨 수집");
    expect(jobLabel("accuweather_weather_sync")).toBe("AccuWeather 날씨 수집");
  });

  it("translates a known step name the same way as a job name", () => {
    expect(jobLabel("krforest_dust_sync")).toBe("청정넷 미세먼지 수집");
  });

  it("falls back to the raw name for anything this project does not define", () => {
    expect(jobLabel("some_future_job_nobody_labeled_yet")).toBe(
      "some_future_job_nobody_labeled_yet",
    );
  });
});

describe("runElapsedSeconds", () => {
  it("is null for a run that already finished", () => {
    expect(runElapsedSeconds(run({ status: "SUCCESS", startTime: 0 }), 100)).toBeNull();
  });

  it("is null for a run with no recorded start time", () => {
    expect(runElapsedSeconds(run({ status: "STARTED", startTime: null }), 100)).toBeNull();
  });

  it("is the gap between start and now for a run still in progress", () => {
    expect(runElapsedSeconds(run({ status: "STARTED", startTime: 40 }), 100)).toBe(60);
  });
});

describe("isStalledRun", () => {
  it("is false just under the threshold", () => {
    const startTime = 1000;
    const now = startTime + STALLED_RUN_THRESHOLD_SECONDS - 1;
    expect(isStalledRun(run({ status: "STARTED", startTime }), now)).toBe(false);
  });

  it("is true at and past the threshold", () => {
    const startTime = 1000;
    const now = startTime + STALLED_RUN_THRESHOLD_SECONDS;
    expect(isStalledRun(run({ status: "STARTED", startTime }), now)).toBe(true);
  });

  it("is false for a run that is not STARTED, no matter how old", () => {
    const startTime = 0;
    const now = STALLED_RUN_THRESHOLD_SECONDS * 100;
    expect(isStalledRun(run({ status: "SUCCESS", startTime }), now)).toBe(false);
  });
});

describe("formatElapsed", () => {
  it("reads in whole minutes under an hour", () => {
    expect(formatElapsed(42 * 60)).toBe("42분");
  });

  it("reads in hours and minutes at an hour or more", () => {
    expect(formatElapsed(72 * 60)).toBe("1시간 12분");
  });

  it("calls out anything under a minute rather than reading as 0분", () => {
    expect(formatElapsed(30)).toBe("1분 미만");
  });
});
