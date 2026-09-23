import assert from "node:assert/strict";
import test from "node:test";

import { waitForLlmReady } from "./llm-readiness.mjs";

test("waits for the configured OpenAI-compatible models endpoint", async () => {
  let requestedUrl;
  let requestedHeaders;

  await waitForLlmReady({
    baseUrl: "http://llama-cpp-api.llm-gateway.svc.cluster.local:8000/v1/",
    apiKey: "test-key",
    fetchImpl: async (url, options) => {
      requestedUrl = url.toString();
      requestedHeaders = options.headers;
      return { ok: true, body: { cancel: async () => {} } };
    },
  });

  assert.equal(requestedUrl, "http://llama-cpp-api.llm-gateway.svc.cluster.local:8000/v1/models");
  assert.deepEqual(requestedHeaders, { Authorization: "Bearer test-key" });
});

test("retries connection errors and non-ready responses until the model is ready", async () => {
  const responses = [
    { ok: false, status: 503, body: { cancel: async () => {} } },
    new TypeError("fetch failed"),
    { ok: true, body: { cancel: async () => {} } },
  ];
  let attempts = 0;

  await waitForLlmReady({
    baseUrl: "http://llama-cpp-api/v1",
    timeoutMs: 1_000,
    pollIntervalMs: 1,
    fetchImpl: async () => {
      attempts += 1;
      const response = responses.shift();
      if (response instanceof Error) throw response;
      return response;
    },
  });

  assert.equal(attempts, 3);
});

test("fails with a bounded timeout while the endpoint remains unavailable", async () => {
  await assert.rejects(
    waitForLlmReady({
      baseUrl: "http://llama-cpp-api/v1",
      timeoutMs: 10,
      pollIntervalMs: 1,
      fetchImpl: async () => ({ ok: false, status: 503, body: { cancel: async () => {} } }),
    }),
    /did not become ready within 10 ms/u,
  );
});

test("cancels promptly when the Pi turn is aborted", async () => {
  const controller = new AbortController();
  controller.abort(new Error("cancelled by test"));
  let attempts = 0;

  await assert.rejects(
    waitForLlmReady({
      baseUrl: "http://llama-cpp-api/v1",
      signal: controller.signal,
      fetchImpl: async () => {
        attempts += 1;
        return { ok: true, body: { cancel: async () => {} } };
      },
    }),
    /cancelled by test/u,
  );
  assert.equal(attempts, 0);
});
