local parsed = std.parseYaml(importstr 'github.com/strimzi/strimzi-kafka-operator/examples/metrics/prometheus-install/pod-monitors/entity-operator-metrics.yaml');
local monitor = if std.type(parsed) == 'array' then parsed[0] else parsed;

monitor {
  spec+: {
    namespaceSelector: {
      matchNames: ['strimzi-kafka'],
    },
  },
}
