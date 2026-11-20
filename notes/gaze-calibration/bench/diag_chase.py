"""Ground-truth audit of harvested chase labels (label error vs model error at that fixation)."""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from gazesim import Subject, World, make_rig, run_calibration, run_session
from bench import GeoController, script_for
from keyless.vision.geo_gaze import GeoGazeModel

rig_name = sys.argv[1] if len(sys.argv) > 1 else "single"
dur = float(sys.argv[2]) if len(sys.argv) > 2 else 150
head0 = np.array([29.9, 12.0, -60.0]) if rig_name == "triple" else np.array([26.5, 10.0, -62.0])
stereo = rig_name.startswith("stereo")
cal = World(make_rig(rig_name), Subject(), np.random.default_rng(11), head0)
X, Y, G = run_calibration(cal, stereo)
m = GeoGazeModel.fit(X, Y, make_rig(rig_name).specs, make_rig(rig_name).ref)
rig = make_rig(rig_name)
w = World(rig, Subject(), np.random.default_rng(1011), head0)
ctl = GeoController(m, chase=True, clicks=False)
ctl.tracker.enable_ekf = False  # audit labels against the static model
st = run_session(w, ctl, stereo, script_for(rig_name, dur), dur, np.random.default_rng(18))
ts = np.array([f.t for f in st.frames]); tp = np.array([f.true_px for f in st.frames]); ch = np.array([f.chasing for f in st.frames]); tk = np.array([f.task for f in st.frames])
for lab in ctl.tracker.harvested:
    sel = (ts >= lab.t0) & (ts <= lab.t1)
    truth = np.nanmedian(tp[sel], axis=0)
    pred = m.predict_batch(lab.feat[None])[0]
    print(f"{lab.source:13s} t={lab.t0:6.1f}-{lab.t1:5.1f} chasing={ch[sel].mean():.1f} label_err={np.linalg.norm(lab.target-truth):6.0f}px model_err={np.linalg.norm(pred-truth):6.0f}px task={sorted(set(tk[sel]))}")
print("chase steps", st.chase_steps, "chains", ctl.tracker.chase.confirmed_chains)
