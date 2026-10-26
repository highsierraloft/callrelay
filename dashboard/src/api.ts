export class ApiError extends Error {
  constructor(
    public status: number,
    text: string,
  ) {
    super(text);
  }
}
let csrf = "";
export const setCsrf = (value: string) => {
  csrf = value;
};
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
  keepalive = false,
): Promise<T> {
  const response = await fetch("/admin/api" + path, {
    method,
    credentials: "same-origin",
    keepalive,
    signal,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(method === "GET" ? {} : { "X-CSRF-Token": csrf }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      const detail = data.detail ?? data.error;
      if (typeof detail === "string") {
        message = detail;
        try {
          message = JSON.parse(detail).message || detail;
        } catch {
          /* Plain error text. */
        }
      } else if (Array.isArray(detail))
        message = detail
          .map(
            (v: { msg: string; loc?: string[] }) =>
              `${v.loc?.slice(1).join(".") || "Value"}: ${v.msg}`,
          )
          .join("; ");
    } catch {
      /* Keep the HTTP status when the connection returns no JSON. */
    }
    throw new ApiError(response.status, message);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export const message = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
