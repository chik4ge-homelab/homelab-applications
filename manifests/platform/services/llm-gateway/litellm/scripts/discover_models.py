import json
import os
import sys
import time
import urllib.error
import urllib.request


UPSTREAM_MODELS_URL = "http://llama-cpp-api.llm-gateway.svc.cluster.local:8000/v1/models"
UPSTREAM_API_BASE = "http://llama-cpp-api.llm-gateway.svc.cluster.local:8000/v1"
CONFIG_PATH = os.environ.get("LITELLM_CONFIG_PATH", "/runtime/config.yaml")
CONTEXT_WINDOW = 131072
MAX_OUTPUT_TOKENS = 8192
MAX_INPUT_TOKENS = CONTEXT_WINDOW - MAX_OUTPUT_TOKENS


def discover_models(api_key: str) -> list[str]:
    headers = {"Authorization": f"Bearer {api_key}"}
    request = urllib.request.Request(UPSTREAM_MODELS_URL, headers=headers)
    deadline = time.monotonic() + 600

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.load(response)
            model_ids = [
                item.get("id")
                for item in payload.get("data", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
            ]
            if model_ids:
                return list(dict.fromkeys(model_ids))
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(3)

    raise RuntimeError("upstream model discovery failed")


def build_config(model_ids: list[str]) -> dict:
    return {
        "model_list": [
            {
                "model_name": model_id,
                "litellm_params": {
                    "model": f"openai/{model_id}",
                    "api_base": UPSTREAM_API_BASE,
                    "api_key": "os.environ/LLAMA_CPP_API_KEY",
                },
                "model_info": {
                    "mode": "chat",
                    "max_input_tokens": MAX_INPUT_TOKENS,
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                },
            }
            for model_id in model_ids
        ],
        "general_settings": {
            "ui_access_mode": "admin_only",
            "store_model_in_db": True,
            "store_prompts_in_spend_logs": False,
            "maximum_spend_logs_retention_period": "30d",
            "maximum_spend_logs_retention_interval": "1d",
            "database_connection_pool_limit": 5,
        },
        "litellm_settings": {
            "callbacks": ["s3_v2"],
            "cold_storage_custom_logger": "s3_v2",
            "set_verbose": False,
            "drop_params": False,
            "s3_callback_params": {
                "s3_bucket_name": "llm-api-logs",
                "s3_region_name": "us-east-1",
                "s3_endpoint_url": "http://external-rgw-backend.rook-ceph.svc.cluster.local:7480",
                "s3_use_ssl": False,
                "s3_verify": False,
                "s3_use_virtual_hosted_style": False,
                "s3_path": "litellm-calls",
                "s3_strip_base64_files": False,
                "s3_aws_access_key_id": "os.environ/LITELLM_S3_ACCESS_KEY_ID",
                "s3_aws_secret_access_key": "os.environ/LITELLM_S3_SECRET_ACCESS_KEY",
            },
        },
    }


def main() -> None:
    api_key = os.environ.get("LLAMA_CPP_API_KEY")
    if not api_key:
        raise RuntimeError("upstream API key is unavailable")
    config = build_config(discover_models(api_key))
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, separators=(",", ":"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("failed to prepare LiteLLM configuration", file=sys.stderr)
        raise SystemExit(1)
