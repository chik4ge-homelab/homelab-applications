local mixin = import 'github.com/imusmanmalik/cert-manager-mixin/mixin.libsonnet';
local config = import 'config.libsonnet';

{
  apiVersion: 'monitoring.coreos.com/v1',
  kind: 'PrometheusRule',
  metadata: {
    name: 'cert-manager-mixin-rules',
  },
  spec: {
    groups: (mixin + config).prometheusRules.groups,
  },
}
