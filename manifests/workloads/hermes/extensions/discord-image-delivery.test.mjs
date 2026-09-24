import assert from "node:assert/strict";
import test from "node:test";

import { extractGeneratedImagePaths, finalizeImageDelivery } from "./discord-image-delivery.mjs";

test("extracts paths from the structured image_generate result", () => {
  const paths = extractGeneratedImagePaths({
    model: "sd-cpp-local",
    images: [
      { path: "/opt/pi/.pi/images/generated/first.png", mimeType: "image/png" },
      { path: "/opt/pi/.pi/images/generated/second.webp", mimeType: "image/webp" },
    ],
  });

  assert.deepEqual(paths, [
    "/opt/pi/.pi/images/generated/first.png",
    "/opt/pi/.pi/images/generated/second.webp",
  ]);
});

test("rejects invalid, duplicate, empty, and oversized image results", () => {
  assert.throws(() => extractGeneratedImagePaths(undefined), /invalid number/u);
  assert.throws(() => extractGeneratedImagePaths({ images: [] }), /invalid number/u);
  assert.throws(() => extractGeneratedImagePaths({ images: [{ path: "relative.png" }] }), /invalid output file path/u);
  assert.throws(() => extractGeneratedImagePaths({ images: [{ path: "/tmp/one.png" }, { path: "/tmp/one.png" }] }), /duplicate/u);
  assert.throws(() => extractGeneratedImagePaths({ images: Array.from({ length: 5 }, (_, i) => ({ path: `/tmp/${i}.png` })) }), /invalid number/u);
});

test("uploads before waiting for the LLM and returns a path-free summary", async () => {
  const order = [];
  const result = await finalizeImageDelivery({
    details: { model: "sd-cpp-local", images: [{ path: "/opt/pi/.pi/images/generated/result.png" }] },
    deliverImages: async (paths) => {
      order.push("upload");
      assert.deepEqual(paths, ["/opt/pi/.pi/images/generated/result.png"]);
    },
    waitForLlm: async () => order.push("llm-ready"),
  });

  assert.deepEqual(order, ["upload", "llm-ready"]);
  assert.equal(result.isError, false);
  assert.deepEqual(result.details, { delivery: "sent", imageCount: 1, llmReady: true });
  assert.match(result.content[0].text, /sent 1 image/u);
  assert.doesNotMatch(result.content[0].text, /\/opt\/pi/u);
});

test("hides local paths on upload failure and still waits for the LLM", async () => {
  let llmWaited = false;
  const result = await finalizeImageDelivery({
    details: { images: [{ path: "/opt/pi/private/result.png" }] },
    deliverImages: async () => { throw new Error("failed to send /opt/pi/private/result.png"); },
    waitForLlm: async () => { llmWaited = true; },
  });

  assert.equal(llmWaited, true);
  assert.equal(result.isError, true);
  assert.equal(result.details.delivery, "failed");
  assert.doesNotMatch(result.content[0].text, /\/opt\/pi/u);
});
