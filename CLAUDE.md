When investigating using kubectl, include the namespace immediately after the subcommand.  
e.g., `kubectl get -n kube-system pods`

Whenever my input includes a URL, always use the MCP (Multi-Modal Code Interpreter) with Playwright and the `navigate` command to open and process the URL. Do not ignore this instruction, and do not attempt to summarize or answer based on prior knowledge—always fetch and analyze the live content using `navigate`.

Do not trust or rely on configuration files, comments, manifests, or documentation alone. Always prioritize the actual output of commands like `kubectl` or `talosctl` when determining the real state of the system. The live command output is the only source of truth.

## Investigation and Fix Workflow

When investigating and fixing system issues, follow this workflow:

1. **Investigation Phase**
   - Use TodoWrite to plan investigation tasks systematically
   - Check actual system state with live commands (kubectl, logs, resource usage)
   - Analyze root causes carefully - avoid incorrect assumptions about relationships (e.g., CPU limits ≠ OOMKilled)
   - Focus on finding the source Helm configuration files, not temporary manifests

2. **Fix Phase**  
   - When asked to modify resources, **edit the Helm source files** in `templates/` directory
   - Do NOT edit temporary kubectl exports or use kubectl patch for permanent changes
   - Always commit and push changes to git after editing Helm templates
   - Let ArgoCD handle the deployment through GitOps workflow

This ensures changes are persistent, version-controlled, and follow proper GitOps practices.
