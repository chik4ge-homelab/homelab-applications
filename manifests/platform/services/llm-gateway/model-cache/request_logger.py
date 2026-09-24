#!/usr/bin/env python3
"""Log complete HTTP request and response bodies while proxying llama-server."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


UPSTREAM_HOST = os.environ.get("UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("UPSTREAM_PORT", "8000"))
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8001"))
READ_SIZE = 64 * 1024
INLINE_BODY_LIMIT = 24 * 1024
LOG_CHUNK_SIZE = 48 * 1024
LOG_LOCK = threading.Lock()

HOP_BY_HOP_HEADERS = {
    "connection",
    "expect",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def emit_record(record: dict[str, object]) -> None:
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with LOG_LOCK:
        print(line, flush=True)


def emit_body(
    *,
    request_id: str,
    direction: str,
    method: str,
    path: str,
    body_file,
    body_bytes: int,
    content_type: str | None,
    status: int | None = None,
    complete: bool = True,
) -> None:
    body_file.seek(0)
    digest = hashlib.sha256()
    while chunk := body_file.read(READ_SIZE):
        digest.update(chunk)
    body_sha256 = digest.hexdigest()
    body_file.seek(0)
    common: dict[str, object] = {
        "timestamp": utc_now(),
        "event": "llm_gateway_http_body",
        "request_id": request_id,
        "direction": direction,
        "method": method,
        "path": path,
        "status": status,
        "content_type": content_type,
        "body_bytes": body_bytes,
        "body_sha256": body_sha256,
        "complete": complete,
    }

    if body_bytes <= INLINE_BODY_LIMIT:
        body = body_file.read()
        try:
            common["body_encoding"] = "utf-8"
            common["body"] = body.decode("utf-8")
        except UnicodeDecodeError:
            common["body_encoding"] = "base64"
            common["body"] = base64.b64encode(body).decode("ascii")
        emit_record(common)
        body_file.seek(0, os.SEEK_END)
        return

    parts = (body_bytes + LOG_CHUNK_SIZE - 1) // LOG_CHUNK_SIZE
    common["body_encoding"] = "base64"
    for part in range(parts):
        chunk = body_file.read(LOG_CHUNK_SIZE)
        record = {
            **common,
            "body_part": part,
            "body_parts": parts,
            "body": base64.b64encode(chunk).decode("ascii"),
        }
        emit_record(record)
    body_file.seek(0, os.SEEK_END)


def read_exactly(source, destination, count: int) -> int:
    remaining = count
    total = 0
    while remaining:
        chunk = source.read(min(READ_SIZE, remaining))
        if not chunk:
            raise ValueError("request body ended before Content-Length")
        destination.write(chunk)
        remaining -= len(chunk)
        total += len(chunk)
    return total


def read_chunked_body(source, destination) -> int:
    total = 0
    while True:
        line = source.readline(65537)
        if not line or len(line) > 65536:
            raise ValueError("invalid chunked request body")
        try:
            chunk_size = int(line.split(b";", 1)[0].strip(), 16)
        except ValueError as exc:
            raise ValueError("invalid chunk size") from exc
        if chunk_size == 0:
            while source.readline(65537) not in (b"\r\n", b"\n", b""):
                pass
            return total
        total += read_exactly(source, destination, chunk_size)
        if source.read(2) != b"\r\n":
            raise ValueError("invalid chunk terminator")


def connection_header_tokens(headers) -> set[str]:
    tokens: set[str] = set()
    for value in headers.get_all("Connection", []):
        tokens.update(token.strip().lower() for token in value.split(","))
    return tokens


class RequestLoggerProxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "llm-gateway-request-logger"
    sys_version = ""

    def handle_expect_100(self) -> bool:
        self.send_response_only(100)
        self.end_headers()
        return True

    def do_GET(self):
        self.proxy_request()

    def do_HEAD(self):
        self.proxy_request()

    def do_POST(self):
        self.proxy_request()

    def do_PUT(self):
        self.proxy_request()

    def do_PATCH(self):
        self.proxy_request()

    def do_DELETE(self):
        self.proxy_request()

    def do_OPTIONS(self):
        self.proxy_request()

    def do_TRACE(self):
        self.proxy_request()

    def proxy_request(self) -> None:
        request_id = str(uuid.uuid4())
        method = self.command
        path = self.path
        request_body = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        response_body = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        request_bytes = 0
        response_bytes = 0
        response_status: int | None = None
        response_type: str | None = None
        response_complete = True
        upstream: http.client.HTTPConnection | None = None

        try:
            transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
            if any(token.strip() == "chunked" for token in transfer_encoding.split(",")):
                request_bytes = read_chunked_body(self.rfile, request_body)
            else:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 0:
                    raise ValueError("negative Content-Length")
                request_bytes = read_exactly(self.rfile, request_body, content_length)
        except (ValueError, OSError) as exc:
            request_body.close()
            response_body.close()
            self.send_error(400, str(exc))
            return

        emit_body(
            request_id=request_id,
            direction="request",
            method=method,
            path=path,
            body_file=request_body,
            body_bytes=request_bytes,
            content_type=self.headers.get("Content-Type"),
        )

        try:
            upstream = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT)
            outbound_headers: dict[str, str] = {}
            connection_tokens = connection_header_tokens(self.headers)
            for name, value in self.headers.items():
                lower_name = name.lower()
                if lower_name in HOP_BY_HOP_HEADERS or lower_name in connection_tokens:
                    continue
                if lower_name in {"host", "content-length"}:
                    continue
                outbound_headers[name] = value
            outbound_headers["Host"] = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
            outbound_headers["Content-Length"] = str(request_bytes)

            request_body.seek(0)
            upstream.request(
                method,
                path,
                body=request_body if request_bytes else None,
                headers=outbound_headers,
            )
            upstream_response = upstream.getresponse()
            response_status = upstream_response.status
            response_type = upstream_response.getheader("Content-Type")

            transfer_encoding = upstream_response.getheader("Transfer-Encoding", "")
            upstream_chunked = any(
                token.strip().lower() == "chunked" for token in transfer_encoding.split(",")
            )
            content_length = upstream_response.getheader("Content-Length")
            no_response_body = method == "HEAD" or response_status in (204, 304)
            downstream_chunked = upstream_chunked or (content_length is None and not no_response_body)
            response_tokens = connection_header_tokens(upstream_response.headers)

            self.send_response_only(response_status, upstream_response.reason)
            for name, value in upstream_response.getheaders():
                lower_name = name.lower()
                if lower_name in HOP_BY_HOP_HEADERS or lower_name in response_tokens:
                    continue
                if downstream_chunked and lower_name == "content-length":
                    continue
                self.send_header(name, value)
            if downstream_chunked:
                self.send_header("Transfer-Encoding", "chunked")
            if "connection" in connection_tokens or self.request_version == "HTTP/1.0":
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()

            if not no_response_body:
                try:
                    while chunk := upstream_response.read(READ_SIZE):
                        response_body.write(chunk)
                        response_bytes += len(chunk)
                        try:
                            if downstream_chunked:
                                self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                                self.wfile.write(chunk)
                                self.wfile.write(b"\r\n")
                            else:
                                self.wfile.write(chunk)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            self.close_connection = True
                    if downstream_chunked:
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                except (http.client.IncompleteRead, OSError) as exc:
                    response_complete = False
                    emit_record(
                        {
                            "timestamp": utc_now(),
                            "event": "llm_gateway_http_proxy_error",
                            "request_id": request_id,
                            "direction": "response",
                            "method": method,
                            "path": path,
                            "error": type(exc).__name__,
                        }
                    )

            emit_body(
                request_id=request_id,
                direction="response",
                method=method,
                path=path,
                body_file=response_body,
                body_bytes=response_bytes,
                content_type=response_type,
                status=response_status,
                complete=response_complete,
            )
        except (http.client.HTTPException, OSError, ValueError) as exc:
            response_status = 502
            response_type = "application/json"
            body = json.dumps({"error": "upstream request failed"}).encode("utf-8")
            response_body.write(body)
            response_bytes = len(body)
            try:
                self.send_response(502)
                self.send_header("Content-Type", response_type)
                self.send_header("Content-Length", str(response_bytes))
                self.send_connection_close()
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass
            emit_record(
                {
                    "timestamp": utc_now(),
                    "event": "llm_gateway_http_proxy_error",
                    "request_id": request_id,
                    "direction": "upstream",
                    "method": method,
                    "path": path,
                    "error": type(exc).__name__,
                }
            )
            emit_body(
                request_id=request_id,
                direction="response",
                method=method,
                path=path,
                body_file=response_body,
                body_bytes=response_bytes,
                content_type=response_type,
                status=response_status,
            )
        finally:
            if upstream is not None:
                upstream.close()
            request_body.close()
            response_body.close()

    def send_connection_close(self) -> None:
        self.send_header("Connection", "close")
        self.close_connection = True

    def log_message(self, fmt: str, *args) -> None:
        # Keep access logs out of stdout; request/response records are emitted above.
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), RequestLoggerProxy)
    server.daemon_threads = True
    server.serve_forever()
