# Immich EmbeddingGemma 2 deployment

This change adds a protocol adapter in `immich` without forking Immich or
changing its database schema. Immich Server v3.2.0 continues to call
`/predict` on port 3003, while only the `clip.textual` and `clip.visual`
entries named `ViT-B-16-SigLIP2__webli` are routed to EmbeddingGemma 2.

```text
immich-server
  -> immich-ml-adapter:3003
       -> immich-machine-learning:3003       # every non-alias task
       -> litellm-proxy:4000                 # only the EmbeddingGemma alias
            -> embeddinggemma-api:8080
```

The adapter has only the two configured upstreams shown above. It does not
contain, receive, or connect to the backend URL. The backend is reachable only
from LiteLLM by Cilium policy and has no external HTTPRoute.

## Versions and immutable inputs

- Immich Server and stock ML: `v3.2.0`
- LiteLLM: the deployed 1.102.1-era image/configuration
- EmbeddingGemma model: `google/embeddinggemma-2`
- Model commit: `914f7f89142e33e77833254d9c9b90c3cef7303b`
- Embedding dimension: 768
- API wrapper image:
  `ghcr.io/chik4ge-homelab/embeddinggemma-api@sha256:351c88bc401242ce6064d360034bd9c242c21bf4984ce16b3681a1f2571f305e`
- Inference image: `ghcr.io/ggml-org/llama.cpp@sha256:4b554057c7cf0cb87e0724b6de9d7ca9bb24a3b72716af11e4c0fecad62c506b`
  (CUDA server, revision `372dc5ca157276beb6c67b2e92477e8d662e964a`)
- EmbeddingGemma runs BF16 GGUF with all model layers and the multimodal
  projector on CUDA. The active pod is pinned to the RTX 3060 Ti on
  `k8s-w-edelweiss`; the former two-replica SYCL deployment is scaled to zero.

The CUDA deployment uses `--flash-attn on`, batch and micro-batch sizes of 512,
two server slots, and two CPU threads. The 512-token micro-batch caps the
effective image budget at 256 tokens. Its 8 GiB `emptyDir` cache downloads the
exact model files from the pinned Hugging Face commit at pod startup; the model
and projector occupy about 1.5 GiB. The inactive SYCL deployment and its PVC
remain available for GitOps rollback. The API accepts text and image inputs.

## LiteLLM contract and secret

LiteLLM registers `embeddinggemma-2` for native `/v1/embeddings` text calls
and adds the authenticated pass-through route
`POST /v1/embeddinggemma/embeddings` to the backend's `/v1/embeddings`.
The adapter uses the pass-through route for both text and image requests so
the multimodal request body is preserved. The adapter key is provisioned by
the LiteLLM PostSync bootstrap job with exactly:

```text
allowed_routes: /v1/embeddinggemma/embeddings
metadata.allowed_passthrough_routes: /v1/embeddinggemma/embeddings
```

The same generated value is read by both ExternalSecrets from the 1Password
item `Immich ML Adapter LiteLLM API Key`, property `password`. It must be
created before the Argo sync that enables the adapter. The master key is not
used by the adapter.

## Activation and validation

Do not start a full Smart Search reindex at activation. First verify:

1. `embeddinggemma-api` has a ready pod and the model-cache init container has
   completed.
2. LiteLLM's native text endpoint and authenticated pass-through endpoint both
   return one finite, normalized 768-dimensional vector.
3. `immich-ml-adapter /ping` returns plain-text `pong` and `/health/upstreams`
   reports both upstreams healthy.
4. A textual alias request sends `input_type=query`; a visual alias request
   sends an image data URL with `input_type=document` and returns Immich's
   stringified `clip` array plus `imageHeight`/`imageWidth`.
5. OCR, facial recognition, and a non-alias CLIP request still reach the stock
   ML service. Test these with a small known asset only.
6. Compare a small sample of old/new search results and run Japanese semantic
   sanity checks before selecting the alias in Immich Smart Search settings.

The local implementation checks completed before deployment were:

- adapter: 6 pytest tests and Ruff passed
- backend: 4 pytest tests and Ruff passed
- GitHub Actions lint/test and container publish passed for both images
- Kustomize server dry-run passed for `llm-gateway` and `immich`
- real local backend inference passed for Japanese text and image input:
  768 finite values with L2 norm approximately 1.0

An in-cluster runtime comparison used a 980×980 JPEG: the CUDA backend returned
an image embedding in 0.156 s versus 2.471 s on the Blossom iGPU. The two
768-dimensional image vectors had cosine similarity 0.9969; text-image cosine
was 0.664 on CUDA and 0.673 on the iGPU. Six distinct synthetic images also
showed about 0.11 s warm latency on CUDA. This was a smoke benchmark, not a
full workload latency distribution.

## Network and security checks

- Adapter ingress: only the Immich server endpoint (plus host probes).
- Adapter egress: DNS, stock ML `:3003`, and LiteLLM `:4000` only.
- LiteLLM ingress: the adapter on `:4000`; egress: backend `:8080` in addition
  to its existing upstreams.
- Backend ingress: LiteLLM on `:8080` (plus host probes); no Immich endpoint.
- Backend cache initialization allows only the Hugging Face FQDNs needed to
  download the pinned files; runtime is offline.
- Both new containers run non-root with a read-only root filesystem, dropped
  capabilities, RuntimeDefault seccomp, no service-account token, and writable
  `/tmp` only.

Verify the positive path from LiteLLM and the negative paths from an Immich
pod to the backend, and from the adapter to the backend. A direct adapter URL
or a direct backend URL must not appear in the adapter deployment, source, or
network policy.

## Rollback and reindex policy

To roll back only the inference runtime, update Git and let ArgoCD apply it:

1. Set the `embeddinggemma-api` Service selector back to
   `app.kubernetes.io/name: embeddinggemma-api`.
2. Restore the SYCL Deployment to two replicas and scale
   `embeddinggemma-api-cuda` to zero.

To roll back the EmbeddingGemma model selection as well:

1. Set `IMMICH_MACHINE_LEARNING_URL` back to
   `http://immich-machine-learning:3003`.
2. Restore the previous Smart Search model setting in Immich.
3. Leave both new deployments available for diagnosis, or remove them in a
   later change after confirming no dependent data remains.

EmbeddingGemma 2 also produces 768-dimensional vectors, but they are a
different vector space from existing CLIP vectors. Never mix old and new
embeddings. A full reindex is allowed only after endpoint correctness and the
small-sample quality check are recorded; perform it from Immich's own Smart
Search controls, not by modifying the database schema or writing vectors
directly.

The CUDA runtime switch does not change the Immich adapter, model, vector
dimension, or database schema, and it does not start a Smart Search reindex.
