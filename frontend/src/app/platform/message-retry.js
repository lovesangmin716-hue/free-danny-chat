function cancelled() {
  const error = new Error("Session changed while sending a message");
  error.name = "AbortError";
  return error;
}

export async function postMessageWithRetry(request, payload, isCurrent = () => true) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (!isCurrent()) throw cancelled();
    try {
      const result = await request("messages.send", "/messages", {
        method: "POST",
        body: JSON.stringify(payload),
      }, { allowStaleResult: true });
      return result;
    } catch (error) {
      if (!isCurrent() || error?.name === "AbortError") throw cancelled();
      const retryable = !error?.status || error.status === 429 || error.status >= 500;
      if (!retryable || attempt === 2) throw error;
      const retryAfter = Math.min(Number(error.retryAfter || 0) * 1000, 3000);
      const backoff = 250 * (2 ** attempt) + Math.floor(Math.random() * 100);
      await new Promise((resolve) => window.setTimeout(resolve, Math.max(retryAfter, backoff)));
    }
  }
  throw new Error("메시지를 전송하지 못했습니다.");
}

export function createClientMessageId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `client_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 14)}`;
}
