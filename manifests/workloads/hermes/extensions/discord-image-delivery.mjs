import { isAbsolute } from "node:path";

export function extractGeneratedImagePaths(details, maxImages = 4) {
  const images = details && typeof details === "object" ? details.images : undefined;
  if (!Array.isArray(images) || images.length < 1 || images.length > maxImages) {
    throw new Error("Image generation returned an invalid number of output files.");
  }

  const paths = images.map((image) => {
    if (!image || typeof image !== "object" || typeof image.path !== "string" || !isAbsolute(image.path)) {
      throw new Error("Image generation returned an invalid output file path.");
    }
    return image.path;
  });
  if (new Set(paths).size !== paths.length) throw new Error("Image generation returned duplicate output files.");
  return paths;
}

export async function finalizeImageDelivery({ details, signal, deliverImages, waitForLlm, onError = () => {} }) {
  let imageCount = 0;
  let delivered = false;
  let uploadAttempted = false;
  let paths;
  try {
    paths = extractGeneratedImagePaths(details);
    imageCount = paths.length;
  } catch (error) {
    onError("Image generation result", error);
  }

  if (paths) {
    uploadAttempted = true;
    try {
      await deliverImages(paths);
      delivered = true;
    } catch (error) {
      onError("Discord image upload", error);
    }
  }

  let llmReady = true;
  try {
    await waitForLlm(signal);
  } catch (error) {
    if (signal?.aborted) throw error;
    llmReady = false;
    onError("LLM readiness wait", error);
  }

  const status = delivered
    ? `Generated and sent ${imageCount} image(s) to the current Discord channel.`
    : uploadAttempted
      ? `Image generation returned ${imageCount} image(s), but uploading them to Discord failed. Do not claim they were attached.`
      : "Image generation returned no output image. No Discord upload was attempted. Do not claim an image was generated or sent.";
  const contentText = llmReady ? status : `${status} The local LLM endpoint is still unavailable.`;

  return {
    content: [{ type: "text", text: contentText }],
    details: { delivery: delivered ? "sent" : uploadAttempted ? "failed" : "not-attempted", imageCount, llmReady },
    isError: !delivered,
  };
}
