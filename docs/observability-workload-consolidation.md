# Observability workload consolidation (2026-10-12)

The cluster has no additional RAM available. Reduce duplicated service processes
while retaining Grafana replicas, Redis/Sentinel quorum and Kafka replication.

| Change | Reserved memory removed |
| --- | ---: |
| Grafana PXC and HAProxy -> shared CNPG PostgreSQL | 3,402,653,184 bytes |
| ArgoCD Redis HA -> shared Redis HA, logical DB 1 | 603,979,776 bytes |
| Kafka 3 brokers + 3 controllers -> 3 dual-role nodes, 1536Mi each | 1,610,612,736 bytes |
| Mimir index/metadata/results caches, 9 -> 3 shared Memcached pods | 402,653,184 bytes |

These are Kubernetes requests, not a measurement of physical RAM released.
The resulting node-failure reservation margin is small; evaluate the live alert
expression when workload requests change. The largest allocatable node is
subtracted by `KubeMemoryOvercommit` in this HA cluster.

## Grafana data migration

Provision `grafana` owned by `grafana_app` in `cloudnative-pg-cluster` first.
The password uses the existing ExternalSecret item through a dedicated CNPG
Secret. Initialize the PostgreSQL schema with Grafana **12.3.1**, the same version
as the MySQL source, with initial admin creation disabled.

`tools/grafana-postgres-migration.py` reads Kubernetes Secrets internally. Run it
with `uv run --with pymysql --with 'psycopg[binary]' python`. Forward the source
MySQL to localhost:15336 and PostgreSQL to localhost:15436. Its `export`, `import`
and `verify` modes take a private backup path. Import replaces application data
in the target database. It retains PostgreSQL's dialect-specific `migration_log`,
resets sequences and verifies all application rows. CHAR padding is normalized
for comparison, since MySQL strips it on reads.

Pause Grafana writes with a GitOps replicas=0 change; export the final source,
import and verify, then change the database connection and restore replicas=2.
Do not retire PXC before checking the live health response, authenticated UI and
monitoring queries. The final export contained 84 tables and 5,471 rows including
migration history. It preserved 76 dashboards, 3 users, 3 data sources and 2 alert
rules. The source application rows were all compared against PostgreSQL.

Private artifacts are outside Git in
`/home/chikage/.local/state/homelab-db-consolidation/2026-10-12/` (directory 0700,
files 0600): final MySQL JSON, PXC credentials and a PostgreSQL custom-format dump.
Grafana's three original PXC PVCs are retained. Restore a database into an isolated
target and validate it before redirecting Grafana. Reverting the source alone
would discard edits made since the cutover; export the current PostgreSQL first.

## ArgoCD Redis

Provision `external-redis-credentials` before changing ArgoCD. The chart requires
key `redis-password`. Use `redis-ha-haproxy.redis.svc.cluster.local:6379`, logical
DB 1; existing consumers use DB 0. Disable embedded Redis HA, standalone Redis
and redis-secret-init. Check the rendered manifests for duplicate `redis` YAML
keys and confirm controller, repo-server and server all read `redis.db=1`.

The cache is regenerated. Existing ArgoCD login sessions can be invalidated;
refresh CLI authentication with the existing SSO flow. Validate application
reconciliation and Redis DB 1 writes after all components roll out.

## Kafka

Keep controller IDs **3, 4, 5** and add the broker role to that existing pool.
Expand its volumes from 5Gi to 30Gi before moving data. Use an explicit 1Gi JVM
heap, requests 1536Mi and limit 3Gi. Preserve replication factor 3 and min ISR 2.

Save all assignments, including `__consumer_offsets`. Reassign all 51 partitions
from IDs 0/1/2 to 3/4/5 with `kafka-reassign-partitions.sh`, capped at 20MiB/s.
Use `--verify` to confirm completion and clear throttles. Check every partition's
leader, replicas and ISR before retiring the broker-only pool. Confirm quorum
voters still use IDs 3/4/5, live consumer offsets advance and Mimir data is fresh.

**Retention caveat:** the old broker PVCs were deleted with their KafkaNodePool,
despite the intended `deleteClaim: false` setting. The new pool's PVCs have
`strimzi.io/delete-claim=false` but still have KafkaNodePool owner references.
Do not assume the flag protects PVCs when deleting their owner. For a future
retirement that needs old PVCs retained, scale the evacuated pool to zero and
keep the owner until the actual retention behavior has been verified. The old
PVCs cannot be used to roll back this migration. All partitions were synchronized
on the three new nodes before deletion; assignments are backed up privately.

## Mimir caches

Keep the existing `mimir-index-cache` service and three replicas, shared by index,
metadata, ruler storage and frontend results caching. Set each Memcached capacity
to 128MiB, request 64Mi and limit 256Mi. Keep the separate large chunks cache.

Keys in Mimir 3.0.4 are separated by format: index keys use prefixes such as `P2:`
and `S:`, bucket metadata uses operation/bucket prefixes, and frontend results
use the versioned `1@` namespace. Inspect upstream code when changing versions.
Sharing adds competition for capacity; monitor cache evictions and query latency.

Disable chart metadata/results cache workloads and explicitly retain their Mimir
backend settings, including ruler storage caching, `cache_results=true` and query
sharding. The chart rejects address overrides while the corresponding managed
cache is enabled. Temporarily disable Application auto-pruning to keep the old
pools during the first rollout. Validate all workloads, all cache roles using the
shared pool and historical query results before restoring pruning. Never flush
all cache keys as part of validation.
