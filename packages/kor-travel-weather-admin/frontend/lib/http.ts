/**
 * Turning a failed response into a sentence an operator can act on.
 *
 * Not every failure carries a JSON body. The auth middleware answers a
 * cross-origin POST with a plain-text 403, an unhandled backend exception
 * yields Starlette's text/plain "Internal Server Error", and a gateway timeout
 * returns HTML. Calling `response.json()` before checking the status turns all
 * of those into a SyntaxError, which is how the Dagster page came to report
 *
 *     unexpected token '교', "교차 사이트 요청이 차단되었습니다." is not valid json
 *
 * when the server had already said, in plain Korean, exactly what was wrong.
 */

/** Read the body once, as JSON when it is JSON and as text when it is not. */
export async function readBody<T>(response: Response): Promise<{ data: T | null; text: string }> {
  const text = await response.text();
  if (!text) return { data: null, text: "" };
  try {
    return { data: JSON.parse(text) as T, text };
  } catch {
    return { data: null, text };
  }
}

//: Long enough for the messages the server actually sends, short enough that an
//: HTML error page becomes the status line rather than a wall of markup.
const MAX_SERVER_SENTENCE = 200;

/**
 * The clearest sentence available for a failed response: the API's own
 * ``detail`` field, else a short plain-text body, else the status code.
 */
export function failureMessage(
  response: Response,
  body: { data: unknown; text: string },
  fallback = "요청 실패",
): string {
  const detail = (body.data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();

  const text = body.text.trim();
  // An HTML error page says nothing a reader wants; the status code says more.
  const looksLikeMarkup = text.startsWith("<");
  if (text && !looksLikeMarkup && text.length <= MAX_SERVER_SENTENCE) return text;

  return `${fallback} (${response.status})`;
}
