const defaultTimeoutMs = 180_000;
const defaultPollIntervalMs = 2_000;
const defaultRequestTimeoutMs = 5_000;

function abortError(signal) {
  return signal?.reason instanceof Error
    ? signal.reason
    : new Error("LLM readiness check was cancelled.");
}

function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError(signal));
      return;
    }

    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);

    function onAbort() {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      reject(abortError(signal));
    }

    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function requestSignal(parentSignal, timeoutMs) {
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  return parentSignal ? AbortSignal.any([parentSignal, timeoutSignal]) : timeoutSignal;
}

export async function waitForLlmReady({
  baseUrl,
  apiKey,
  signal,
  timeoutMs = defaultTimeoutMs,
  pollIntervalMs = defaultPollIntervalMs,
  requestTimeoutMs = defaultRequestTimeoutMs,
  fetchImpl = globalThis.fetch,
  now = Date.now,
} = {}) {
  if (typeof baseUrl !== "string" || baseUrl.trim() === "") {
    throw new Error("LLM_BASE_URL is required to wait for the LLM endpoint.");
  }
  if (typeof fetchImpl !== "function") throw new Error("fetch is unavailable.");
  for (const [name, value] of Object.entries({ timeoutMs, pollIntervalMs, requestTimeoutMs })) {
    if (!Number.isFinite(value) || value <= 0) throw new RangeError(`${name} must be a positive number.`);
  }

  const modelsUrl = new URL("models", `${baseUrl.replace(/\/+$/u, "")}/`);
  if (modelsUrl.protocol !== "http:" && modelsUrl.protocol !== "https:") {
    throw new Error("LLM_BASE_URL must use HTTP or HTTPS.");
  }

  const deadline = now() + timeoutMs;
  let lastFailure = "no response";
  let attempted = false;

  while (!attempted || now() < deadline) {
    if (signal?.aborted) throw abortError(signal);
    const remainingMs = deadline - now();
    if (attempted && remainingMs <= 0) break;
    attempted = true;

    const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : {};
    try {
      const response = await fetchImpl(modelsUrl, {
        method: "GET",
        headers,
        signal: requestSignal(signal, Math.max(1, Math.min(requestTimeoutMs, remainingMs))),
      });
      if (response.ok) {
        await response.body?.cancel().catch(() => {});
        return;
      }
      lastFailure = `HTTP ${response.status}`;
      await response.body?.cancel().catch(() => {});
    } catch (error) {
      if (signal?.aborted) throw abortError(signal);
      lastFailure = error instanceof Error ? error.name : "request failed";
    }

    const delayMs = Math.min(pollIntervalMs, deadline - now());
    if (delayMs > 0) await delay(delayMs, signal);
  }

  throw new Error(`LLM endpoint did not become ready within ${timeoutMs} ms (last response: ${lastFailure}).`);
}
