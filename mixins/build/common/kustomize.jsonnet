local resources = std.filter(function(item) item != '', std.split(std.extVar('resources'), ','));

{
  apiVersion: 'kustomize.config.k8s.io/v1beta1',
  kind: 'Kustomization',
  resources: resources,
}
