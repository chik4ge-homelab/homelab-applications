local rule = std.parseYaml(importstr 'metrics/prometheus-install/prometheus-rules/prometheus-kafka-exporter-topic-rules-group.yaml');
local groups = rule.spec.groups;
local filteredRules = [r for r in groups[0].rules if r.alert != 'NoMessageForTooLong'];

rule {
  spec+: {
    groups: [
      groups[0] {
        rules: filteredRules,
      },
    ],
  },
}
