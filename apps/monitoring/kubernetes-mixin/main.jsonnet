local mixin = import 'github.com/kubernetes-monitoring/kubernetes-mixin/version-1.2.0/mixin.libsonnet';

mixin.grafanaDashboards + mixin.prometheusRules
