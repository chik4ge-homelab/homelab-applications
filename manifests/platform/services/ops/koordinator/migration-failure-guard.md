# Migration failure guard

The guard polls PodMigrationJobs every five seconds. A `Failed` job initiated by
`CustomPriority` blocks further descheduler evictions for its owning workload.
It follows ReplicaSet ownership to the Deployment and Job ownership to the
CronJob, checking owner UIDs before recording a failure.

The workload's metadata stores:

- `koordinator.chik4ge.me/migration-blocked=true` as a label.
- `koordinator.chik4ge.me/migration-failure` as an annotation containing the job,
  failure reason, message, and observation time.

The guard propagates the block label to existing and replacement Pods. It does
not modify the Pod template or trigger a rollout. Both `CustomPriority` and
`MigrationController` exclude labeled Pods. The block persists after failed jobs
expire, controller restarts, and workload rollouts. Node placement remains
unrestricted; GPU nodes can still host these workloads.

Deployment, StatefulSet, DaemonSet, ReplicaSet, Job, and CronJob owners are
supported. The guard does not block ownerless Pods or workloads recreated with
a different UID. An already started migration is not cancelled. Label propagation
can take one polling interval; Kubernetes node eviction is outside this policy.

After addressing a failure, wait for its failed MigrationJobs to expire, then
remove the block label and failure annotation from the owning workload to allow
descheduler migrations again. While the original failed job is retained, removing
the label causes the guard to record that failure again.

Run the owner-resolution and persistence tests with:

```sh
python3 -B manifests/platform/services/ops/koordinator/migration-failure-guard-test.py
```
