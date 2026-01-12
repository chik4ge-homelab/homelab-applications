local rule = std.parseYaml(importstr 'metrics/prometheus-install/prometheus-rules/prometheus-kafka-rules.yaml');
local groups = rule.spec.groups;
local rules = groups[0].rules;

local brokerRule = {
  alert: 'KafkaBrokerContainersDown',
  expr: 'absent(container_last_seen{container="kafka",pod=~"kafka-cluster-kafka-broker-nodepool-[0-9]+"})',
  ['for']: '3m',
  labels: {
    severity: 'major',
  },
  annotations: {
    summary: 'All Kafka broker containers down or in CrashLookBackOff status',
    description: 'All Kafka broker containers have been down or in CrashLookBackOff status for 3 minutes',
  },
};

local controllerRule = {
  alert: 'KafkaControllerContainersDown',
  expr: 'absent(container_last_seen{container="kafka",pod=~"kafka-cluster-kafka-controller-nodepool-[0-9]+"})',
  ['for']: '3m',
  labels: {
    severity: 'major',
  },
  annotations: {
    summary: 'All Kafka controller containers down or in CrashLookBackOff status',
    description: 'All Kafka controller containers have been down or in CrashLookBackOff status for 3 minutes',
  },
};

local patchedRules = [
  if r.alert == 'AbnormalControllerState' then r { ['for']: '3m' }
  else if r.alert == 'KafkaBrokerContainersDown' then r + brokerRule
  else if r.alert == 'KafkaControllerContainersDown' then r + controllerRule
  else r
  for r in rules
];

local hasController = std.length([r for r in rules if r.alert == 'KafkaControllerContainersDown']) > 0;

rule {
  spec+: {
    groups: [
      groups[0] {
        rules: if hasController then patchedRules else patchedRules + [controllerRule],
      },
    ],
  },
}
