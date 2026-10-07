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
- Backend runtime: Python 3.12, Sentence Transformers 6.1.0, PyTorch 2.14.0 CPU,
  torchvision 0.29.1 CPU, Transformers 5.19.0
- Stock vLLM check: current vLLM nightly contains `EmbeddingGemma2Model`, but
  the official x86_64 CPU image requires AVX2/AVX512; all cluster worker nodes
  expose only x86-64-v2-era flags, so it exits with SIGILL before serving.
  The custom backend remains selected for this cluster.
- Adapter image:
  `ghcr.io/chik4ge-homelab/immich-ml-adapter@sha256:57560b6d479bd6dceaae9b12bd9a92960e111fdb859551d56eea0151ae86a8f5`
- Backend image:
  `ghcr.io/chik4ge-homelab/embeddinggemma-api@sha256:d9e0e6e46f7c24d122e7b913fe6ba020af50cfc3198fc9ee50b72d260e88b1c2`

The serving Deployment runs two replicas with required pod anti-affinity so
they land on different worker nodes. Because the available `ceph-rbd` class is
`ReadWriteOnce`, each replica uses an 8 GiB per-pod `emptyDir` cache. The cache
init container downloads the exact model files from the pinned Hugging Face
commit on each pod start; the current model occupies about 1.5 GiB. The former
8 GiB PVC remains declared for rollback and is not deleted by this change. The
serving containers use CPU FP32, `HF_HUB_OFFLINE=1`, two Torch/BLAS threads,
and up to two requests in flight per replica. Audio is disabled in the
Sentence Transformers configuration; only text and image inputs are accepted.

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

The Kubernetes latency and resource benchmark is intentionally run after the
model is warmed in the cluster. Record cold model-load time, warm text/image
p50 and p95, sequential throughput, pod RSS, CPU, and the results with Torch
thread counts 1, 2, and 4. Keep the lowest stable setting that does not
starve the other `llm-gateway` services.

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

Rollback is a Git change delivered by ArgoCD:

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

At the time this runbook was written, the remaining external prerequisite was
1Password CLI authentication for creating the dedicated item. No cluster
apply or Smart Search reindex is performed by this change until that secret
is available and the positive/negative connectivity tests pass.
