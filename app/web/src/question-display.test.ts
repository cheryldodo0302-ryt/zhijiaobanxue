import { describe, expect, it } from "vitest";
import { learningModeLabel, questionAnswerLabel, questionOptions, questionTypeLabel } from "./question-display";

describe("question display labels", () => {
  it("translates question types and judgment answers", () => {
    expect(questionTypeLabel("true_false")).toBe("判断题");
    expect(questionAnswerLabel("T", "true_false")).toBe("对");
    expect(questionAnswerLabel("F", "true_false")).toBe("错");
    expect(questionAnswerLabel(["T", "F"], "true_false")).toBe("对、错");
    expect(learningModeLabel("cloze")).toBe("挖空练习");
  });
  it("provides answer choices for judgment questions with empty options", () => {
    expect(questionOptions({ type: "true_false", options: [] })).toEqual([
      { key: "T", text: "正确" }, { key: "F", text: "错误" },
    ]);
    expect(questionOptions({ question_type: "true_false", options: {} })).toEqual([
      { key: "T", text: "正确" }, { key: "F", text: "错误" },
    ]);
    expect(questionOptions({ type: "single_choice", options: [] })).toEqual([]);
    expect(questionOptions({ type: "single_choice", options: ["选项一", "选项二"] }))
      .toEqual([{ key: "选项一", text: "选项一" }, { key: "选项二", text: "选项二" }]);
    expect(questionOptions({ type: "true_false", options: [{ key: "Y", text: "对" }, { key: "N", text: "错" }] }))
      .toEqual([{ key: "Y", text: "对" }, { key: "N", text: "错" }]);
  });
});
