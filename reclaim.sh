#!/bin/bash

# 出力先ディレクトリ
OUTPUT_DIR=./generated-pvs
mkdir -p "$OUTPUT_DIR"

# PVC 情報を JSON で取得
kubectl get pvc --all-namespaces -o json | jq -c '.items[]' | while read pvc; do
  name=$(echo "$pvc" | jq -r .metadata.name)
  namespace=$(echo "$pvc" | jq -r .metadata.namespace)
  uid=$(echo "$pvc" | jq -r .metadata.uid)
  storage=$(echo "$pvc" | jq -r .spec.resources.requests.storage)
  storage_class=$(echo "$pvc" | jq -r .spec.storageClassName)

  pv_name="pvc-${uid}"
  volume_handle="${pv_name}"
  iqn="iqn.2005-10.org.freenas.ctl:csi-${pv_name}"

  cat <<EOF > "${OUTPUT_DIR}/${pv_name}.yaml"
apiVersion: v1
kind: PersistentVolume
metadata:
  name: ${pv_name}
  annotations:
    pv.kubernetes.io/provisioned-by: truenas-iscsi
    volume.kubernetes.io/provisioner-deletion-secret-name: ""
    volume.kubernetes.io/provisioner-deletion-secret-namespace: ""
spec:
  accessModes:
    - ReadWriteOnce
  capacity:
    storage: ${storage}
  claimRef:
    apiVersion: v1
    kind: PersistentVolumeClaim
    name: ${name}
    namespace: ${namespace}
    uid: ${uid}
  csi:
    driver: truenas-iscsi
    fsType: ext4
    volumeAttributes:
      interface: ""
      iqn: ${iqn}
      lun: "0"
      node_attach_driver: iscsi
      portal: 192.168.0.200:3260
      portals: ""
      provisioner_driver: freenas-iscsi
      storage.kubernetes.io/csiProvisionerIdentity: dummy-id
    volumeHandle: ${volume_handle}
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ${storage_class}
  volumeMode: Filesystem
EOF

  echo "✅ Generated: ${OUTPUT_DIR}/${pv_name}.yaml"

done
