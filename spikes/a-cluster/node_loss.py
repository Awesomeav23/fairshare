"""Spike A part 2: lock the cloud with work, delete one pod, see if the cloud survives."""
import subprocess, sys, time, h2o
K = ["kubectl", "--context", "kind-fairshare", "-n", "fairshare-jobs"]
h2o.connect(url="http://localhost:54321", verbose=False)
fr = h2o.upload_file(sys.argv[1])   # client-side upload; any work locks the cloud
print("frame rows:", fr.nrows, "| cloud locked:", h2o.cluster().locked)
subprocess.run(K + ["delete", "pod", "spa-h2o-1", "--wait=false"], check=True)
for t in range(0, 91, 10):
    time.sleep(10 if t else 1)
    try:
        c = h2o.api("GET /3/Cloud")
        print(f"t+{t:>2}s cloud_size={c['cloud_size']} healthy={c['cloud_healthy']} bad_nodes={c['bad_nodes']}")
    except Exception as e:
        print(f"t+{t:>2}s cloud API error: {type(e).__name__}: {str(e)[:100]}")
try:
    print("frame still usable? mean(age) =", fr["age"].mean()[0])
except Exception as e:
    print("frame op failed:", type(e).__name__, str(e)[:160])
