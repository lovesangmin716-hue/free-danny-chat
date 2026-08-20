// One HTTP contract for JSON, form, binary, authentication, and rate-limit failures.
export class HttpError extends Error {
    constructor(message, details = {}) {
      super(message);
      this.name = "HttpError";
      this.status = Number(details.status || 0);
      this.method = details.method || "GET";
      this.url = details.url || "";
      this.retryAfter = Number(details.retryAfter || 0);
      this.payload = details.payload ?? null;
    }
  }

export function createHttpClient({ onUnauthorized } = {}) {
    async function request(url, options = {}) {
      const { headers: optionHeaders = {}, ...requestOptions } = options;
      const method = String(requestOptions.method || "GET").toUpperCase();
      const isJsonBody = typeof requestOptions.body === "string";
      const requestUrl = new URL(String(url), window.location.href);
      const isSameOrigin = requestUrl.origin === window.location.origin;
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: {
          ...(isJsonBody ? { "Content-Type": "application/json" } : {}),
          ...optionHeaders,
        },
        ...requestOptions,
      });

      const contentType = response.headers.get("Content-Type") || "";
      let payload = null;
      if (contentType.includes("application/json")) {
        try {
          payload = await response.json();
        } catch (_) {
          payload = null;
        }
      }

      const statusLabel = `${response.status}${response.statusText ? ` ${response.statusText}` : ""}`;
      const fallbackMessage = `${method} ${url} 요청 실패 (HTTP ${statusLabel})`;
      const payloadMessage = typeof payload?.error === "string"
        ? payload.error
        : (payload?.error?.message || payload?.message || "");
      if (response.status === 401 && isSameOrigin && typeof onUnauthorized === "function") onUnauthorized();
      if (!response.ok) {
        throw new HttpError(payloadMessage || fallbackMessage, {
          status: response.status,
          method,
          url,
          retryAfter: response.headers.get("Retry-After"),
          payload,
        });
      }
      return payload;
    }

    return Object.freeze({ request });
}
