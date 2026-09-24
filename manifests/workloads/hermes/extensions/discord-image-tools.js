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
const execFileAsync = promisify(execFile);
const attachmentsRoot = resolve(process.env.PISCORD_ATTACHMENTS_DIR || "/attachments");
const outputRoot = resolve(process.env.PI_IMAGE_GENERATION_OUTPUT_DIR || "/opt/pi/.pi/images/generated");
const piscordCli = process.env.PISCORD_CLI || "/usr/local/bin/piscord";
const maxImageBytes = 20 * 1024 * 1024;
const maxBatchBytes = 50 * 1024 * 1024;
const allowedExtensions = new Set([".png", ".jpg", ".jpeg", ".webp"]);
const stagedDirectories = new Set();

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
  for (const path of paths) images.push(await validateImageFile(path, outputRoot, "Generated image"));
  if (images.reduce((sum, image) => sum + image.size, 0) > maxBatchBytes) {
    throw new Error("The generated image files exceed 50 MiB in total.");
  }
  const channelJid = await currentChannelJid();
  const args = ["send", "--channel", channelJid];
  for (const image of images) args.push("--file", image.canonicalPath);
  await execFileAsync(piscordCli, args, { timeout: 120000, maxBuffer: 1024 * 1024 });
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

async function stageReferenceImages(inputPaths, cwd) {
  const sourcePaths = [...new Set(inputPaths.map((input) => resolve(input)))];
  if (sourcePaths.length < 1 || sourcePaths.length > 4) {
    throw new Error("At most four reference images can be used at once.");
  }

  const sources = [];
  for (const path of sourcePaths) {
    if (!isWithin(attachmentsRoot, path)) {
      throw new Error("Reference image is not an attachment from the current Discord message.");
    }
    sources.push(await validateImageFile(path, attachmentsRoot, "Reference image"));
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

function toolError(message) {
  return { content: [{ type: "text", text: message }], details: {}, isError: true };
}

export default function (pi) {
  process.once("exit", () => {
    for (const directory of stagedDirectories) {
      try { rmSync(directory, { recursive: true, force: true }); } catch {}
    }
  });

  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName !== "image_generate") return;
    event.input.outputDir = outputRoot;

    const rawImages = event.input.image;
    const imagePaths = typeof rawImages === "string"
      ? [rawImages]
      : Array.isArray(rawImages)
        ? rawImages.filter((path) => typeof path === "string")
        : [];
    const attachmentPaths = imagePaths.filter((path) => isWithin(attachmentsRoot, resolve(path)));
    if (attachmentPaths.length === 0) return;

    const currentAttachments = new Set(currentMessageAttachmentPaths().map((path) => resolve(path)));
    if (attachmentPaths.some((path) => !currentAttachments.has(resolve(path)))) {
      return {
        block: true,
        reason: "This image path is not attached to the current Discord message. Do not retry it. For an explicit edit of the immediately previous successful image, use that image's reference path from its successful image_generate result; otherwise ask the user to attach the source image again.",
      };
    }

    try {
      const staged = await stageReferenceImages(attachmentPaths, ctx.cwd);
      const replacePath = (path) => staged.get(resolve(path)) || path;
      event.input.image = typeof rawImages === "string"
        ? replacePath(rawImages)
        : rawImages.map((path) => typeof path === "string" ? replacePath(path) : path);
    } catch (error) {
      const errorName = error instanceof Error ? error.name : "unknown error";
      console.error(`[discord-image-tools] Reference image staging failed (${errorName})`);
      return {
        block: true,
        reason: "The current Discord reference image could not be staged, so image generation did not run. Do not retry this request or claim success; explain the failure briefly in Japanese.",
      };
    }
  });

  pi.on("tool_result", async (event, ctx) => {
    if (event.toolName !== "image_generate" || event.isError) return;

    return finalizeImageDelivery({
      details: event.details,
      signal: ctx.signal,
      deliverImages: uploadGeneratedImageFiles,
      waitForLlm: (signal) => waitForLlmReady({
        baseUrl: process.env.LLM_BASE_URL,
        apiKey: process.env.LLM_GATEWAY_API_KEY,
        signal,
      }),
      onError: (operation, error) => {
        const errorName = error instanceof Error ? error.name : "unknown error";
        console.error(`[discord-image-tools] ${operation} failed (${errorName})`);
      },
    });
  });

  pi.registerTool({
    name: "stage_discord_reference_images",
    label: "Stage Discord reference images",
    description: "Copy image attachments from the current Discord message into the Pi session directory so image_generate can safely use them as references. Call this for image edits or when the user asks to base a new image on an attached image.",
    promptSnippet: "For image edits or requests based on attached images, call stage_discord_reference_images first and pass its returned file paths as image_generate.image.",
    parameters: Type.Object({}, { additionalProperties: false }),
    async execute(_toolCallId, _params, _signal, _onUpdate, ctx) {
      try {
        const inputs = currentMessageAttachmentPaths();
        const sourcePaths = inputs.filter((input) => isWithin(attachmentsRoot, resolve(input)));
        if (sourcePaths.length === 0) return toolError("No supported image attachments were found in the current Discord message.");
        const staged = await stageReferenceImages(sourcePaths, ctx.cwd || process.cwd());
        const paths = [...staged.values()];
        return {
          content: [{ type: "text", text: `Reference images staged for image_generate.image:\n${paths.map((path) => `- ${path}`).join("\n")}` }],
          details: { images: paths },
        };
      } catch (error) {
        return toolError(error instanceof Error ? error.message : "Could not stage the Discord reference images.");
      }
    },
  });

  pi.registerTool({
    name: "get_last_sent_image_reference",
    label: "Get last sent image reference",
    description: "Retrieve the most recently generated image successfully sent to the current Discord channel, for an explicit follow-up edit of that image.",
    promptSnippet: "For an explicit edit of the immediately previous image sent by this bot, call this tool and pass its returned path to image_generate.image. Never show the path to the user.",
    parameters: Type.Object({}, { additionalProperties: false }),
    async execute() {
      try {
        const paths = await latestSentImagePaths();
        return {
          content: [{
            type: "text",
            text: `Use these references only when the user explicitly asks to edit the most recently sent image. Never show the paths to the user:\n${paths.map((path) => `- ${path}`).join("\n")}`,
          }],
          details: { images: paths },
        };
      } catch (error) {
        const errorName = error instanceof Error ? error.name : "unknown error";
        console.error(`[discord-image-tools] Previous image lookup failed (${errorName})`);
        return toolError("No successfully sent image is available in the current Discord channel.");
      }
    },
  });
}
