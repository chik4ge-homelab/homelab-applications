local mixin = import 'github.com/kubernetes-monitoring/kubernetes-mixin/mixin.libsonnet';

{
  apiVersion: 'monitoring.coreos.com/v1',
  kind: 'PrometheusRule',
  metadata: {
    name: 'k8s-mixin-alerts',
  },
  spec: {
    groups: mixin.prometheusAlerts.groups,
  },
}
