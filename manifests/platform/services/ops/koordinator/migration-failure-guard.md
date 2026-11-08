# Migration failure guard

The guard polls PodMigrationJobs every ten seconds. A `Failed` job initiated by
`CustomPriority` puts its owning workload into a temporary retry cooldown. The
first cooldown is two minutes; consecutive failures double it up to 30 minutes,
and the count resets after 24 hours without a failure. The guard follows
ReplicaSet ownership to the Deployment and Job ownership to the CronJob,
checking owner UIDs before recording a failure.

The workload's metadata stores:

- `koordinator.chik4ge.me/migration-blocked=true` as a label.
- `koordinator.chik4ge.me/migration-failure` as an annotation containing the job,
  failure reason, message, observation time, retry deadline, and failure count.

The guard propagates the block label to existing and replacement Pods while the
cooldown is active. It does not modify the Pod template or trigger a rollout.
Both `CustomPriority` and `MigrationController` exclude labeled Pods. When the
cooldown expires, it removes the block and permits a new attempt. GPU nodes can
still host these workloads.

Deployment, StatefulSet, DaemonSet, ReplicaSet, Job, and CronJob owners are
supported. The guard does not block ownerless Pods or workloads recreated with
a different UID. An already started migration is not cancelled. Label propagation
can take one polling interval; Kubernetes node eviction is outside this policy.

The failure annotation remains after the cooldown as an audit record and to avoid
reapplying the same failed job. A later failed job replaces it with the next
backoff interval.
