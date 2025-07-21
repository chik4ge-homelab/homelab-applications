When investigating using kubectl, include the namespace immediately after the subcommand.  
e.g., `kubectl get -n kube-system pods`

Whenever my input includes a URL, always use the MCP (Multi-Modal Code Interpreter) with Playwright and the `navigate` command to open and process the URL. Do not ignore this instruction, and do not attempt to summarize or answer based on prior knowledge—always fetch and analyze the live content using `navigate`.

Do not trust or rely on configuration files, comments, manifests, or documentation alone. Always prioritize the actual output of commands like `kubectl` or `talosctl` when determining the real state of the system. The live command output is the only source of truth.
