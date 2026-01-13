local mixin = std.extVar('mixin');
local config = std.extVar('config');
local groupsKey = std.extVar('groups_key');

{
  apiVersion: 'monitoring.coreos.com/v1',
  kind: 'PrometheusRule',
  metadata: {
    name: std.extVar('name'),
  },
  spec: {
    groups: (mixin + config)[groupsKey].groups,
  },
}
