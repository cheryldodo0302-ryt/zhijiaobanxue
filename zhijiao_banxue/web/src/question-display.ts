const QUESTION_TYPE_LABELS: Record<string, string> = {
  single_choice: "单选题",
  multiple_choice: "多选题",
  true_false: "判断题",
  short_answer: "简答题",
  fill_blank: "填空题",
  other: "其他",
};

function normalized(value: unknown) {
  return String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[ _/-]/g, "");
}

export function questionTypeLabel(value: unknown) {
  const raw = String(value ?? "").trim();
  const key = normalized(raw);
  if (QUESTION_TYPE_LABELS[key]) return QUESTION_TYPE_LABELS[key];
  if (key.includes("判断") || key.includes("truefalse") || key.includes("judgement"))
    return "判断题";
  if (key.includes("多选") || key.includes("multiplechoice")) return "多选题";
  if (key.includes("单选") || key.includes("singlechoice")) return "单选题";
  if (key.includes("简答") || key.includes("shortanswer")) return "简答题";
  if (key.includes("填空") || key.includes("fillblank")) return "填空题";
  return raw || "其他";
}

function judgmentAnswerLabel(value: unknown) {
  const key = normalized(value);
  if (["t", "true", "y", "yes", "1", "对", "正确", "是", "真", "√", "✓", "✔"].includes(key))
    return "对";
  if (["f", "false", "n", "no", "0", "错", "错误", "否", "假", "×", "✕", "✖", "✗"].includes(key))
    return "错";
  return String(value ?? "");
}

export function questionAnswerLabel(value: unknown, questionType?: unknown) {
  if (questionTypeLabel(questionType) === "判断题") {
    if (Array.isArray(value)) return value.map(judgmentAnswerLabel).join("、");
    return judgmentAnswerLabel(value);
  }
  if (Array.isArray(value)) return value.map((item) => String(item ?? "")).join("、");
  return String(value ?? "");
}

export function learningModeLabel(value: unknown) {
  return ({ cloze: "挖空练习", recitation: "背诵检测" } as Record<string, string>)[String(value ?? "")] || String(value ?? "");
}
