## ディレクトリ構造とサービス配置基準

本リポジトリは、Talos Linux 上の Kubernetes クラスタおよび Homelab 全体を GitOps 管理するため、  
サービスをレイヤ別に明確に分類している。

### ディレクトリ構造

```

templates/
├── core/
│   └── networking/
│       └── cilium.yaml
│
├── platform/
│   ├── base/
│   │   ├── operators/
│   │   ├── networking/
│   │   ├── secrets/
│   │   └── storage/
│   │
│   └── services/
│       ├── data-platform/
│       ├── gitops/
│       ├── networking/
│       ├── observability/
│       └── ops/
│
└── workloads/

```

---

### サービス配置のレイヤ定義

#### 1. core
Kubernetes クラスタの成立に必須となる最低限のコンポーネントのみを配置する。

**基準**
- これがないとクラスタが成立しない（Pod ネットワーク・基本ストレージなど）
- 他レイヤから依存されるが、core は他レイヤに依存しない

**例**
- CNI (cilium)

---

#### 2. platform/base
クラスタおよび Homelab 全体の「共通基盤」となる API・CRD・コントロールプレーンを配置する。

**基準**
- operator / CRD / バックエンドなど「他の platform や workloads を支える基盤」
- 外部公開・認証・シークレット・ストレージなど、全体の前提となる仕組み
- base → services → workloads の一方向依存を保つ

**典型カテゴリ**
- networking（gateway, ingress, DNS, VPN）
- secrets（external-secrets, cert-manager, 1password-connect）
- storage（CSI）
- data-operator（MinIO operator, Percona operator, Strimzi operator）

---

#### 3. platform/services
base を利用して提供される「共有サービス」を配置する。  
クラスタの可観測性・運用性・データ基盤など、全体を支えるサービス層である。

**基準**
- クラスタ運用に不可欠だが「クラスタの成立条件」ではない
- operator ではなく、その operator が管理する実体（MinIO・DB・Kafka など）
- observability / gitops / ops などの上位サービス

**典型カテゴリ**
- data（MinIO クラスタ、共通 DB クラスタ等）
- observability（grafana, mimir, loki, alloy, exporters）
- gitops（argocd, arc）
- ops（descheduler, reloader, talos-backup, pve-lb）
- networking サービス（Proxmox 用 HAProxy など）

---

#### 4. workloads
最終的なエンドユーザーに価値を提供するアプリケーションを配置する。

**基準**
- そのアプリ自身が目的であり、他の workloads を支える基盤ではない
- 停止してもクラスタや platform は成立し続ける
- Home Assistant、Unifi、ゲームサーバーなど

---

### 依存関係ポリシー

```

core → platform/base → platform/services → workloads

```

- 依存は必ず上記方向のみ。逆方向は作らない。
  - 例外として、 gitops 用の ArgoCD のみは全レイヤを監視するため逆方向依存を許容する。
- platform/base は他レイヤの前提となる “基礎 API” として扱う。
- operator（CRD・controller）は base、実体（クラスタ・テナント）は services。
- workloads は platform の上で動く“最終価値の提供者”。

---

### mixins 運用

- mixin 生成定義は `mixins/build/config.yaml` で管理する
- `entrypoint` は jsonnet の入口、`destination` は出力先ディレクトリ
- 生成は `scripts/generate-mixin-rules.sh` を実行する
