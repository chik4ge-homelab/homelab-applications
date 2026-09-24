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
  let uploadError;
  let paths;
  try {
    paths = extractGeneratedImagePaths(details);
    imageCount = paths.length;
  } catch (error) {
    onError("Image generation result", error);
  }

  if (paths) {
    try {
      await deliverImages(paths);
      delivered = true;
    } catch (error) {
      uploadError = error;
      onError(error?.phase === "prepare" ? "Discord image preparation" : "Discord image upload", error);
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
    ? `Discordチャンネルに画像を${imageCount}枚送信しました。`
    : !paths
      ? "画像生成ツールから有効な画像を取得できなかったため、Discordには送信していません。"
      : uploadError?.phase === "prepare"
        ? `画像は${imageCount}枚生成されましたが、Discordへの送信準備に失敗したため送信していません。`
        : `画像は${imageCount}枚生成されましたが、Discordへの送信に失敗しました。`;
  const contentText = llmReady ? status : `${status} 返信生成用のLLMはまだ利用できません。`;

  return {
    content: [{ type: "text", text: contentText }],
    details: {
      delivery: delivered ? "sent" : !paths ? "not-attempted" : uploadError?.phase === "prepare" ? "preparation-failed" : "failed",
      imageCount,
      llmReady,
    },
    isError: !delivered,
  };
}
