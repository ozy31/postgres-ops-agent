#!/usr/bin/env bash
# Single-node kind cluster with CloudNativePG, the Barman Cloud plugin, local
# S3 and the test cluster from e2e/cluster.yaml. Every credential is
# generated here and lives only in the cluster.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=e2e/versions.env
source e2e/versions.env

kind get clusters | grep -qx pg-ops || kind create cluster --name pg-ops --image "$KIND_NODE_IMAGE" --wait 2m

helm repo add jetstack https://charts.jetstack.io --force-update >/dev/null
helm repo add cnpg https://cloudnative-pg.github.io/charts --force-update >/dev/null
helm upgrade --install cert-manager jetstack/cert-manager --version "$CERT_MANAGER_CHART_VERSION" \
  -n cert-manager --create-namespace --set crds.enabled=true --wait
helm upgrade --install cnpg cnpg/cloudnative-pg --version "$CNPG_CHART_VERSION" \
  -n cnpg-system --create-namespace --wait
helm upgrade --install barman-cloud cnpg/plugin-barman-cloud --version "$BARMAN_CLOUD_CHART_VERSION" \
  -n cnpg-system --wait

secret() { openssl rand -hex "$1"; }
ns() { kubectl create namespace "$1" --dry-run=client -o yaml | kubectl apply -f - >/dev/null; }
ns orders
ns object-store

if ! kubectl -n orders get secret orders-db-backup-s3 >/dev/null 2>&1; then
  key=$(secret 10) sec=$(secret 20)
  kubectl -n object-store create secret generic seaweedfs-s3-config \
    --from-literal=s3.json="{\"identities\":[{\"name\":\"orders-db\",\"credentials\":[{\"accessKey\":\"$key\",\"secretKey\":\"$sec\"}],\"actions\":[\"Read\",\"Write\",\"List\",\"Tagging\"]}]}"
  kubectl -n orders create secret generic orders-db-backup-s3 \
    --from-literal=ACCESS_KEY_ID="$key" --from-literal=ACCESS_SECRET_KEY="$sec"
fi
if ! kubectl -n orders get secret pg-ops-agent-db >/dev/null 2>&1; then
  kubectl -n orders create secret generic pg-ops-agent-db --type=kubernetes.io/basic-auth \
    --from-literal=username=ops_agent --from-literal=password="$(secret 16)"
  kubectl -n orders label secret pg-ops-agent-db cnpg.io/reload=true
fi

kubectl apply -f e2e/seaweedfs.yaml
kubectl -n object-store rollout status deploy/seaweedfs --timeout=3m
kubectl apply -f e2e/cluster.yaml -f deploy/rbac.yaml
kubectl -n orders wait cluster/orders-db --for=condition=Ready --timeout=10m
kubectl -n orders rollout status deploy/orders-db-pooler-rw --timeout=3m
echo "e2e cluster ready"
