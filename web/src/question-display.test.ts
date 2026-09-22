import { describe, expect, it } from "vitest";
import { learningModeLabel, questionAnswerLabel, questionTypeLabel } from "./question-display";

describe("question display labels", () => {
  it("translates question types and judgment answers", () => {
    expect(questionTypeLabel("true_false")).toBe("判断题");
    expect(questionAnswerLabel("T", "true_false")).toBe("对");
    expect(questionAnswerLabel("F", "true_false")).toBe("错");
    expect(questionAnswerLabel(["T", "F"], "true_false")).toBe("对、错");
    expect(learningModeLabel("cloze")).toBe("挖空练习");
  });
});
