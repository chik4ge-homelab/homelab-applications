# Operational Rules

- Always review existing functionality and current logs/state before making changes.
- Act autonomously to discover deployment methods and relevant configuration before asking the user.
- If a change is destructive or risky, explain the impact scope before proceeding.
- GitOps is the default delivery path, even when the user requests direct application. Use Helm/Kustomize sources and let ArgoCD apply changes.
- When testing with `kubectl apply`, first render or export the manifest to a local YAML file, then apply that file.
- Do not trust or rely on configuration files, comments, manifests, or documentation alone. Always prioritize live command output when determining the real state of the system.
- During investigations, compare the committed configuration to the live cluster state and report any divergence to the user.
- After any apply, confirm the system reaches the intended state using live command output.
- You may proceed with commit/push without prior confirmation, but after completing work request user review before running `git commit` and `git push`.
- Confirmation prompts are handled by the system; proceed with commands as needed without extra user checks unless explicitly instructed otherwise.
- Before running `git push`, explain what was changed.

# Helm and Kustomize

- For Helm charts or Kustomize updates, confirm the values schema and related documentation before editing.

# Agent Skills

- Use the `kubectl-namespace-order` skill whenever using `kubectl`.
