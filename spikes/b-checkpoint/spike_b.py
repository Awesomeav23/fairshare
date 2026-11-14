"""Spike B: per-model checkpoint to S3, then resume on a brand-new H2O cluster.

  train  <csv>  : run AutoML, save each finished model to S3, simulate preemption after N models
  resume <csv>  : (new cluster) load saved models, run AutoML for the remainder, build combined leaderboard
"""
import json, os, sys, tempfile, threading, time
import boto3, h2o
from h2o.automl import H2OAutoML

MODE, CSV = sys.argv[1], sys.argv[2]
MAX_MODELS, PREEMPT_AFTER, SEED, PROJECT = 8, 3, 42, "job1"
s3 = boto3.client("s3", endpoint_url="http://localhost:9000", aws_access_key_id="spike",
                  aws_secret_access_key="spike", region_name="us-east-1")
BUCKET, PREFIX = "checkpoints", "job1/"

def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)

h2o.connect(url="http://localhost:54321", verbose=False)
h2o.no_progress()
fr = h2o.upload_file(CSV)
fr["default"] = fr["default"].asfactor()
train, valid = fr.split_frame([0.8], seed=SEED)
y, x = "default", [c for c in fr.columns if c != "default"]
log("xgboost backend available:", h2o.estimators.H2OXGBoostEstimator.available())

def save_model(mid):
    m = h2o.get_model(mid)
    with tempfile.TemporaryDirectory() as d:
        p = h2o.download_model(m, path=d)           # client-side binary download
        key = f"{PREFIX}models/{mid}.bin"
        s3.upload_file(p, BUCKET, key)
        return key, os.path.getsize(p)

if MODE == "train":
    aml = H2OAutoML(max_models=MAX_MODELS, seed=SEED, project_name=PROJECT, exclude_algos=["StackedEnsemble"])
    err = {}
    t = threading.Thread(target=lambda: _train(), daemon=True)
    def _train():
        try: aml.train(x=x, y=y, training_frame=train, leaderboard_frame=valid)
        except Exception as e: err["e"] = e
    t0 = time.time(); t.start()
    saved = {}
    while t.is_alive():
        time.sleep(1)
        try:
            ids = [m["name"] for m in h2o.api(f"GET /99/AutoML/{PROJECT}")["leaderboard"]["models"]]
        except Exception:
            continue
        for mid in ids:
            if mid not in saved:
                key, size = save_model(mid); saved[mid] = key
                log(f"checkpointed {mid} ({size/1024:.0f} KB) after {time.time()-t0:.0f}s")
        if len(saved) >= PREEMPT_AFTER:
            jobs = [j for j in h2o.api("GET /3/Jobs")["jobs"] if j["status"] == "RUNNING" and "AutoML" in j["description"]]
            log("PREEMPT: cancelling", [j["key"]["name"] for j in jobs])
            c0 = time.time()
            for j in jobs: h2o.api(f"POST /3/Jobs/{j['key']['name']}/cancel")
            t.join(timeout=60)
            log(f"train thread stopped={not t.is_alive()} in {time.time()-c0:.1f}s, err={type(err.get('e')).__name__ if err else None}")
            break
    # catch any model that finished during cancel
    for m in h2o.api(f"GET /99/AutoML/{PROJECT}")["leaderboard"]["models"]:
        if m["name"] not in saved:
            saved[m["name"]] = save_model(m["name"])[0]; log("late checkpoint", m["name"])
    steps = h2o.api(f"GET /99/AutoML/{PROJECT}").get("modeling_steps")
    manifest = {"saved_models": saved, "runtime_used_secs": round(time.time() - t0), "modeling_steps": steps}
    s3.put_object(Bucket=BUCKET, Key=PREFIX + "manifest.json", Body=json.dumps(manifest, indent=1).encode())
    log("manifest:", json.dumps({k: v for k, v in manifest.items() if k != "modeling_steps"}, indent=1))
    log("modeling_steps:", json.dumps(steps))

elif MODE == "resume":
    man = json.loads(s3.get_object(Bucket=BUCKET, Key=PREFIX + "manifest.json")["Body"].read())
    loaded = []
    with tempfile.TemporaryDirectory() as d:
        for mid, key in man["saved_models"].items():
            p = os.path.join(d, mid); s3.download_file(BUCKET, key, p)
            m = h2o.upload_model(p)                  # client-side upload into the NEW cluster
            loaded.append(m)
            auc = m.model_performance(valid).auc()
            log(f"loaded {m.model_id}: AUC on valid = {auc:.4f}")
    done_ids = {m.model_id for m in loaded}
    remaining = MAX_MODELS - len(loaded)
    # Build plan = full default plan minus steps already done (model id prefix encodes algo + step order).
    # Default 3.46 order observed for this data (exclude StackedEnsemble, no XGBoost on ARM).
    FULL = [("GLM","def_1"),("GBM","def_5"),("DRF","def_1"),("GBM","def_2"),("GBM","def_3"),("GBM","def_4"),("DRF","XRT"),("GBM","def_1")]
    fam = lambda a, sid: "XRT" if sid == "XRT" else a
    executed = [(s["name"], st["id"]) for s in man["modeling_steps"] for st in s["steps"]]
    log("steps executed in attempt 1:", executed)
    saved_n = {}
    for mid in man["saved_models"]:
        f = mid.split("_")[0]; saved_n[f] = saved_n.get(f, 0) + 1
    done, seen = set(), {}
    for a, sid in executed:                      # nth step of a family produced the nth model of that family
        f = fam(a, sid); seen[f] = seen.get(f, 0) + 1
        if seen[f] <= saved_n.get(f, 0): done.add((a, sid))
    rest = [st for st in FULL if st not in done]
    log("steps done (skip):", sorted(done)); log("steps to run:", rest)
    plan = []
    for a, sid in rest:
        if plan and plan[-1][0] == a: plan[-1][1].append(sid)
        else: plan.append((a, [sid]))
    man["resume_plan"] = plan
    log(f"resuming: {len(loaded)} models done, {remaining} remaining")
    r0 = time.time()
    aml2 = H2OAutoML(max_models=remaining, seed=SEED, project_name="job1_a2", exclude_algos=["StackedEnsemble"],
                     modeling_plan=man.get("resume_plan"))
    aml2.train(x=x, y=y, training_frame=train, leaderboard_frame=valid)
    new = [h2o.get_model(r[0]) for r in aml2.leaderboard[:, "model_id"].as_data_frame(use_pandas=False, header=False)]
    log(f"attempt 2 trained {len(new)} models in {time.time()-r0:.0f}s:", [m.model_id for m in new])
    dup_algos = [m.model_id.split("_AutoML")[0] for m in new if m.model_id.split("_AutoML")[0] in {i.split("_AutoML")[0] for i in done_ids}]
    log("same algo+step names retrained (duplicate work):", dup_algos)
    combined = h2o.make_leaderboard(loaded + new, leaderboard_frame=valid)
    log("combined leaderboard:\n" + combined.as_data_frame(use_pandas=False, header=True).__repr__()[:2000])
