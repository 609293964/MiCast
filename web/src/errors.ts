const TECHNICAL_ERROR = /(?:traceback|\bfile\s+["']|line\s+\d+|\b(?:type|reference|syntax|runtime|value|key|attribute)error\b|\bat\s+\S+\s*\(|\{\s*"(?:detail|error)"|http\s+\d{3}|internal server error|\.py\b|node_modules|\/var\/|\\appdata\\)/i;

const GENERIC_ERROR = "操作未完成，请稍后重试；如持续发生，请在诊断页下载报告";

/** Keep UI copy useful while never exposing stack traces or implementation details. */
export function safeUserMessage(value: unknown): string {
  const raw = value instanceof Error ? value.message : typeof value === "string" ? value : "";
  const message = raw.trim();
  if (!message || TECHNICAL_ERROR.test(message) || message.length > 240) return GENERIC_ERROR;
  return message;
}
