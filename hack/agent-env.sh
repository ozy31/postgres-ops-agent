#!/usr/bin/env bash
# Prints the environment for running the MCP server *as the agent's service
# account*, not as you: a kubeconfig with a short-lived token for
# pg-ops-agent, and the ops_agent database role. Written under .state/
# (git-ignored).
#
#   eval "$(hack/agent-env.sh)"
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .state
kc=.state/agent.kubeconfig

server=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' |
  base64 -d > .state/ca.crt
token=$(kubectl -n orders create token pg-ops-agent --duration=2h)
KUBECONFIG=$kc kubectl config set-cluster e2e --server="$server" \
  --certificate-authority=.state/ca.crt --embed-certs=true >/dev/null
KUBECONFIG=$kc kubectl config set-credentials pg-ops-agent --token="$token" >/dev/null
KUBECONFIG=$kc kubectl config set-context agent --cluster=e2e --user=pg-ops-agent --namespace=orders >/dev/null
KUBECONFIG=$kc kubectl config use-context agent >/dev/null
rm -f .state/ca.crt

kubectl -n orders get secret pg-ops-agent-db -o jsonpath='{.data.password}' | base64 -d > .state/db-password
chmod 600 .state/db-password "$kc"

cat <<ENV
export KUBECONFIG=$PWD/$kc
export PG_OPS_NAMESPACE=orders PG_OPS_CLUSTER=orders-db
export PG_OPS_HOST=127.0.0.1 PG_OPS_PORT=15432 PG_OPS_DATABASE=orders PG_OPS_USER=ops_agent
export PG_OPS_PASSWORD_FILE=$PWD/.state/db-password
ENV
