---
name: kubectl-namespace-order
description: Enforce consistent kubectl namespace flag placement to reduce mistakes.
---

## What I do
- Ensure `kubectl` commands use `kubectl <subcommand> -n <namespace> ...`

## When to use me
Use this when suggesting or reviewing `kubectl` commands so the namespace flag
appears immediately after the subcommand.

## Examples

✅ `kubectl get -n kube-system pods`

❌ `kubectl -n kube-system get pods`
