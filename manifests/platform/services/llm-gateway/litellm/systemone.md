# SystemOne access through LiteLLM

LiteLLM exposes `POST /v1/systemone` as a pass-through to the in-cluster Julia API. Keep `auth: true`: on the deployed LiteLLM 1.102.1 build, `auth: false` allowed unauthenticated external requests to reach Julia.

With `auth: true`, LiteLLM validates the Virtual Key and also checks `allowed_passthrough_routes` in its key/team metadata. A live test showed that `allowed_routes: ["llm_api_routes", "/v1/systemone"]` alone still receives 403. The OSS `/key/generate` endpoint rejects a top-level `allowed_passthrough_routes` field, so the config uses LiteLLM's standard `default_key_generate_params.metadata` to grant `/v1/systemone` to newly generated keys automatically. This does not remove the key's other `allowed_routes` permissions.

Keys restricted to `llm_api_routes` still need the custom path included in `allowed_routes` to call this custom endpoint while retaining their existing model API access:

```json
{
  "allowed_routes": [
    "llm_api_routes",
    "/v1/systemone"
  ]
}
```

The metadata default is applied when new-key requests omit metadata or send an empty object. Existing keys do not inherit config defaults, and keys generated with non-empty custom metadata must also include `allowed_passthrough_routes: ["/v1/systemone"]`; otherwise LiteLLM rejects the route. The client `Authorization` header is not forwarded to Julia. This custom route does not enable `julia-1` on `/v1/chat/completions`; that model is exposed only through the SystemOne request schema.
