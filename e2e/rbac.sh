#!/usr/bin/env bash
# The agent's service account can read what the tools need and nothing else.
set -euo pipefail
as=system:serviceaccount:orders:pg-ops-agent
fail=0
# usage: check <yes|no> <verb> <resource[/subresource]> [namespace]
check() {
  local want=$1 verb=$2 res=$3 ns=${4:-orders} sub=()
  # "pods/log" as a plain argument means "the pod named log"; subresources
  # need --subresource.
  if [[ $res == */* ]]; then
    sub=(--subresource="${res#*/}")
  fi
  got=$(kubectl auth can-i "$verb" "${res%%/*}" ${sub[@]+"${sub[@]}"} -n "$ns" --as="$as" 2>/dev/null || true)
  if [[ $got == "$want" ]]; then
    printf '  ok    %-3s %-7s %s (%s)\n' "$got" "$verb" "$res" "$ns"
  else
    printf '  FAIL  %-3s %-7s %s (%s), want %s\n' "$got" "$verb" "$res" "$ns" "$want"
    fail=1
  fi
}
echo "allowed:"
check yes get clusters.postgresql.cnpg.io
check yes list backups.postgresql.cnpg.io
check yes list pods
check yes list events
echo "denied:"
check no get secrets
check no create pods/exec
check no get pods/log
check no patch clusters.postgresql.cnpg.io
check no delete pods
check no create backups.postgresql.cnpg.io
check no update clusters.postgresql.cnpg.io/status
check no list pods kube-system
check no get clusters.postgresql.cnpg.io payments
exit $fail
