# Phase 0 spikes

Throwaway experiments that tested the risky assumptions before Phase 1. **Not production code.** Results: [docs/SPIKE_RESULTS.md](../docs/SPIKE_RESULTS.md).

| Folder | Spike |
|---|---|
| `h2o-image/` | ARM64 H2O-3 server image (jar copied from the pip `h2o` package) |
| `kind/` | kind cluster config (`fairshare`) |
| `a-cluster/` | A: H2O cluster manifest generator + node-loss test |
| `b-checkpoint/` | B: checkpoint to S3, resume on a new cluster |
| `c-runner/` | C: runner pod with leader discovery and SIGTERM checkpointing |
| `data/` | Synthetic dataset generator |

## Rerun

```bash
cd spikes
python3 -m venv .venv && .venv/bin/pip install h2o==3.46.0.12 boto3 requests
cp .venv/lib/python3.13/site-packages/h2o/backend/bin/h2o.jar h2o-image/
docker build -t fairshare/h2o:3.46.0.12 h2o-image
docker build -t fairshare/runner-spike:0.1 c-runner
kind create cluster --config kind/cluster.yaml
kind load docker-image fairshare/h2o:3.46.0.12 fairshare/runner-spike:0.1 --name fairshare
kubectl --context kind-fairshare create namespace fairshare-jobs
docker run -d --name fairshare-spike-s3 -p 9000:8333 chrislusf/seaweedfs:latest server -s3 -dir=/data
.venv/bin/python data/make_dataset.py data/credit.csv 60000

# Spike B
b-checkpoint/run.sh cpx train
kubectl --context kind-fairshare -n fairshare-jobs delete sts,svc -l fairshare.io/cluster=cpx
b-checkpoint/run.sh cpy resume

# Spike C (needs the dataset in bucket "datasets", key "credit.csv")
c-runner/run.sh
```
