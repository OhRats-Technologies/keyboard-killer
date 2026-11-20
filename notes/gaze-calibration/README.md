# Head-Movement-Robust 3-D Geometric Gaze Calibration & Online Adaptation

## 1. Problem Formulation

The previous gaze pipeline (`StereoGazeModel`) regresses a 20-D facial feature vector directly to 2-D screen pixels $(x, y)$ using a Huber-polynomial + multi-scale RBF manifold. Because screen pixels are a **projection** of a 3-D ray from the user's moving head onto a fixed physical plane, direct 2-D regression fails whenever:

1. **The user translates their head or body** (leaning left/right, moving closer/back, slouching): the ray origin $\mathbf{o}$ shifts by $\Delta \mathbf{o}$, shifting the screen hit by $\Delta \mathbf{o}_{xy}$ plus parallax, which a polynomial trained at one head position extrapolates catastrophically ($12^\circ\text{–}17^\circ$ error for a 10 cm lean).
2. **The camera is mounted off-axis or bumped**: any camera rotation $\Delta \mathbf{R}$ or shift $\Delta \mathbf{c}$ invalidates the pixel map.
3. **Multiple monitors are connected** (different sizes, portrait rotation, hinge angles, tilts): calibrating on one monitor provides no physical extrapolation onto uncalibrated monitors.

### Solution: 3-D Ray-Plane Geometry + Online EKF + Cursor-Chase Self-Calibration

Implemented in [`src/keyless/vision/geo_gaze.py`](../../src/keyless/vision/geo_gaze.py).

---

## 2. Coordinate Frames & Forward Model

We define the world frame $M_0$ on the **reference (calibrated) monitor**:
- Origin at the monitor's top-left corner, $+x$ along pixel rows, $+y$ along pixel columns, $+z$ into the screen (units: **cm**).
- Each monitor $m$ is a physical rectangle $(\mathbf{p}_m, \mathbf{u}_m, \mathbf{v}_m, W_m, H_m)$ in $M_0$, with physical dimensions $(W_m, H_m)$ read from Linux DRM EDID (`/sys/class/drm/card*-*/edid`) and automatically swapped for portrait rotation.

For each active camera block $k \in \{0, 1\}$ (supporting both monocular and stereo rigs, with automatic single-camera fallback when one camera loses the face):

1. **Decode head pose** from the 10-D feature block $[dx_A, dy_A, dx_B, dy_B, \psi_{\text{los}}, \theta_{\text{los}}, \phi, t_x, t_y, t_z]$:
   - Invert the line-of-sight Euler correction from `landmarks._extract_los_euler_and_translation`:
     $$\psi = \psi_{\text{los}} + 0.65 \arctan2(t_x, |t_z|), \qquad \theta = \theta_{\text{los}} + 0.65 \arctan2(t_y, |t_z|)$$
   - Construct head-to-camera rotation $\mathbf{R}_h(g) = \mathbf{R}_z(\phi)\,\mathbf{R}_y(g_\psi \psi)\,\mathbf{R}_x(g_\theta \theta)$ with per-camera MediaPipe yaw/pitch gain $\mathbf{g} = (g_\psi, g_\theta)$.

2. **Eye-in-head gaze direction** $\mathbf{d}_h(\alpha, \beta)$:
   - Binocular iris offset $\overline{dx} = \tfrac{1}{2}(dx_A + dx_B)$, $\overline{dy} = \tfrac{1}{2}(dy_A + dy_B)$ plus camera-to-eye viewing angles $(v_\theta, v_\phi)$ in head coordinates map through a regularized 8-term quadratic basis $\boldsymbol{\Phi}(\overline{dx}, \overline{dy}, v_\theta, v_\phi)$ to eye-in-head angles $(\alpha, \beta)$:
     $$\mathbf{d}_h = \begin{bmatrix} \sin\alpha \cos\beta \\ \sin\beta \\ \cos\alpha \cos\beta \end{bmatrix}$$

3. **3-D gaze ray in $M_0$**:
   - In camera frame: $\mathbf{o}_c = \mathbf{t}_c + \mathbf{R}_h \mathbf{e}_{\text{mid}}$, $\mathbf{d}_c = \mathbf{R}_h \mathbf{d}_h$.
   - Mapped to $M_0$ via the per-camera rigid pose $(\mathbf{R}_k, \mathbf{c}_k)$ and online drift $\boldsymbol{\delta}_k = [\delta r_x, \delta r_y, \delta s_x, \delta s_y]$:
     $$\mathbf{o} = \mathbf{R}_{\delta}(\mathbf{R}_k \mathbf{o}_c) + \mathbf{c}_k + [\delta s_x, \delta s_y, 0]^\top, \qquad \mathbf{d} = \mathbf{R}_{\delta}(\mathbf{R}_k \mathbf{d}_c)$$

4. **Multi-monitor ray intersection & fusion**:
   - Intersect $(\mathbf{o}, \mathbf{d})$ with every monitor plane $m$: $s_m = \frac{(\mathbf{p}_m - \mathbf{o})\cdot \mathbf{n}_m}{\mathbf{d}\cdot \mathbf{n}_m}$.
   - Pick the nearest in-bounds hit ($s_m > 0$), or clamp to the closest rectangle if looking off-bezel.
   - Fuse active cameras by inverse calibration variance $w_k = 1 / \sigma_k^2$.

---

## 3. Eliminating Single-Plane Gauge Freedom at Calibration Time

On a single calibration plane with minimal head translation, the 6-DOF camera pose $(\mathbf{R}_k, \mathbf{c}_k)$ has a severe gauge degeneracy: an unconstrained nonlinear least-squares solver can rotate $\mathbf{R}_k$ by $55^\circ\text{–}80^\circ$ (compensating via iris cross-terms) or trade viewing-angle terms $(v_\theta, v_\phi)$ against head-pose gain $\mathbf{g}$ and push the estimated viewer to $z = -100\text{ cm}$. While calibration residuals look small, any subsequent head translation produces $4^\circ\text{–}6^\circ$ parallax error.

We resolve this cleanly with **head-aligned initialization and soft physical priors** (`_fit_block` / `_residuals`):
- **Head-aligned initialization**: During calibration on $M_0$, the user faces the reference monitor ($\mathbf{R}_{\text{head}\to M_0} \approx \operatorname{diag}(-1, -1, 1)$), so $\mathbf{R}_{k,0} = \operatorname{diag}(-1, -1, 1)\,\overline{\mathbf{R}}_h^\top$ immediately recovers camera orientation to within a few degrees—even for a $35^\circ$ off-axis tripod camera.
- **Physical soft priors**:
  1. Mean calibration head orientation in $M_0$ near $\operatorname{diag}(-1, -1, 1)$ ($\sigma = 15^\circ, 15^\circ, 8^\circ$).
  2. Camera approximately level ($\mathbf{R}_k[0,1]$ small, $\sigma = 12^\circ$).
  3. Viewer position in front of $M_0$ at depth matching the MediaPipe head distance $\|\bar{\mathbf{t}}_c\|$, and camera depth $c_z$ near the monitor plane ($\sigma = 12\text{ cm}$).
  4. Strong damping on $(v_\theta, v_\phi)$ coefficients ($\sigma = 0.06$), which are collinear with $(-\psi, -\theta)$ during static calibration.

This reduces camera rotation recovery error from **$55^\circ\text{–}80^\circ$ down to $1^\circ\text{–}5^\circ$** and cuts off-axis lean/closer/back errors in half.

---

## 4. Automatic Multi-Monitor Placement

Given `DisplayLayout` (logical pixel rectangles + DRM EDID mm):
1. The calibrated reference monitor defines $M_0$.
2. Adjacent monitors sharing a layout edge are attached at that edge in cm and **hinged about the shared edge** until their front normal faces the estimated viewer $\hat{\mathbf{v}}_{M_0}$ (matching how desks angle side/stacked displays).
3. Disconnected monitors are placed coplanar by layout offset and turned toward $\hat{\mathbf{v}}_{M_0}$.
4. Online, `GeoGazeTracker` refines each non-reference monitor's 5-DOF pose $[\text{yaw}, \text{pitch}, t_x, t_y, t_z]$ from clicks and chase labels.
5. On startup, `load_gaze_model(path, layout)` automatically calls `model.with_layout(...)` so plugging/unplugging or rearranging monitors works without recalibrating.

---

## 5. Cursor-Chase Self-Calibration (Proof of Observability)

Consider a locally constant miscalibration bias $\mathbf{b} \in \mathbb{R}^2$ between the displayed cursor $\mathbf{c}$ and true gaze $\mathbf{g}$:
$$\mathbf{c}(t) = \mathbf{g}(t) + \mathbf{b}$$

When a user fixates on an intended target $\mathbf{A}$ (fixation $0$, displayed cursor $\mathbf{c}_0 = \mathbf{A} + \mathbf{b}$), notices the offset cursor, and glances at it:
- **Step $i$** ($i = 1, \dots, n$): The user saccades to the cursor they saw $\tau \approx 100\text{ ms}$ before saccade onset, so their true gaze is $\mathbf{g}_i = \mathbf{c}_{\text{seen}}(t_i - \tau)$. The model then predicts $\hat{\mathbf{p}}_i = \mathbf{g}_i + \mathbf{b}$, forming an arithmetic progression with step $\mathbf{b}$.
- **Return** (fixation $n+1$): The user gives up and looks back at $\mathbf{A}$, landing near $\hat{\mathbf{p}}_0$.

This yields **implicit ground-truth labels with zero explicit calibration**:
- For every chase fixation $i \in \{1, \dots, n\}$: ground truth is directly the buffered displayed cursor $\mathbf{y}_i = \mathbf{c}_{\text{seen}}(t_i - \tau)$ (empirically within **2–26 px** of true gaze vs 160–230 px model error).
- For the initial dwell and return fixations: $\hat{\mathbf{b}} = \frac{1}{n}\sum_{i=1}^n (\hat{\mathbf{p}}_i - \mathbf{c}_{\text{seen},i})$, giving $\mathbf{y}_0 = \mathbf{y}_{n+1} = \hat{\mathbf{p}}_0 - \hat{\mathbf{b}}$.

### Noise-Robust Segmentation & False-Positive Rejection
- **`FixationSegmenter`**: Dual-scale ($k \in \{4, 8\}$ frames) change-point detector anchored to calibration-time fixation noise $\sigma_k$, preventing temporally correlated webcam noise from shattering fixations or inflating $\sigma_k$.
- **`ChaseDetector`**: Rejects left-to-right near-horizontal saccade runs ($\theta < 25^\circ$, i.e. text reading), requires step consistency, and requires single-step loops ($n=1$) to repeat in the same direction within 30 s.
- **`GeoGazeTracker` (EKF)**: Tracks per-camera drift $[\delta r_x, \delta r_y, \delta s_x, \delta s_y]$ (tightly damping translation relative to rotation to prevent depth-coupled nullspace drift) and non-reference monitor 5-DOF pose, with Huber NIS gating and automatic covariance reopening on persistent directional outliers (camera bump recovery).

---

## 6. Virtual Benchmark Results

Closed-loop simulation (`notes/gaze-calibration/bench/bench.py`, `gazesim.py`) calibrated to real webcam feature SNR (horizontal $\approx 6$, vertical $\approx 3\text{–}4$) and fixation noise, running 300 s sessions across 5 phases (`calm`, `lean+closer`, `slouch+back`, `camera_bump`, `recentred`).

### Open-Loop Static Posture Sweep (3 seeds, median angular error in degrees)

| Rig / Posture | `current` (`StereoGazeModel`) | `geo` (`GeoGazeModel`) |
|---|---|---|
| **single** — calib pose | 1.97° | **1.33°** |
| **single** — lean L 10 cm | 12.25° | **1.80°** |
| **single** — lean R 10 cm | 13.64° | **1.73°** |
| **single** — closer 12 cm | 5.09° | **1.95°** |
| **single** — back 12 cm | 2.96° | **1.64°** |
| **single** — slouch 8 cm | 3.87° | **2.05°** |
| **single** — body turn 12° | 5.06° | **1.33°** |
| **single** — head roll 8° | 2.40° | **1.46°** |
| **offaxis** — calib pose | 2.19° | **1.42°** |
| **offaxis** — lean L 10 cm | 10.97° | **2.21°** |
| **offaxis** — lean R 10 cm | 13.57° | **1.96°** |
| **offaxis** — closer 12 cm | 4.63° | **1.95°** |
| **offaxis** — back 12 cm | 4.05° | **1.55°** |
| **offaxis** — slouch 8 cm | 4.64° | **2.41°** |
| **offaxis** — body turn 12° | 5.26° | **1.45°** |
| **offaxis** — head roll 8° | 5.37° | **1.47°** |

### Closed-Loop 5-Rig Benchmark (2 seeds × 300 s, mean of medians / p90 in degrees)

| Rig | `current` | `current+clicks` | `geo` (static) | `geo+clicks` | `geo+clicks+chase` |
|---|---|---|---|---|---|
| **single** (mono, top-center) | 4.07° / 13.32° | 4.49° / 16.38° | 2.06° / 4.59° | **1.97°** / **4.19°** | 2.02° / 4.47° *(bump: **1.17°**, lowest chase/min: 17.1)* |
| **offaxis** (mono, 35° tripod) | 4.37° / 14.27° | 4.95° / 15.93° | 2.73° / 5.46° | 2.51° / **4.76°** | **2.37°** / 5.03° |
| **dual** (24" ref + angled 27") | 6.53° / 15.05° | 6.53° / 15.05° | 3.70° / 9.19° | 2.74° / 6.69° | **2.73°** / **6.49°** |
| **triple** (portrait + 27" + laptop) | 4.69° / 13.50° | 4.97° / 13.80° | 4.06° / 7.81° | **2.44°** / 6.12° | 2.45° / **5.75°** |
| **stereo_dual** (2 cams + 2 screens) | 6.57° / 15.38° | 6.57° / 15.38° | 3.92° / 9.02° | 2.79° / **6.09°** | **2.62°** / 6.29° |

*All multi-monitor rigs (`dual`, `triple`, `stereo_dual`) were calibrated **only on the reference monitor**; non-reference monitor planes were placed automatically from layout + EDID mm and refined online by `GeoGazeTracker`.*

---

## 7. Live Wiring Summary

- **EDID physical monitor dimensions**: `platform/base.py` (`MonitorInfo.width_mm`, `height_mm`) + `platform/linux/display.py` (`read_drm_edid_mm` reads `/sys/class/drm/card*-*/edid`).
- **Calibration & unified loading**: `vision/calibrator.py` (`load_gaze_model`, `_fit_gaze_model`) defaults to `config.gaze_model = "geo"` while preserving full backwards compatibility with legacy `StereoGazeModel` JSON files.
- **Runtime loop & EKF/chase adaptation**: `vision/cursor_gimbal.py` instantiates `GeoGazeTracker` for `GeoGazeModel`, feeds the live displayed cursor into `observe_display`, and enables seamless single-camera fallback in stereo mode when one camera is occluded.
- **Daemon & UI**: `daemon.py`, `main.py`, `ui/gaze_region.py`, and `ui/onboarding/steps.py` use `load_gaze_model`, re-place monitors automatically on layout change (`with_layout`), and forward verified click targets to `GeoGazeTracker.record_click`.

### Running the Benchmarks (Multi-Core Parallel)

Both `bench.py` and `posture_sweep.py` use `ProcessPoolExecutor` (`--workers`, auto-detecting CPU count):

```bash
cd notes/gaze-calibration/bench
uv run python posture_sweep.py single offaxis --seeds 3
uv run python bench.py single offaxis dual triple stereo_dual --seeds 2 --duration 300
```
