from __future__ import annotations

import base64
import json
import math
import os
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import openvino as ov
from PIL import Image, UnidentifiedImageError
from tokenizers import Tokenizer


MODEL_NAME = os.environ.get("MODEL_NAME", "embeddinggemma-2")
MODEL_ROOT = Path(os.environ.get("MODEL_ROOT", "/models/openvino"))
IMAGE_TOKEN_BUDGET = int(os.environ.get("IMAGE_TOKEN_BUDGET", "256"))
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", str(64 * 1024 * 1024)))
MAX_CONCURRENCY = max(1, int(os.environ.get("MAX_CONCURRENCY", "1")))
PATCH_SIZE = 16
POOL = 3
MAX_PATCHES = IMAGE_TOKEN_BUDGET * POOL**2
IMAGE_TOKEN_ID = 258880
BOS_TOKEN_ID = 2
BOI_TOKEN_ID = 255999
EOI_TOKEN_ID = 258882
EOS_TOKEN_ID = 1
QUERY_PREFIX = "task: search result | query: "
DOCUMENT_PREFIX = "title: none | text: "
EMPTY_FEATURES = np.zeros((0, 512), dtype=np.float32)


@dataclass(frozen=True)
class RequestItem:
    kind: str
    value: str | Image.Image


class EmbeddingRuntime:
    def __init__(self) -> None:
        self.ready = False
        self.device = "GPU"
        self.request_semaphore = threading.Semaphore(MAX_CONCURRENCY)
        self.core = ov.Core()
        if not any(name.startswith("GPU") for name in self.core.available_devices):
            raise RuntimeError(f"Intel GPU unavailable: {self.core.available_devices}")

        properties = {"INFERENCE_PRECISION_HINT": ov.Type.f16}
        text_path = MODEL_ROOT / "openvino" / "model.xml"
        vision_path = MODEL_ROOT / "openvino" / "vision_model.xml"
        tokenizer_path = MODEL_ROOT / "tokenizer.json"
        for path in (text_path, vision_path, tokenizer_path):
            if not path.is_file():
                raise RuntimeError(f"model artifact is missing: {path}")

        started = time.monotonic()
        self.text_model = self.core.compile_model(str(text_path), "GPU", properties)
        vision_ir = self.core.read_model(str(vision_path))
        vision_ir.reshape(
            {
                "pixel_values": [1, MAX_PATCHES, PATCH_SIZE * PATCH_SIZE * 3],
                "pixel_position_ids": [1, MAX_PATCHES, 2],
            }
        )
        self.vision_model = self.core.compile_model(vision_ir, "GPU", properties)
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.device = ",".join(self.text_model.get_property("EXECUTION_DEVICES"))
        self._embed_image(Image.new("RGB", (768, 768), color=(32, 96, 160)))
        self.ready = True
        _log(
            "startup",
            model=MODEL_NAME,
            model_repo=os.environ.get("MODEL_HUB_REPO", "wesjos/embeddinggemma-2-openvino"),
            model_revision=os.environ.get("MODEL_REVISION", "unknown"),
            device=self.device,
            image_token_budget=IMAGE_TOKEN_BUDGET,
            startup_ms=round((time.monotonic() - started) * 1000, 2),
            ready=True,
        )

    @staticmethod
    def _normalize(vector: np.ndarray) -> list[float]:
        values = np.asarray(vector, dtype=np.float32).reshape(-1)
        if values.shape != (768,) or not np.isfinite(values).all():
            raise RuntimeError(f"model returned an invalid embedding shape or value: {values.shape}")
        norm = float(np.linalg.norm(values))
        if not math.isfinite(norm) or norm <= 0:
            raise RuntimeError("model returned an unnormalizable embedding")
        normalized = values / norm
        return normalized.tolist()

    def _embed_texts(self, texts: list[str], input_type: str) -> list[list[float]]:
        prefix = QUERY_PREFIX if input_type == "query" else DOCUMENT_PREFIX
        encodings = self.tokenizer.encode_batch([prefix + text for text in texts])
        width = max(len(encoding.ids) for encoding in encodings)
        input_ids = np.zeros((len(encodings), width), dtype=np.int64)
        attention_mask = np.zeros((len(encodings), width), dtype=np.int64)
        for index, encoding in enumerate(encodings):
            length = len(encoding.ids)
            input_ids[index, :length] = encoding.ids
            attention_mask[index, :length] = 1
        result = self.text_model(
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "image_features": EMPTY_FEATURES,
                "video_features": EMPTY_FEATURES,
                "audio_features": EMPTY_FEATURES,
            }
        )
        matrix = np.asarray(result[self.text_model.output("sentence_embedding")], dtype=np.float32)
        if matrix.shape != (len(texts), 768):
            raise RuntimeError(f"model returned an unexpected text embedding shape: {matrix.shape}")
        return [self._normalize(row) for row in matrix]

    def _embed_image(self, image: Image.Image) -> list[float]:
        width, height = image.size
        valid_patch_count = MAX_PATCHES
        side = POOL * PATCH_SIZE
        scale = math.sqrt(valid_patch_count * PATCH_SIZE**2 / (width * height))
        target_height = max(side, int(scale * height // side) * side)
        target_width = max(side, int(scale * width // side) * side)
        pixels = np.asarray(
            image.resize((target_width, target_height), Image.Resampling.BICUBIC),
            dtype=np.float32,
        )
        pixels = pixels.transpose(2, 0, 1) / 255.0
        patch_height = target_height // PATCH_SIZE
        patch_width = target_width // PATCH_SIZE
        patches = (
            pixels.reshape(3, patch_height, PATCH_SIZE, patch_width, PATCH_SIZE)
            .transpose(1, 3, 2, 4, 0)
            .reshape(patch_height * patch_width, -1)
        )
        grid = np.stack(
            np.meshgrid(np.arange(patch_width), np.arange(patch_height), indexing="xy"),
            axis=-1,
        ).reshape(-1, 2)
        padding = MAX_PATCHES - len(patches)
        if padding < 0:
            raise ValueError(f"image preprocessing produced too many patches: {len(patches)}")
        pixel_values = np.pad(patches, ((0, padding), (0, 0)))[None]
        pixel_position_ids = np.pad(grid, ((0, padding), (0, 0)), constant_values=-1)[None]
        vision_result = self.vision_model(
            {"pixel_values": pixel_values, "pixel_position_ids": pixel_position_ids}
        )
        features = np.asarray(
            vision_result[self.vision_model.output("image_features")], dtype=np.float32
        ).reshape(-1, 512)[:IMAGE_TOKEN_BUDGET]
        token_count = len(features)
        input_ids = np.asarray(
            [[BOS_TOKEN_ID, BOI_TOKEN_ID] + [IMAGE_TOKEN_ID] * token_count + [EOI_TOKEN_ID, EOS_TOKEN_ID]],
            dtype=np.int64,
        )
        result = self.text_model(
            {
                "input_ids": input_ids,
                "attention_mask": np.ones_like(input_ids),
                "image_features": features,
                "video_features": EMPTY_FEATURES,
                "audio_features": EMPTY_FEATURES,
            }
        )
        return self._normalize(result[self.text_model.output("sentence_embedding")])

    def encode(self, items: list[RequestItem], input_type: str) -> list[list[float]]:
        if input_type not in {"query", "document"}:
            raise ValueError("input_type must be query or document")
        vectors: list[list[float] | None] = [None] * len(items)
        text_indexes = [index for index, item in enumerate(items) if item.kind == "text"]
        if text_indexes:
            text_vectors = self._embed_texts(
                [str(items[index].value) for index in text_indexes], input_type
            )
            for index, vector in zip(text_indexes, text_vectors, strict=True):
                vectors[index] = vector
        for index, item in enumerate(items):
            if item.kind == "image":
                assert isinstance(item.value, Image.Image)
                vectors[index] = self._embed_image(item.value)
        if any(vector is None for vector in vectors):
            raise RuntimeError("model did not return all embeddings")
        return [vector for vector in vectors if vector is not None]


def _log(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, separators=(",", ":"), sort_keys=True), flush=True)


def _image_from_data_url(url: str) -> Image.Image:
    header, separator, encoded = url.partition(",")
    if not separator or not header.startswith("data:image/") or ";base64" not in header:
        raise ValueError("image_url must be a base64 image data URL")
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("image_url contains invalid base64") from error
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("image_url exceeds the configured size limit")
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("image_url is not a readable image") from error


def _parse_item(value: Any) -> RequestItem:
    if isinstance(value, str):
        return RequestItem("text", value)
    if isinstance(value, dict) and "content" in value:
        content = value["content"]
    elif isinstance(value, dict) and value.get("type") == "image_url":
        content = [value]
    else:
        raise ValueError("input items must be strings or image content blocks")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        raise ValueError("content must contain exactly one image block")
    block = content[0]
    if block.get("type") != "image_url":
        raise ValueError("only image_url content blocks are supported")
    image_url = block.get("image_url")
    if not isinstance(image_url, dict) or not isinstance(image_url.get("url"), str):
        raise ValueError("image_url.url is required")
    return RequestItem("image", _image_from_data_url(image_url["url"]))


def _parse_input(value: Any) -> list[RequestItem]:
    values = value if isinstance(value, list) else [value]
    if not values:
        raise ValueError("input must not be empty")
    return [_parse_item(item) for item in values]


class EmbeddingHandler(BaseHTTPRequestHandler):
    runtime: EmbeddingRuntime
    server_version = "EmbeddingGemmaOpenVINO/1.0"

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in {"/", "/health/live"}:
            self._respond(200, {"status": "ok"})
        elif self.path == "/health/ready":
            status = 200 if self.runtime.ready else 503
            self._respond(
                status,
                {
                    "status": "ready" if self.runtime.ready else "not_ready",
                    "dimension": 768,
                    "device": self.runtime.device,
                },
            )
        elif self.path == "/v1/models":
            self._respond(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": MODEL_NAME,
                            "object": "model",
                            "owned_by": "google",
                            "embedding_dimension": 768,
                        }
                    ],
                },
            )
        else:
            self._respond(404, {"detail": "not found"})

    def do_POST(self) -> None:
        if self.path != "/v1/embeddings":
            self._respond(404, {"detail": "not found"})
            return
        request_id = self.headers.get("x-request-id", "") or "unknown"
        started = time.monotonic()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_IMAGE_BYTES * 2:
                raise ValueError("request body is empty or exceeds the configured size limit")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or body.get("model") != MODEL_NAME:
                raise ValueError(f"model must be {MODEL_NAME}")
            items = _parse_input(body.get("input"))
            input_type = body.get("input_type", "document")
            queued_at = time.monotonic()
            with self.runtime.request_semaphore:
                inference_started = time.monotonic()
                vectors = self.runtime.encode(items, input_type)
            queue_wait_ms = round((inference_started - queued_at) * 1000, 2)
            inference_ms = round((time.monotonic() - inference_started) * 1000, 2)
            modality = "mixed" if len({item.kind for item in items}) > 1 else items[0].kind
            self._respond(
                200,
                {
                    "object": "list",
                    "model": MODEL_NAME,
                    "data": [
                        {"object": "embedding", "index": index, "embedding": vector}
                        for index, vector in enumerate(vectors)
                    ],
                    "usage": {"prompt_tokens": 0, "total_tokens": 0},
                },
            )
            _log(
                "request_complete",
                request_id=request_id,
                endpoint="/v1/embeddings",
                model=MODEL_NAME,
                modality=modality,
                batch_size=len(items),
                device=self.runtime.device,
                queue_wait_ms=queue_wait_ms,
                inference_ms=inference_ms,
                total_ms=round((time.monotonic() - started) * 1000, 2),
                http_status=200,
            )
        except ValueError as error:
            _log(
                "request_error",
                request_id=request_id,
                error_type=type(error).__name__,
                total_ms=round((time.monotonic() - started) * 1000, 2),
                http_status=400,
            )
            self._respond(
                400,
                {
                    "error": {"type": "invalid_request_error", "message": str(error)},
                    "request_id": request_id,
                },
            )
        except Exception as error:  # noqa: BLE001
            _log(
                "request_error",
                request_id=request_id,
                error_type=type(error).__name__,
                total_ms=round((time.monotonic() - started) * 1000, 2),
                http_status=500,
            )
            self._respond(
                500,
                {
                    "error": {"type": "server_error", "message": "embedding request failed"},
                    "request_id": request_id,
                },
            )

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> None:
    runtime = EmbeddingRuntime()
    handler = type("ConfiguredEmbeddingHandler", (EmbeddingHandler,), {"runtime": runtime})
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), handler)
    server.daemon_threads = True
    _log("http_server_ready", host="0.0.0.0", port=server.server_port, device=runtime.device)
    server.serve_forever()


if __name__ == "__main__":
    main()
