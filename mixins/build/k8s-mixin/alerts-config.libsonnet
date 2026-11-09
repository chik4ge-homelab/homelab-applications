{
  _config+:: {
    // Cilium replaces kube-proxy in this cluster. Keep the upstream
    // KubeProxyDown alert pointed at the Cilium agent scrape target.
    kubeProxySelector: 'job="cilium-agent"',
  },
}
