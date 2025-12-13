#!/usr/bin/env -S bash
set -e

##############
# VARIABLES #
#############
OUTPUT_DIR=${OUTPUT_DIR:="manifests/platform/base/storage/rook-ceph/external-cluster"}
DRY_RUN_OPTS="--dry-run=client -o yaml"
NAMESPACE=${NAMESPACE:="rook-ceph"}
MON_SECRET_NAME=rook-ceph-mon
RGW_ADMIN_OPS_USER_SECRET_NAME=rgw-admin-ops-user
MON_SECRET_CLUSTER_NAME_KEYNAME=cluster-name
MON_SECRET_FSID_KEYNAME=fsid
MON_SECRET_ADMIN_KEYRING_KEYNAME=admin-secret
MON_SECRET_MON_KEYRING_KEYNAME=mon-secret
MON_SECRET_CEPH_USERNAME_KEYNAME=ceph-username
MON_SECRET_CEPH_SECRET_KEYNAME=ceph-secret
MON_ENDPOINT_CONFIGMAP_NAME=rook-ceph-mon-endpoints
EXTERNAL_COMMAND_CONFIGMAP_NAME=external-cluster-user-command
ROOK_EXTERNAL_CLUSTER_NAME=$NAMESPACE
ROOK_RBD_FEATURES=${ROOK_RBD_FEATURES:-"layering"}
ROOK_EXTERNAL_MAX_MON_ID=2
ROOK_EXTERNAL_MAPPING={}
RBD_STORAGE_CLASS_NAME=ceph-rbd
RBD_TOPOLOGY_STORAGE_CLASS_NAME=ceph-rbd-topology
CEPHFS_STORAGE_CLASS_NAME=cephfs
ROOK_EXTERNAL_MONITOR_SECRET=mon-secret
OPERATOR_NAMESPACE=rook-ceph # default set to rook-ceph
CSI_DRIVER_NAME_PREFIX=${CSI_DRIVER_NAME_PREFIX:-$OPERATOR_NAMESPACE}
RBD_PROVISIONER=$CSI_DRIVER_NAME_PREFIX".rbd.csi.ceph.com"       # csi-provisioner-name
CEPHFS_PROVISIONER=$CSI_DRIVER_NAME_PREFIX".cephfs.csi.ceph.com" # csi-provisioner-name
CLUSTER_ID_RBD=$NAMESPACE
CLUSTER_ID_CEPHFS=$NAMESPACE
: "${ROOK_EXTERNAL_ADMIN_SECRET:=admin-secret}"

#############
# FUNCTIONS #
#############

function checkEnvVars() {
  mkdir -p "$OUTPUT_DIR"
  if [ -z "$NAMESPACE" ]; then
    echo "Please populate the environment variable NAMESPACE"
    exit 1
  fi
  if [ -z "$ROOK_RBD_FEATURES" ] || [[ ! "$ROOK_RBD_FEATURES" =~ .*"layering".* ]]; then
    echo "Please populate the environment variable ROOK_RBD_FEATURES"
    echo "For a kernel earlier than 5.4 use a value of 'layering'; for 5.4 or later"
    echo "use 'layering,fast-diff,object-map,deep-flatten,exclusive-lock'"
    exit 1
  fi
  if [ -z "$ROOK_EXTERNAL_FSID" ]; then
    echo "Please populate the environment variable ROOK_EXTERNAL_FSID"
    exit 1
  fi
  if [ -z "$ROOK_EXTERNAL_CEPH_MON_DATA" ]; then
    echo "Please populate the environment variable ROOK_EXTERNAL_CEPH_MON_DATA"
    exit 1
  fi
  if [[ "$ROOK_EXTERNAL_ADMIN_SECRET" == "admin-secret" ]]; then
    if [ -z "$ROOK_EXTERNAL_USER_SECRET" ]; then
      echo "Please populate the environment variable ROOK_EXTERNAL_USER_SECRET"
      exit 1
    fi
    if [ -z "$ROOK_EXTERNAL_USERNAME" ]; then
      echo "Please populate the environment variable ROOK_EXTERNAL_USERNAME"
      exit 1
    fi
  fi
  if [[ "$ROOK_EXTERNAL_ADMIN_SECRET" != "admin-secret" ]] && [ -n "$ROOK_EXTERNAL_USER_SECRET" ]; then
    echo "Providing both ROOK_EXTERNAL_ADMIN_SECRET and ROOK_EXTERNAL_USER_SECRET is not supported, choose one only."
    exit 1
  fi
  if [ -n "$KUBECONTEXT" ]; then
    echo "Using $KUBECONTEXT as the context value for kubectl commands"
    KUBECTL="kubectl --context=$KUBECONTEXT"
  else
    KUBECTL="kubectl"
  fi
}

function createClusterNamespace() {
  $KUBECTL \
    create \
    namespace \
    "$NAMESPACE" \
    $DRY_RUN_OPTS \
    >"$OUTPUT_DIR/namespace.yaml"
}

function createRadosNamespaceCR() {
  cat <<eof | $KUBECTL create -f - $DRY_RUN_OPTS >"$OUTPUT_DIR/cephblockpoolradosnamespace.yaml"
apiVersion: ceph.rook.io/v1
kind: CephBlockPoolRadosNamespace
metadata:
  name: $RADOS_NAMESPACE
  namespace: $NAMESPACE # namespace:cluster
spec:
  # blockPoolName is the name of the CephBlockPool CR where the namespace will be created.
  blockPoolName: $RBD_POOL_NAME
eof
}

function createSubvolumeGroupCR() {
  cat <<eof | $KUBECTL create -f - $DRY_RUN_OPTS >"$OUTPUT_DIR/cephfilesystemsubvolumegroup.yaml"
---
apiVersion: ceph.rook.io/v1
kind: CephFilesystemSubVolumeGroup
metadata:
  name: $SUBVOLUME_GROUP
  namespace: $NAMESPACE # namespace:cluster
spec:
  # filesystemName is the metadata name of the CephFilesystem CR where the subvolume group will be created
  filesystemName: $CEPHFS_FS_NAME
eof
}

function importClusterID() {
  if [ -n "$RADOS_NAMESPACE" ]; then
    createRadosNamespaceCR
    RBD_STORAGE_CLASS_NAME=ceph-rbd-$RADOS_NAMESPACE
  fi
  if [ -n "$SUBVOLUME_GROUP" ]; then
    createSubvolumeGroupCR
    CEPHFS_STORAGE_CLASS_NAME=cephfs-$SUBVOLUME_GROUP
  fi
}

function importSecret() {
  userID=$(getUserId "$ROOK_EXTERNAL_USERNAME")
  $KUBECTL -n "$NAMESPACE" \
    create \
    secret \
    generic \
    --type="kubernetes.io/rook" \
    "$MON_SECRET_NAME" \
    --from-literal="$MON_SECRET_CLUSTER_NAME_KEYNAME"="$ROOK_EXTERNAL_CLUSTER_NAME" \
    --from-literal="$MON_SECRET_FSID_KEYNAME"="$ROOK_EXTERNAL_FSID" \
    --from-literal="$MON_SECRET_ADMIN_KEYRING_KEYNAME"="$ROOK_EXTERNAL_ADMIN_SECRET" \
    --from-literal="$MON_SECRET_MON_KEYRING_KEYNAME"="$ROOK_EXTERNAL_MONITOR_SECRET" \
    --from-literal="$MON_SECRET_CEPH_USERNAME_KEYNAME"="$userID" \
    --from-literal="$MON_SECRET_CEPH_SECRET_KEYNAME"="$ROOK_EXTERNAL_USER_SECRET" \
    $DRY_RUN_OPTS \
    >"$OUTPUT_DIR/mon-secret.yaml"
}

function importConfigMap() {
  $KUBECTL -n "$NAMESPACE" \
    create \
    configmap \
    "$MON_ENDPOINT_CONFIGMAP_NAME" \
    --from-literal=data="$ROOK_EXTERNAL_CEPH_MON_DATA" \
    --from-literal=mapping="$ROOK_EXTERNAL_MAPPING" \
    --from-literal=maxMonId="$ROOK_EXTERNAL_MAX_MON_ID" \
    $DRY_RUN_OPTS \
    >"$OUTPUT_DIR/mon-endpoints-configmap.yaml"
}

function createInputCommadConfigMap() {
  $KUBECTL -n "$NAMESPACE" \
    create \
    configmap \
    "$EXTERNAL_COMMAND_CONFIGMAP_NAME" \
    --from-literal=args="$ARGS" \
    $DRY_RUN_OPTS \
    >"$OUTPUT_DIR/external-cluster-user-command-configmap.yaml"
}

function getUserId(){
  id="$1"
  if [ -z "$CEPHX_KEY_GENERATION" ] || [ "$CEPHX_KEY_GENERATION" == 0 ]; then
    echo "$id"
  else
    result="$id.$CEPHX_KEY_GENERATION"
    echo "$result"
  fi
}

function importCsiRBDNodeSecret() {
  userID=$(getUserId "$CSI_RBD_NODE_SECRET_NAME")
  $KUBECTL -n "$NAMESPACE" \
    create \
    secret \
    generic \
    --type="kubernetes.io/rook" \
    "rook-""$CSI_RBD_NODE_SECRET_NAME" \
    --from-literal=userID="$userID" \
    --from-literal=userKey="$CSI_RBD_NODE_SECRET" \
    $DRY_RUN_OPTS \
    >"$OUTPUT_DIR/csi-rbd-node-secret.yaml"
}

function importCsiRBDProvisionerSecret() {
  userID=$(getUserId "$CSI_RBD_PROVISIONER_SECRET_NAME")
  $KUBECTL -n "$NAMESPACE" \
    create \
    secret \
    generic \
    --type="kubernetes.io/rook" \
    "rook-""$CSI_RBD_PROVISIONER_SECRET_NAME" \
    --from-literal=userID="$userID" \
    --from-literal=userKey="$CSI_RBD_PROVISIONER_SECRET" \
    $DRY_RUN_OPTS \
    >"$OUTPUT_DIR/csi-rbd-provisioner-secret.yaml"
}

function importCsiCephFSNodeSecret() {
  userID=$(getUserId "$CSI_CEPHFS_NODE_SECRET_NAME")
  $KUBECTL -n "$NAMESPACE" \
    create \
    secret \
    generic \
    --type="kubernetes.io/rook" \
    "rook-""$CSI_CEPHFS_NODE_SECRET_NAME" \
    --from-literal=userID="$userID" \
    --from-literal=userKey="$CSI_CEPHFS_NODE_SECRET" \
    $DRY_RUN_OPTS \
    >"$OUTPUT_DIR/csi-cephfs-node-secret.yaml"
}

function importCsiCephFSProvisionerSecret() {
  userID=$(getUserId "$CSI_CEPHFS_PROVISIONER_SECRET_NAME")
  $KUBECTL -n "$NAMESPACE" \
    create \
    secret \
    generic \
    --type="kubernetes.io/rook" \
    "rook-""$CSI_CEPHFS_PROVISIONER_SECRET_NAME" \
    --from-literal=userID="$userID" \
    --from-literal=userKey="$CSI_CEPHFS_PROVISIONER_SECRET" \
    $DRY_RUN_OPTS \
    >"$OUTPUT_DIR/csi-cephfs-provisioner-secret.yaml"
}

function importRGWAdminOpsUser() {
  $KUBECTL -n "$NAMESPACE" \
    create \
    secret \
    generic \
    --type="kubernetes.io/rook" \
    "$RGW_ADMIN_OPS_USER_SECRET_NAME" \
    --from-literal=accessKey="$RGW_ADMIN_OPS_USER_ACCESS_KEY" \
    --from-literal=secretKey="$RGW_ADMIN_OPS_USER_SECRET_KEY" \
    $DRY_RUN_OPTS \
    >"$OUTPUT_DIR/rgw-admin-ops-user-secret.yaml"
}

function createECRBDStorageClass() {
  cat <<eof | $KUBECTL create -f - $DRY_RUN_OPTS >"$OUTPUT_DIR/storageclass-rbd-ec.yaml"
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: $RBD_STORAGE_CLASS_NAME
provisioner: $RBD_PROVISIONER
parameters:
  clusterID: $CLUSTER_ID_RBD
  pool: $RBD_METADATA_EC_POOL_NAME
  dataPool: $RBD_POOL_NAME
  imageFormat: "2"
  imageFeatures: $ROOK_RBD_FEATURES
  csi.storage.k8s.io/provisioner-secret-name: "rook-$CSI_RBD_PROVISIONER_SECRET_NAME"
  csi.storage.k8s.io/provisioner-secret-namespace: $NAMESPACE
  csi.storage.k8s.io/controller-expand-secret-name:  "rook-$CSI_RBD_PROVISIONER_SECRET_NAME"
  csi.storage.k8s.io/controller-expand-secret-namespace: $NAMESPACE
  csi.storage.k8s.io/node-stage-secret-name: "rook-$CSI_RBD_NODE_SECRET_NAME"
  csi.storage.k8s.io/node-stage-secret-namespace: $NAMESPACE
  csi.storage.k8s.io/fstype: ext4
allowVolumeExpansion: true
reclaimPolicy: Delete
eof
}

function createRBDStorageClass() {
  cat <<eof | $KUBECTL create -f - $DRY_RUN_OPTS >"$OUTPUT_DIR/storageclass-rbd.yaml"
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: $RBD_STORAGE_CLASS_NAME
provisioner: $RBD_PROVISIONER
parameters:
  clusterID: $CLUSTER_ID_RBD
  pool: $RBD_POOL_NAME
  imageFormat: "2"
  imageFeatures: $ROOK_RBD_FEATURES
  csi.storage.k8s.io/provisioner-secret-name: "rook-$CSI_RBD_PROVISIONER_SECRET_NAME"
  csi.storage.k8s.io/provisioner-secret-namespace: $NAMESPACE
  csi.storage.k8s.io/controller-expand-secret-name:  "rook-$CSI_RBD_PROVISIONER_SECRET_NAME"
  csi.storage.k8s.io/controller-expand-secret-namespace: $NAMESPACE
  csi.storage.k8s.io/node-stage-secret-name: "rook-$CSI_RBD_NODE_SECRET_NAME"
  csi.storage.k8s.io/node-stage-secret-namespace: $NAMESPACE
  csi.storage.k8s.io/fstype: ext4
allowVolumeExpansion: true
reclaimPolicy: Delete
eof
}

function getTopologyTemplate() {
  topology=$(
    cat <<-END
     {"poolName":"$1",
      "domainSegments":[
        {"domainLabel":"$2","value":"$3"}]},
END
  )
}

function createTopology() {
  TOPOLOGY=""
  declare -a topology_failure_domain_values_array=()
  declare -a topology_pools_array=()
  topology_pools=("$(echo "$TOPOLOGY_POOLS" | tr "," "\n")")
  for i in ${topology_pools[0]}; do topology_pools_array+=("$i"); done
  topology_failure_domain_values=("$(echo "$TOPOLOGY_FAILURE_DOMAIN_VALUES" | tr "," "\n")")
  for i in ${topology_failure_domain_values[0]}; do topology_failure_domain_values_array+=("$i"); done
  for ((i = 0; i < ${#topology_failure_domain_values_array[@]}; i++)); do
    getTopologyTemplate "${topology_pools_array[$i]}" "$TOPOLOGY_FAILURE_DOMAIN_LABEL" "${topology_failure_domain_values_array[$i]}"
    TOPOLOGY="$TOPOLOGY"$'\n'"$topology"
    topology=""
  done
}

function createRBDTopologyStorageClass() {
  cat <<eof | $KUBECTL create -f - $DRY_RUN_OPTS >"$OUTPUT_DIR/storageclass-rbd-topology.yaml"
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: $RBD_TOPOLOGY_STORAGE_CLASS_NAME
provisioner: $RBD_PROVISIONER
parameters:
  clusterID: $CLUSTER_ID_RBD
  imageFormat: "2"
  imageFeatures: $ROOK_RBD_FEATURES
  topologyConstrainedPools: |
    [$TOPOLOGY
    ]
  csi.storage.k8s.io/provisioner-secret-name: "rook-$CSI_RBD_PROVISIONER_SECRET_NAME"
  csi.storage.k8s.io/provisioner-secret-namespace: $NAMESPACE
  csi.storage.k8s.io/controller-expand-secret-name:  "rook-$CSI_RBD_PROVISIONER_SECRET_NAME"
  csi.storage.k8s.io/controller-expand-secret-namespace: $NAMESPACE
  csi.storage.k8s.io/node-stage-secret-name: "rook-$CSI_RBD_NODE_SECRET_NAME"
  csi.storage.k8s.io/node-stage-secret-namespace: $NAMESPACE
  csi.storage.k8s.io/fstype: ext4
allowVolumeExpansion: true
reclaimPolicy: Delete
volumeBindingMode: WaitForFirstConsumer
eof
}

function createCephFSStorageClass() {
  cat <<eof | $KUBECTL create -f - $DRY_RUN_OPTS >"$OUTPUT_DIR/storageclass-cephfs.yaml"
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: $CEPHFS_STORAGE_CLASS_NAME
provisioner: $CEPHFS_PROVISIONER
parameters:
  clusterID: $CLUSTER_ID_CEPHFS
  fsName: $CEPHFS_FS_NAME
  pool: $CEPHFS_POOL_NAME
  csi.storage.k8s.io/provisioner-secret-name: "rook-$CSI_CEPHFS_PROVISIONER_SECRET_NAME"
  csi.storage.k8s.io/provisioner-secret-namespace: $NAMESPACE
  csi.storage.k8s.io/controller-expand-secret-name: "rook-$CSI_CEPHFS_PROVISIONER_SECRET_NAME"
  csi.storage.k8s.io/controller-expand-secret-namespace: $NAMESPACE
  csi.storage.k8s.io/node-stage-secret-name: "rook-$CSI_CEPHFS_NODE_SECRET_NAME"
  csi.storage.k8s.io/node-stage-secret-namespace: $NAMESPACE
allowVolumeExpansion: true
reclaimPolicy: Delete
eof
}

########
# MAIN #
########
checkEnvVars
createClusterNamespace
importClusterID
importSecret
importConfigMap
createInputCommadConfigMap
if [ -n "$CSI_RBD_NODE_SECRET_NAME" ] && [ -n "$CSI_RBD_NODE_SECRET" ]; then
  importCsiRBDNodeSecret
fi
if [ -n "$CSI_RBD_PROVISIONER_SECRET_NAME" ] && [ -n "$CSI_RBD_PROVISIONER_SECRET" ]; then
  importCsiRBDProvisionerSecret
fi
if [ -n "$RGW_ADMIN_OPS_USER_ACCESS_KEY" ] && [ -n "$RGW_ADMIN_OPS_USER_SECRET_KEY" ]; then
  importRGWAdminOpsUser
fi
if [ -n "$CSI_CEPHFS_NODE_SECRET_NAME" ] && [ -n "$CSI_CEPHFS_NODE_SECRET" ]; then
  importCsiCephFSNodeSecret
fi
if [ -n "$CSI_CEPHFS_PROVISIONER_SECRET_NAME" ] && [ -n "$CSI_CEPHFS_PROVISIONER_SECRET" ]; then
  importCsiCephFSProvisionerSecret
fi
if [ -n "$RBD_POOL_NAME" ]; then
  if [ -n "$RBD_METADATA_EC_POOL_NAME" ]; then
    createECRBDStorageClass
  else
    createRBDStorageClass
  fi
fi
if [ -n "$CEPHFS_FS_NAME" ] && [ -n "$CEPHFS_POOL_NAME" ]; then
  createCephFSStorageClass
fi
if [ -n "$TOPOLOGY_POOLS" ] && [ -n "$TOPOLOGY_FAILURE_DOMAIN_LABEL" ] && [ -n "$TOPOLOGY_FAILURE_DOMAIN_VALUES" ]; then
  createTopology
  createRBDTopologyStorageClass
fi
