import { execFile } from "node:child_process";
import { constants, rmSync } from "node:fs";
import { copyFile, lstat, mkdir, mkdtemp, open, realpath } from "node:fs/promises";
import { basename, extname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { createRequire } from "node:module";
import { promisify } from "node:util";
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

function toolError(message) {
  return { content: [{ type: "text", text: message }], details: {}, isError: true };
}

export default function (pi) {
  process.once("exit", () => {
    for (const directory of stagedDirectories) {
      try { rmSync(directory, { recursive: true, force: true }); } catch {}
    }
  });

  pi.on("tool_result", async (event, ctx) => {
    if (event.toolName !== "image_generate" || event.isError) return;

    await waitForLlmReady({
      baseUrl: process.env.LLM_BASE_URL,
      apiKey: process.env.LLM_GATEWAY_API_KEY,
      signal: ctx.signal,
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
        const inputs = [...new Set(process.argv
          .filter((arg) => arg.startsWith("@"))
          .map((arg) => arg.slice(1))
          .filter(Boolean))];
        const sources = [];
        for (const input of inputs) {
          const path = resolve(input);
          if (!isWithin(attachmentsRoot, path)) continue;
          const validated = await validateImageFile(path, attachmentsRoot, "Reference image");
          sources.push(validated);
        }
        if (sources.length === 0) return toolError("No supported image attachments were found in the current Discord message.");
        if (sources.length > 4) return toolError("At most four reference images can be used at once.");
        const totalBytes = sources.reduce((sum, image) => sum + image.size, 0);
        if (totalBytes > maxBatchBytes) return toolError("The reference image attachments exceed 50 MiB in total.");
        const cwd = resolve(ctx.cwd || process.cwd());
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
    name: "send_generated_images_to_discord",
    label: "Send generated images to Discord",
    description: "Upload generated image files from the configured Pi image output directory to the Discord channel for the current session. Use only after image_generate succeeds.",
    promptSnippet: "After image_generate succeeds, call send_generated_images_to_discord with every generated output path before telling the user the image is ready.",
    parameters: Type.Object({
      images: Type.Array(Type.String({ description: "Absolute path returned by image_generate." }), {
        minItems: 1,
        maxItems: 4,
        description: "Image paths returned by image_generate.",
      }),
    }, { additionalProperties: false }),
    async execute(_toolCallId, params) {
      try {
        const uniquePaths = [...new Set(params.images)];
        if (uniquePaths.length !== params.images.length) throw new Error("Duplicate image paths are not allowed.");
        const images = [];
        for (const path of uniquePaths) images.push(await validateImageFile(path, outputRoot, "Generated image"));
        if (images.reduce((sum, image) => sum + image.size, 0) > maxBatchBytes) {
          throw new Error("The generated image files exceed 50 MiB in total.");
        }
        const channelJid = await currentChannelJid();
        const args = ["send", "--channel", channelJid];
        for (const path of uniquePaths) args.push("--file", path);
        await execFileAsync(piscordCli, args, { timeout: 120000, maxBuffer: 1024 * 1024 });
        return {
          content: [{ type: "text", text: `Sent ${uniquePaths.length} generated image(s) to the current Discord channel.` }],
          details: { count: uniquePaths.length },
        };
      } catch (error) {
        return toolError(error instanceof Error ? error.message : "Could not send the generated images to Discord.");
      }
    },
  });
}
