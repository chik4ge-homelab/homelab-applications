---
name: talosctl-node-flag-order
description: Enforce consistent talosctl node flag placement to reduce mistakes.
---

## What I do
- Ensure `talosctl` commands use `talosctl <subcommand> -n <node> ...`

## When to use me
Use this when suggesting or reviewing `talosctl` commands so the node flag
appears immediately after the subcommand.

## Examples

✅ `talosctl get -n 10.0.0.11 machineconfig`

❌ `talosctl -n 10.0.0.11 get machineconfig`
