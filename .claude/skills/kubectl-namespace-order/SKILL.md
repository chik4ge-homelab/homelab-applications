# Skill: kubectl namespace flag order

## Purpose

Enforce consistent `kubectl` namespace flag placement to reduce mistakes and improve readability.

## Rule

- Do not use `kubectl -n <namespace> <subcommand> ...`.
- Always place the namespace flag immediately after the subcommand: `kubectl <subcommand> -n <namespace> ...`.

## Examples

✅ `kubectl get -n kube-system pods`

❌ `kubectl -n kube-system get pods`

## Checklist

- Confirm the namespace flag appears after the subcommand.
- Update any suggested commands to follow the rule before sharing.
