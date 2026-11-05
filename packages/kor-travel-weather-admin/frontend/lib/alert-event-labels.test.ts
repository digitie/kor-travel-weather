import { describe, expect, it } from "vitest";
import { REGIONAL_ALERT_DATASET, syncRunErrorText } from "./alert-event-labels";

describe("지역별 특보 실행의 상세 원인", () => {
  it("페이지 변경과 인증 거절을 구분해 한국어 원인과 코드를 제공한다", () => {
    expect(syncRunErrorText(REGIONAL_ALERT_DATASET, "ALERT_PAGE_TOTAL_CHANGED"))
      .toContain("전체 행 수가 바뀌어");
    expect(syncRunErrorText(REGIONAL_ALERT_DATASET, "ALERT_PROVIDER_AUTH"))
      .toContain("인증 또는 접근 권한");
    expect(syncRunErrorText(REGIONAL_ALERT_DATASET, "ALERT_PROVIDER_AUTH"))
      .toContain("ALERT_PROVIDER_AUTH");
  });
  it("특보 작업의 알 수 없는 원문 URL이나 credential을 표시하지 않는다", () => {
    const result = syncRunErrorText(REGIONAL_ALERT_DATASET, "https://example.invalid/?serviceKey=fixture-only-key");
    expect(result).toContain("원인을 확인할 수 없습니다");
    expect(result).not.toContain("fixture-only-key");
    expect(result).not.toContain("https://");
    expect(syncRunErrorText(REGIONAL_ALERT_DATASET, "constructor")).toContain("원인을 확인할 수 없습니다");
  });
  it("성공과 다른 작업의 기존 오류 표현을 유지한다", () => {
    expect(syncRunErrorText(REGIONAL_ALERT_DATASET, null)).toBeNull();
    expect(syncRunErrorText("legacy_dataset", "기존 오류 안내")).toBe("기존 오류 안내");
  });
});
