# Local Koordinator chart patches

Upstream chart: `koordinator-sh/koordinator` version `1.8.0`, from
<https://koordinator-sh.github.io/charts/>.

- Make the koordlet vGPU records host path configurable. Talos keeps `/usr/local`
  read-only, so this deployment places the unused vGPU records directory under
  `/var/run/koordlet`.
- Render `deviceDaemon.nodeAffinity` under the Kubernetes `PodAffinity.nodeAffinity`
  field. The upstream template emitted its `NodeAffinity` value directly as a
  `PodAffinity`, which the API server pruned and left the DaemonSet unrestricted.
- Omit the chart's Namespace and scheduler/descheduler ConfigMaps because this
  application manages them from its Kustomize source, including the Pod Security
  labels and cluster-specific policies.
- Give the PodMonitor template a `.yaml` extension so Helm lint accepts the chart.

The chart's upstream defaults remain unchanged except for the added path value;
the deployment-specific override is in `charts/templates/platform/services/ops/koordinator.yaml`.
