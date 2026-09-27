# SystemOne access through LiteLLM

LiteLLM exposes `POST /v1/systemone` as a pass-through to the in-cluster Julia API. On the deployed LiteLLM 1.102.1 build, `auth: true` also activates a separate `allowed_passthrough_routes` check, which is not available to OSS virtual-key management. This endpoint uses `auth: false` to avoid that Enterprise-only route check.

The pass-through handler in the deployed build still calls LiteLLM's `user_api_key_auth` dependency, so requests without a valid Virtual Key and requests with an invalid key are rejected by LiteLLM. Its client `Authorization` header is not forwarded to Julia.

Keys restricted to the `llm_api_routes` group must also include `/v1/systemone` in `allowed_routes` to call this custom endpoint while retaining their existing LLM API access:

```json
{
  "allowed_routes": [
    "llm_api_routes",
    "/v1/systemone"
  ]
}
```

This custom route does not enable `julia-1` on `/v1/chat/completions`; that model is exposed only through the SystemOne request schema.
