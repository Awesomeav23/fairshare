#!/usr/bin/env bash
# Usage: run.sh <cluster-name> <train|resume>
set -euo pipefail
cd "$(dirname "$0")/.."
K="kubectl --context kind-fairshare -n fairshare-jobs"
NAME=$1; MODE=$2
T0=$(date +%s)
a-cluster/h2o-cluster.sh "$NAME" 1 1536Mi | $K apply -f - >/dev/null
until [ "$($K get pod $NAME-h2o-0 -o jsonpath='{.status.containerStatuses[0].ready}' 2>/dev/null)" = "true" ]; do sleep 1; done
echo "[cluster $NAME ready in $(( $(date +%s) - T0 ))s]"
$K port-forward svc/$NAME-h2o 54321:54321 >/dev/null 2>&1 & PF=$!
trap 'kill $PF 2>/dev/null' EXIT
sleep 3
.venv/bin/python -I b-checkpoint/spike_b.py "$MODE" data/credit.csv 2>&1 | grep -vE "^\s*$|Warning|warn\(|Closing connection"
