import hashlib
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "http://litellm-proxy.llm-gateway.svc.cluster.local:4000"
VIEWER_USER_ID = "proxy-admin-viewer"
UI_SETTINGS = {
    "enabled_ui_pages_internal_users": ["logs", "usage", "models"],
    "disable_agents_for_internal_users": True,
    "disable_vector_stores_for_internal_users": True,
    "disable_model_add_for_internal_users": True,
    "enable_chat_ui": False,
}


def request(method: str, path: str, payload: dict | None = None, *, auth: bool = True):
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {os.environ['LITELLM_MASTER_KEY']}"
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode()
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        error.read()
        return error.code, {}


def wait_for_ready() -> None:
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        status, _ = request("GET", "/health/readiness", auth=False)
        if status == 200:
            return
        time.sleep(3)
    raise RuntimeError("LiteLLM did not become ready")


def key_info(api_key: str):
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    query = urllib.parse.urlencode({"key": key_hash})
    return request("GET", f"/key/info?{query}")


def ensure_agent_key() -> None:
    api_key = os.environ["LITELLM_AGENT_API_KEY"]
    status, _ = key_info(api_key)
    if status == 200:
        return
    if status != 404:
        raise RuntimeError("could not check Hermes API key")
    status, _ = request(
        "POST",
        "/key/generate",
        {
            "key": api_key,
            "key_alias": "hermes-agent",
            "allowed_routes": ["/v1/chat/completions", "/v1/models"],
        },
    )
    if status not in (200, 201):
        raise RuntimeError("could not create Hermes API key")


def ensure_viewer_user() -> None:
    query = urllib.parse.urlencode({"user_id": VIEWER_USER_ID})
    status, existing = request("GET", f"/user/info?{query}")
    if status == 404:
        status, _ = request(
            "POST",
            "/user/new",
            {
                "user_id": VIEWER_USER_ID,
                "user_email": os.environ["PROXY_VIEWER_EMAIL"],
                "user_alias": "proxy_admin_viewer",
                "user_role": "proxy_admin_viewer",
                "auto_create_key": False,
            },
        )
        if status not in (200, 201):
            raise RuntimeError("could not create read-only UI user")
    elif status == 200:
        user = existing.get("user_info", existing)
        if user.get("user_role") != "proxy_admin_viewer":
            raise RuntimeError("existing UI user does not have proxy_admin_viewer role")
    else:
        raise RuntimeError("could not check read-only UI user")

    password = os.environ["PROXY_VIEWER_PASSWORD"]
    if not password:
        raise RuntimeError("read-only UI user password is missing")
    status, _ = request(
        "POST",
        "/user/update",
        {"user_id": VIEWER_USER_ID, "password": password},
    )
    if status not in (200, 201):
        raise RuntimeError("could not set read-only UI user password")

    api_key = os.environ["PROXY_VIEWER_API_KEY"]
    status, _ = key_info(api_key)
    if status == 200:
        return
    if status != 404:
        raise RuntimeError("could not check read-only UI key")
    status, _ = request(
        "POST",
        "/key/generate",
        {
            "key": api_key,
            "key_alias": "proxy_admin_viewer-ui",
            "user_id": VIEWER_USER_ID,
        },
    )
    if status not in (200, 201):
        raise RuntimeError("could not create read-only UI key")


def ensure_ui_settings() -> None:
    status, response = request("GET", "/get/ui_settings")
    if status != 200:
        raise RuntimeError("could not read LiteLLM UI settings")
    values = response.get("values", {})
    if all(values.get(key) == value for key, value in UI_SETTINGS.items()):
        return
    status, _ = request("PATCH", "/update/ui_settings", UI_SETTINGS)
    if status not in (200, 201):
        raise RuntimeError("could not set LiteLLM UI settings")


def main() -> None:
    wait_for_ready()
    ensure_agent_key()
    ensure_viewer_user()
    ensure_ui_settings()
    print("LiteLLM bootstrap complete")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"LiteLLM bootstrap failed: {error}", file=sys.stderr)
        raise SystemExit(1)
