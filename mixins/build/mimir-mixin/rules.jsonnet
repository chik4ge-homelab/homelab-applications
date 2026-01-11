local mixin = import 'github.com/grafana/mimir/operations/mimir-mixin/mixin.libsonnet';

{
  apiVersion: 'monitoring.coreos.com/v1',
  kind: 'PrometheusRule',
  metadata: {
    name: 'mimir-mixin-rules',
  },
  spec: {
    groups: mixin.prometheusRules.groups,
  },
}
