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
    const responseCache = new Map();
    const responseCacheLimit = 64;

    function cachedResponse(key) {
      const cached = responseCache.get(key);
      if (!cached) return null;
      responseCache.delete(key);
      responseCache.set(key, cached);
      return cached;
    }

    function rememberResponse(key, value) {
      responseCache.delete(key);
      responseCache.set(key, value);
      while (responseCache.size > responseCacheLimit) {
        responseCache.delete(responseCache.keys().next().value);
      }
    }

    function clearCache() {
      responseCache.clear();
    }

    async function request(url, options = {}) {
      const { headers: optionHeaders = {}, ...requestOptions } = options;
      const method = String(requestOptions.method || "GET").toUpperCase();
      const isJsonBody = typeof requestOptions.body === "string";
      const requestUrl = new URL(String(url), window.location.href);
      const isSameOrigin = requestUrl.origin === window.location.origin;
      const headers = new Headers(optionHeaders);
      if (isJsonBody && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
      const cacheKey = requestUrl.href;
      const cacheableRequest = method === "GET" && isSameOrigin && !requestOptions.body;
      const cached = cacheableRequest ? cachedResponse(cacheKey) : null;
      if (cached && !headers.has("If-None-Match")) headers.set("If-None-Match", cached.etag);
      const response = await fetch(url, {
        credentials: "same-origin",
        headers,
        ...requestOptions,
      });

      if (response.status === 304 && cached) return cached.payload;

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
      const etag = response.headers.get("ETag") || "";
      const cacheControl = response.headers.get("Cache-Control") || "";
      if (
        cacheableRequest
        && etag
        && contentType.includes("application/json")
        && !cacheControl.toLowerCase().includes("no-store")
      ) {
        rememberResponse(cacheKey, { etag, payload });
      }
      return payload;
    }

    return Object.freeze({ clearCache, request });
}
