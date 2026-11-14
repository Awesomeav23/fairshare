"""Spike C: minimal runner. Runs inside kind, talks to H2O by Service DNS and to S3,
checkpoints each model, and handles SIGTERM (= preemption) within the grace period.
Events are printed as JSON lines (RabbitMQ is out of scope for this spike)."""
import json, os, signal, sys, tempfile, threading, time
import boto3, h2o
from h2o.automl import H2OAutoML

import urllib.request
JOB, ATTEMPT = os.environ["JOB_ID"], int(os.environ["ATTEMPT"])
S3_URL = os.environ["S3_ENDPOINT"]
STS, DISCOVERY, NODES = os.environ["H2O_STATEFULSET"], os.environ["H2O_DISCOVERY"], int(os.environ["H2O_NODES"])

def http_status(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, b""
    except Exception: return None, b""

def find_leader():
    """Ask every pod directly (stable StatefulSet DNS). Leader only counts once the full cloud has formed."""
    for i in range(NODES):
        host = f"{STS}-{i}.{DISCOVERY}"
        code, _ = http_status(f"http://{host}:8080/kubernetes/isLeaderNode")
        if code == 200:
            c, body = http_status(f"http://{host}:54321/3/Cloud")
            if c == 200 and json.loads(body)["cloud_size"] == NODES:
                return f"http://{host}:54321"
    return None
s3 = boto3.client("s3", endpoint_url=S3_URL, aws_access_key_id="spike", aws_secret_access_key="spike", region_name="us-east-1")
PROJECT = f"job{JOB}_a{ATTEMPT}"
stop = threading.Event(); sigterm_at = {}

def event(kind, **kw):
    print(json.dumps({"event": kind, "job_id": JOB, "attempt": ATTEMPT, "t": round(time.time(), 2), **kw}), flush=True)

def on_sigterm(signum, frame):
    sigterm_at["t"] = time.time(); stop.set()
signal.signal(signal.SIGTERM, on_sigterm)

# 1. wait for the H2O cloud
t0 = time.time()
H2O_URL = None
while H2O_URL is None and time.time() - t0 < 240:
    H2O_URL = find_leader() or time.sleep(1)
h2o.connect(url=H2O_URL, verbose=False)
h2o.no_progress()
event("connected", leader=H2O_URL, secs=round(time.time() - t0, 1), cloud_size=h2o.cluster().cloud_size)

# 2. dataset from S3 -> H2O
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "data.csv"); s3.download_file("datasets", "credit.csv", p)
    fr = h2o.upload_file(p)
fr["default"] = fr["default"].asfactor()
train, valid = fr.split_frame([0.8], seed=42)
event("started", rows=fr.nrows)

# 3. AutoML in a thread; main thread checkpoints and watches for SIGTERM
aml = H2OAutoML(max_models=8, seed=42, project_name=PROJECT, exclude_algos=["StackedEnsemble"])
th = threading.Thread(target=lambda: _safe_train(), daemon=True)
def _safe_train():
    try: aml.train(x=[c for c in fr.columns if c != "default"], y="default", training_frame=train, leaderboard_frame=valid)
    except Exception as e: print("train ended:", type(e).__name__, flush=True)
th.start()
saved = {}
def checkpoint_new():
    for m in h2o.api(f"GET /99/AutoML/{PROJECT}")["leaderboard"]["models"]:
        mid = m["name"]
        if mid in saved: continue
        with tempfile.TemporaryDirectory() as d:
            path = h2o.download_model(h2o.get_model(mid), path=d)
            key = f"{JOB}/models/{mid}.bin"; s3.upload_file(path, "checkpoints", key)
        saved[mid] = key; event("model_saved", model_id=mid, object_key=key)

while th.is_alive() and not stop.is_set():
    time.sleep(1)
    try: checkpoint_new()
    except Exception as e: print("poll error:", e, flush=True)

if stop.is_set():
    deadline = sigterm_at["t"] + 45            # grace period is 60 s; leave margin
    h2o_ok = True
    try:
        for j in h2o.api("GET /3/Jobs")["jobs"]:
            if j["status"] == "RUNNING": h2o.api(f"POST /3/Jobs/{j['key']['name']}/cancel")
        th.join(timeout=max(0, min(15, deadline - time.time())))
        if time.time() < deadline: checkpoint_new()
    except Exception as e:
        h2o_ok = False; print("H2O unavailable during checkpoint:", type(e).__name__, flush=True)
    steps = None
    try: steps = h2o.api(f"GET /99/AutoML/{PROJECT}").get("modeling_steps")
    except Exception: pass
    # Manifest is written from what is already safely in S3, even if H2O is gone.
    s3.put_object(Bucket="checkpoints", Key=f"{JOB}/manifest.json",
                  Body=json.dumps({"saved_models": saved, "modeling_steps": steps}).encode())
    event("checkpointed", saved_models=list(saved), h2o_reachable=h2o_ok,
          secs_since_sigterm=round(time.time() - sigterm_at["t"], 1))
    sys.stdout.flush(); os._exit(0)            # don't wait on h2o client threads stuck in retries
checkpoint_new()
event("finished", models=len(saved))
