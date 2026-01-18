---
name: argocd-refresh-after-push
description: Refresh ArgoCD applications after git push in app-of-apps setup.
---

## What I do
- After `git push`, refresh the app-of-apps application first using `argocd app sync apps --grpc-web`.
  - Run this when the git push includes changes to Application YAMLs.
- Then refresh each changed application using `argocd app sync <changed-application> --grpc-web`.
- Confirm each refreshed application's last commit matches `git status`.
- Verify the intended changes are applied.

## When to use me
Use this after pushing GitOps changes when ArgoCD is managed by an app-of-apps.
