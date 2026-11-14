#!/usr/bin/env bash
# Spike C: 2-node H2O cluster + runner pod; preempt (delete pod) after N saved models.
set -uo pipefail
cd "$(dirname "$0")/.."
K="kubectl --context kind-fairshare -n fairshare-jobs"
MEM=${MEM:-1536Mi}; AFTER=${AFTER:-3}
$K delete statefulset,service -l fairshare.io/cluster=spc --wait=true >/dev/null 2>&1; $K delete pod job7-a1-runner --ignore-not-found --wait=true >/dev/null
.venv/bin/python -I -c "
import boto3;s3=boto3.client('s3',endpoint_url='http://localhost:9000',aws_access_key_id='spike',aws_secret_access_key='spike',region_name='us-east-1')
[s3.delete_object(Bucket='checkpoints',Key=o['Key']) for o in s3.list_objects_v2(Bucket='checkpoints').get('Contents',[])]"
a-cluster/h2o-cluster.sh spc 2 "$MEM" | $K apply -f - >/dev/null
$K apply -f c-runner/runner-pod.yaml >/dev/null
until [ "$($K get pod job7-a1-runner -o jsonpath='{.status.phase}')" = "Running" ]; do sleep 1; done
mkdir -p out; LOG=out/runner.log; : > $LOG
$K logs -f job7-a1-runner > $LOG 2>&1 &
for i in $(seq 1 300); do [ "$(grep -c model_saved $LOG)" -ge "$AFTER" ] && break; grep -qE '"finished"|Traceback' $LOG && break; sleep 1; done
echo "[$(date +%T)] deleting runner pod (SIGTERM)"; D0=$(date +%s)
$K delete pod job7-a1-runner --wait=true >/dev/null
echo "[$(date +%T)] pod gone after $(( $(date +%s) - D0 ))s"
sleep 1; grep -E '^\{|unavailable|Traceback|Error' $LOG | cut -c1-260
echo "--- H2O pods:"; $K get pods -l fairshare.io/cluster=spc -o custom-columns='NAME:.metadata.name,RESTARTS:.status.containerStatuses[0].restartCount,LAST:.status.containerStatuses[0].lastState.terminated.reason'
echo "--- S3 checkpoints:"; .venv/bin/python -I -c "
import boto3;s3=boto3.client('s3',endpoint_url='http://localhost:9000',aws_access_key_id='spike',aws_secret_access_key='spike',region_name='us-east-1')
[print(' ',o['Key'],o['Size']) for o in s3.list_objects_v2(Bucket='checkpoints').get('Contents',[])]"
