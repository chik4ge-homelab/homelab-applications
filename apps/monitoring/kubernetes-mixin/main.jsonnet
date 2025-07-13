local mixin = import 'vendor/kubernetes-mixin/mixin.libsonnet';

mixin.grafanaDashboards + mixin.prometheusRules
