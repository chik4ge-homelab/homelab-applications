local raw = importstr 'metrics/kafka-metrics.yaml';
local docs = [std.parseYaml(doc) for doc in std.split(raw, '\n---') if std.length(doc) > 0];
local configMaps = [
  doc
  for doc in docs
  if doc.kind == 'ConfigMap' && doc.metadata.name == 'kafka-metrics'
];

if std.length(configMaps) != 1 then
  error 'Expected kafka-metrics ConfigMap in metrics/kafka-metrics.yaml'
else
  local configMap = configMaps[0];
  local data = configMap.data;
  local sourceKey = if std.objectHas(data, 'kafka-metrics-config.yaml') then 'kafka-metrics-config.yaml' else 'kafka-metrics-config.yml';
  configMap {
    data: {
      'kafka-metrics-config.yaml': data[sourceKey],
    },
  }
