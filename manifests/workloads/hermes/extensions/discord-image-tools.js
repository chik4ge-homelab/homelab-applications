import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { constants, rmSync } from "node:fs";
import { copyFile, lstat, mkdir, mkdtemp, open, readFile, realpath, rename, writeFile } from "node:fs/promises";
import { basename, extname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { createRequire } from "node:module";
import { promisify } from "node:util";
import { finalizeImageDelivery } from "./discord-image-delivery.mjs";
import { waitForLlmReady } from "./llm-readiness.mjs";

const piRequire = createRequire("/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/cli.js");
const { Type } = piRequire("typebox");
const { createJiti } = piRequire("jiti");
const imageGenRequire = createRequire("/opt/pi/.pi/agent/npm/package.json");
const imageGenJiti = createJiti(import.meta.url, {
  moduleCache: false,
  alias: {
    "@earendil-works/pi-ai": "/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/index.js",
    typebox: piRequire.resolve("typebox"),
  },
});
const execFileAsync = promisify(execFile);
const attachmentsRoot = resolve(process.env.PISCORD_ATTACHMENTS_DIR || "/attachments");
const outputRoot = resolve(process.env.PI_IMAGE_GENERATION_OUTPUT_DIR || "/opt/pi/.pi/images/generated");
const piscordCli = process.env.PISCORD_CLI || "/usr/local/bin/piscord";
const maxImageBytes = 20 * 1024 * 1024;
const maxBatchBytes = 50 * 1024 * 1024;
const allowedExtensions = new Set([".png", ".jpg", ".jpeg", ".webp"]);
const stagedDirectories = new Set();
let imageGenModulePromise;

function imageGenModule() {
  if (!imageGenModulePromise) {
    const entry = imageGenRequire.resolve("@amaster.ai/pi-image-gen");
    imageGenModulePromise = imageGenJiti.import(entry);
  }
  return imageGenModulePromise;
}

function isWithin(root, path) {
  const pathFromRoot = relative(root, path);
  return pathFromRoot !== "" && pathFromRoot !== ".." && !pathFromRoot.startsWith(`..${sep}`) && !isAbsolute(pathFromRoot);
}

async function validateImageFile(inputPath, root, label) {
  const absolutePath = resolve(inputPath);
  if (!isWithin(root, absolutePath)) throw new Error(`${label} is outside the allowed directory.`);
  const info = await lstat(absolutePath);
  if (info.isSymbolicLink() || !info.isFile()) throw new Error(`${label} must be a regular file.`);
  if (info.size <= 0 || info.size > maxImageBytes) throw new Error(`${label} must be between 1 byte and 20 MiB.`);
  const canonicalRoot = await realpath(root);
  const canonicalPath = await realpath(absolutePath);
  if (!isWithin(canonicalRoot, canonicalPath)) throw new Error(`${label} resolves outside the allowed directory.`);
  const extension = extname(canonicalPath).toLowerCase();
  if (!allowedExtensions.has(extension)) throw new Error(`${label} must be PNG, JPEG, or WebP.`);
  const file = await open(canonicalPath, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const openedInfo = await file.stat();
    if (!openedInfo.isFile() || openedInfo.dev !== info.dev || openedInfo.ino !== info.ino) {
      throw new Error(`${label} changed while it was being checked.`);
    }
    const header = Buffer.alloc(12);
    const { bytesRead } = await file.read(header, 0, header.length, 0);
    const bytes = header.subarray(0, bytesRead);
    const valid = extension === ".png"
      ? bytes.length >= 8 && bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
      : extension === ".webp"
        ? bytes.length >= 12 && bytes.toString("ascii", 0, 4) === "RIFF" && bytes.toString("ascii", 8, 12) === "WEBP"
        : bytes.length >= 3 && bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
    if (!valid) throw new Error(`${label} does not match its image file extension.`);
  } finally {
    await file.close();
  }
  return { canonicalPath, size: info.size };
}

function currentDiscordSessionFolder() {
  const args = process.argv;
  const equalsArg = args.find((arg) => arg.startsWith("--session-dir="));
  const index = args.indexOf("--session-dir");
  const sessionDir = equalsArg ? equalsArg.slice("--session-dir=".length) : args[index + 1];
  if (!sessionDir) throw new Error("Could not identify the current Discord session.");
  const folder = basename(resolve(sessionDir));
  if (!folder || folder === "." || folder === "..") throw new Error("Current Discord session folder is invalid.");
  return folder;
}

function currentMessageAttachmentPaths() {
  const paths = new Set();
  for (const arg of process.argv) {
    if (arg.startsWith("@")) paths.add(arg.slice(1));
    for (const match of arg.matchAll(/<file\b[^>]*\bname=(["'])(.*?)\1[^>]*>/gsu)) {
      paths.add(match[2]);
    }
  }
  return [...paths].filter(Boolean);
}

async function currentChannelJid() {
  const folder = currentDiscordSessionFolder();
  const { stdout } = await execFileAsync(piscordCli, ["channels"], { timeout: 10000, maxBuffer: 1024 * 1024 });
  for (const line of stdout.split(/\r?\n/u)) {
    const jid = line.trim().split(/\s+/u, 1)[0];
    const channelFolder = line.match(/(?:^|\s)folder=([^\s]+)/u)?.[1];
    if (jid?.startsWith("dc:") && channelFolder === folder) return jid;
  }
  throw new Error("Could not map the current Discord session to a channel.");
}

function latestSentImageRecordPath(channelJid) {
  const channelId = channelJid.match(/^dc:(\d+)$/u)?.[1];
  if (!channelId) throw new Error("Current Discord channel ID is invalid.");
  return join(outputRoot, `.last-sent-${channelId}.json`);
}

async function rememberSentImages(channelJid, paths) {
  const recordPath = latestSentImageRecordPath(channelJid);
  const temporaryPath = `${recordPath}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporaryPath, JSON.stringify({ channelJid, paths, sentAt: new Date().toISOString() }), {
      flag: "wx",
      mode: 0o600,
    });
    await rename(temporaryPath, recordPath);
  } catch (error) {
    try { rmSync(temporaryPath, { force: true }); } catch {}
    throw error;
  }
}

async function uploadGeneratedImageFiles(paths) {
  const images = [];
  let channelJid;
  try {
    for (const path of paths) images.push(await validateImageFile(path, outputRoot, "Generated image"));
    if (images.reduce((sum, image) => sum + image.size, 0) > maxBatchBytes) {
      throw new Error("The generated image files exceed 50 MiB in total.");
    }
    channelJid = await currentChannelJid();
  } catch {
    const error = new Error("Discord image delivery could not be prepared.");
    error.name = "DiscordImageDeliveryError";
    error.phase = "prepare";
    throw error;
  }

  const args = ["send", "--channel", channelJid];
  for (const image of images) args.push("--file", image.canonicalPath);
  try {
    await execFileAsync(piscordCli, args, { timeout: 120000, maxBuffer: 1024 * 1024 });
  } catch {
    const error = new Error("Discord image send failed.");
    error.name = "DiscordImageDeliveryError";
    error.phase = "send";
    throw error;
  }
  try {
    await rememberSentImages(channelJid, images.map((image) => image.canonicalPath));
  } catch (error) {
    const errorName = error instanceof Error ? error.name : "unknown error";
    console.error(`[discord-image-tools] Latest sent image record failed (${errorName})`);
  }
}

async function latestSentImagePaths() {
  const channelJid = await currentChannelJid();
  const recordPath = latestSentImageRecordPath(channelJid);
  const recordInfo = await lstat(recordPath);
  if (recordInfo.isSymbolicLink() || !recordInfo.isFile() || recordInfo.size > 16 * 1024) {
    throw new Error("Latest sent image record is invalid.");
  }
  const record = JSON.parse(await readFile(recordPath, "utf8"));
  if (record.channelJid !== channelJid || !Array.isArray(record.paths) || record.paths.length < 1 || record.paths.length > 4) {
    throw new Error("Latest sent image record is invalid.");
  }
  const images = [];
  for (const path of record.paths) images.push(await validateImageFile(path, outputRoot, "Previously sent image"));
  return images.map((image) => image.canonicalPath);
}

async function stageReferenceImages(inputPaths, cwd, sourceRoot = attachmentsRoot, label = "Reference image") {
  const sourcePaths = [...new Set(inputPaths.map((input) => resolve(input)))];
  if (sourcePaths.length < 1 || sourcePaths.length > 4) {
    throw new Error("At most four reference images can be used at once.");
  }

  const sources = [];
  for (const path of sourcePaths) {
    if (!isWithin(sourceRoot, path)) {
      throw new Error(`${label} is outside the allowed directory.`);
    }
    sources.push(await validateImageFile(path, sourceRoot, label));
  }
  if (sources.reduce((sum, image) => sum + image.size, 0) > maxBatchBytes) {
    throw new Error("The reference image attachments exceed 50 MiB in total.");
  }

  const referenceRoot = resolve(cwd, ".pi/images/references");
  await mkdir(referenceRoot, { recursive: true });
  const stagingDir = await mkdtemp(join(referenceRoot, "request-"));
  stagedDirectories.add(stagingDir);
  const paths = [];
  for (const [index, source] of sources.entries()) {
    const safeName = basename(source.canonicalPath).replace(/[^a-zA-Z0-9._-]/gu, "_");
    const target = join(stagingDir, `${index + 1}-${safeName}`);
    await copyFile(source.canonicalPath, target, constants.COPYFILE_EXCL);
    paths.push(target);
  }
  return new Map(sourcePaths.map((path, index) => [path, paths[index]]));
}

function sessionImageAttachmentPaths(sessionManager) {
  const pathsByName = new Map();
  const addPath = (inputPath) => {
    if (typeof inputPath !== "string") return;
    const path = resolve(inputPath);
    if (!isWithin(attachmentsRoot, path) || !allowedExtensions.has(extname(path).toLowerCase())) return;
    const name = basename(path);
    const paths = pathsByName.get(name) || new Set();
    paths.add(path);
    pathsByName.set(name, paths);
  };

  for (const entry of sessionManager.getBranch()) {
    if (entry.type !== "message" || entry.message?.role !== "user") continue;
    const content = entry.message.content;
    const text = typeof content === "string"
      ? content
      : Array.isArray(content)
        ? content.filter((part) => part?.type === "text").map((part) => part.text).join("\n")
        : "";
    for (const match of text.matchAll(/<file\b[^>]*\bname=(["'])(.*?)\1[^>]*>/gsu)) addPath(match[2]);
  }

  for (const path of currentMessageAttachmentPaths()) addPath(path);
  return pathsByName;
}

function filterImageContext(messages) {
  return messages.map((message) => {
    if (message.role === "toolResult" && ["image_generate", "stage_discord_reference_images", "get_last_sent_image_reference"].includes(message.toolName)) {
      const delivery = message.details?.delivery;
      const status = typeof delivery === "string" ? `delivery=${delivery}` : "delivery=unknown";
      return {
        ...message,
        content: [{ type: "text", text: `過去の画像ツール結果（${status}）。内部画像ファイルの参照情報は会話コンテキストから除去済みです。` }],
        details: undefined,
      };
    }

    const filterText = (source) => {
      let text = source;
      if (message.role === "user") {
        text = text.replace(/<file\b[^>]*\bname=(["'])(.*?)\1[^>]*>/gsu, (tag, _quote, inputPath) => {
          const path = resolve(inputPath);
          if (!isWithin(attachmentsRoot, path) || !allowedExtensions.has(extname(path).toLowerCase())) return tag;
          return `<file name="${basename(path)}">`;
        });
      }
      return stripInternalImagePaths(text);
    };

    if (typeof message.content === "string") {
      const text = filterText(message.content);
      return text === message.content ? message : { ...message, content: text };
    }
    if (!Array.isArray(message.content)) return message;

    let changed = false;
    const content = message.content.map((part) => {
      if (part.type === "text") {
        const text = filterText(part.text);
        if (text !== part.text) changed = true;
        return text === part.text ? part : { ...part, text };
      }
      if (message.role === "assistant" && part.type === "toolCall" && part.name === "image_generate" && part.arguments) {
        const arguments_ = { ...part.arguments };
        const hadImage = Object.hasOwn(arguments_, "image");
        const hadOutputDir = Object.hasOwn(arguments_, "outputDir");
        delete arguments_.image;
        delete arguments_.outputDir;
        if (hadImage || hadOutputDir) {
          changed = true;
          return { ...part, arguments: arguments_ };
        }
      }
      return part;
    });
    return changed ? { ...message, content } : message;
  });
}

function stripInternalImagePaths(text) {
  return text.replace(/(?:\/attachments\/|\/(?:[^/\r\n\s<>"'`()\[\]]+\/)*\.pi\/images\/(?:generated|references)\/)[^\r\n\s<>"'`()\[\]]*/gu, (token) => {
    const trailingPunctuation = token.match(/[.,!?;:。！？、]+$/u)?.[0] || "";
    return trailingPunctuation;
  });
}

function toolError(message, delivery = "not-attempted") {
  return { content: [{ type: "text", text: `delivery=${delivery}. ${message}` }], details: { delivery }, isError: true };
}

export default function (pi) {
  let imageActionUsed = false;

  process.once("exit", () => {
    for (const directory of stagedDirectories) {
      try { rmSync(directory, { recursive: true, force: true }); } catch {}
    }
  });

  pi.registerTool({
    name: "discord_image_create",
    label: "画像を生成して送信",
    description: "画像の新規生成または編集から、現在のDiscordチャンネルへの送信までを1回で行います。会話中に添付された画像を使う場合は、該当する `<file name=...>` に表示されたファイル名だけを referenceFiles に指定してください。複数の添付画像から、依頼に合うものを選び、必要な画像だけ指定します。直前にこのボットが送信した画像を編集する場合に限り editLastSentImage=true を指定します。添付画像の取得、内部パスの解決、参照画像の準備、生成、送信はプラグインが処理します。内部パスは渡さず、送信成功を伝えるのは結果が delivery=sent の場合だけにしてください。",
    promptSnippet: "画像を生成または編集してこのDiscordチャンネルに送信します。会話中の添付画像はファイル名で選び、内部パス解決と送信はツールに任せます。",
    promptGuidelines: [
      "画像生成・編集の依頼ごとに1回だけ呼び出す。",
      "会話中の添付画像を使う場合は、ユーザーの依頼と画像内容に合う画像をファイル名で選び、referenceFiles に指定する。",
      "直前に送信した画像の編集をユーザーが明示した場合だけ editLastSentImage=true を指定する。",
      "ファイルパスを引数にせず、内部パスを返信にも出さない。",
      "結果が delivery=sent の場合だけ送信成功を伝える。",
    ],
    parameters: Type.Object({
      prompt: Type.String({ description: "生成したい画像、または編集で加えたい変更を説明します。" }),
      referenceFiles: Type.Array(Type.String({ description: "会話中の添付に表示されたファイル名。パスは指定しません。" }), {
        maxItems: 4,
        uniqueItems: true,
        description: "使う添付画像のファイル名を指定します。新規生成なら空配列です。",
      }),
      editLastSentImage: Type.Optional(Type.Boolean({ description: "直前にこのボットが送信した画像を編集する場合だけ true。" })),
      n: Type.Optional(Type.Integer({ minimum: 1, maximum: 4, description: "生成する枚数（1〜4枚）。省略時は1枚です。" })),
    }, { additionalProperties: false }),
    executionMode: "sequential",
    async execute(_toolCallId, params, signal, _onUpdate, ctx) {
      if (imageActionUsed) {
        return toolError("An image action already ran for this Discord request. Do not retry it; report the previous result.");
      }
      imageActionUsed = true;

      let referencePaths = [];
      try {
        const pathsByName = sessionImageAttachmentPaths(ctx.sessionManager);
        const requestedNames = params.referenceFiles;
        if (requestedNames.some((name) => typeof name !== "string" || name.length === 0 || /[\\/]/u.test(name) || name === "." || name === "..")) {
          return toolError("Choose attached images by filename only; internal paths are not accepted.");
        }
        const selectedPaths = [];
        for (const name of requestedNames) {
          const matches = [...(pathsByName.get(name) || [])];
          if (matches.length === 0) return toolError(`No image attachment named ${name} is available in this conversation.`);
          if (matches.length > 1) return toolError(`The image filename ${name} appears more than once in this conversation; the source is ambiguous.`);
          selectedPaths.push(matches[0]);
        }
        if (selectedPaths.length > 0) {
          const staged = await stageReferenceImages(selectedPaths, ctx.cwd || process.cwd());
          referencePaths = selectedPaths.map((path) => staged.get(resolve(path)));
        }
        if (params.editLastSentImage) {
          const sourcePaths = await latestSentImagePaths();
          if (referencePaths.length + sourcePaths.length > 4) return toolError("At most four reference images can be used at once.");
          const staged = await stageReferenceImages(sourcePaths, ctx.cwd || process.cwd(), outputRoot, "Previously sent image");
          referencePaths.push(...sourcePaths.map((path) => staged.get(resolve(path))));
        }
      } catch (error) {
        const errorName = error instanceof Error ? error.name : "unknown error";
        console.error(`[discord-image-tools] Reference preparation failed (${errorName})`);
        return toolError("The requested reference image could not be prepared. Image generation did not run.");
      }

      let generated;
      try {
        const imageGen = await imageGenModule();
        const settings = imageGen.loadImageGenSettings(ctx.cwd || process.cwd(), ctx.isProjectTrusted());
        const generateParams = {
          prompt: params.prompt,
          outputDir: outputRoot,
          ...(params.n === undefined ? {} : { n: params.n }),
          ...(referencePaths.length === 0 ? {} : { image: referencePaths }),
        };
        generated = await imageGen.generateImage(generateParams, {
          cwd: ctx.cwd || process.cwd(),
          settings,
          signal,
        });
      } catch (error) {
        const errorName = error instanceof Error ? error.name : "unknown error";
        console.error(`[discord-image-tools] Image generation failed (${errorName})`);
        return toolError("Image generation failed. No image was sent to Discord.", "generation-failed");
      }

      return finalizeImageDelivery({
        details: generated,
        signal,
        deliverImages: uploadGeneratedImageFiles,
        waitForLlm: (waitSignal) => waitForLlmReady({
          baseUrl: process.env.LLM_BASE_URL,
          apiKey: process.env.LLM_GATEWAY_API_KEY,
          signal: waitSignal,
        }),
        onError: (operation, error) => {
          const errorName = error instanceof Error ? error.name : "unknown error";
          console.error(`[discord-image-tools] ${operation} failed (${errorName})`);
        },
      });
    },
  });

  pi.on("agent_start", () => {
    imageActionUsed = false;
    const internalImageTools = new Set([
      "image_generate",
      "stage_discord_reference_images",
      "get_last_sent_image_reference",
      "discord_image_create",
    ]);
    pi.setActiveTools([
      ...pi.getActiveTools().filter((name) => !internalImageTools.has(name)),
      "discord_image_create",
    ]);
  });

  pi.on("context", (event) => {
    const messages = filterImageContext(event.messages);
    return { messages };
  });

  pi.on("tool_call", (event) => {
    if (event.toolName !== "image_generate") return;
    return {
      block: true,
      reason: "Use discord_image_create for image requests. It resolves references and sends the image internally; do not call the raw image_generate tool.",
    };
  });
}
