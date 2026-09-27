# SystemOne access through LiteLLM

LiteLLM exposes `POST /v1/systemone` as a pass-through to the in-cluster Julia API. Keep `auth: true`: on the deployed LiteLLM 1.102.1 build, `auth: false` allowed unauthenticated external requests to reach Julia.

With `auth: true`, LiteLLM validates the Virtual Key and also requires a matching `allowed_passthrough_routes` value on the key or team. A live test showed that `allowed_routes: ["llm_api_routes", "/v1/systemone"]` alone still receives 403. The top-level `allowed_passthrough_routes` key-generation field is rejected as Enterprise-only by this OSS deployment, so regular Virtual Keys cannot currently use the pass-through through that field.

Even when the independent pass-through access issue is resolved, a key restricted to `llm_api_routes` would need the custom route added to `allowed_routes` to preserve its existing model API access:

```json
{
  "allowed_routes": [
    "llm_api_routes",
    "/v1/systemone"
  ]
}
```

The client `Authorization` header is not forwarded to Julia. This custom route does not enable `julia-1` on `/v1/chat/completions`; that model is exposed only through the SystemOne request schema.
