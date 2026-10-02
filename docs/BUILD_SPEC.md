box about the peak excluded); `|du|, |dv| ≤ 3.0° · s = 60 px`; `std(highpassed)/mean(patch) ≥ 0.01`; valid intersection ≥ 0.15 · 512². Featureless sky produces a low PSR and is dropped, which is correct — there is nothing to align there and nothing will look wrong either.

**Fallback:** if > 40 % of pairs fail the PSR gate, use `opencv-python` AKAZE/ORB matches (ratio test + RANSAC) into the same normal equations, one row pair per match.

## 9.3 The guard — non-negotiable

```
Score(params) = sum_p n_p * NCC_p / sum_p n_p
  NCC_p = zero-lag NCC of the two high-passed, Hann-windowed 512x512 patches for pair p,
          re-rendered under `params`, over the intersection mask.
  The pair set P is FROZEN from the baseline's accepted pairs, so both scores are computed
  over exactly the same evidence.

ACCEPT only if ALL of:
  Score(refined) > Score(baseline) * 1.005
  max|dYaw| <= 2.0 deg  and  max|dPitch| <= 2.0 deg
  |P| / |P_possible| >= 0.60
  no clamp was hit during the final solve
Otherwise revert to metadata-only and write geometry.refinement.rejected_reason.
Always record BOTH scores and which path produced the shipped file.
```

Additional cheap runtime guard that a residual sign inversion would trip: after the **first** GN step, require `Score(step1) > Score(baseline)`; if it dropped, the residual sign is inverted, not the data.

**Regression test that pins both signs (mandatory, T15):** inject `Δyaw = +1.0°` into one tile of `100_0679`'s *estimate*, re-render the pair, run one GN iteration, assert the recovered correction is `−1.0° ± 0.1°` — not `+1.0°` (row sign flipped) and not `−2.0°` (correlation sign flipped). That single test fixes the whole chain.

## 9.4 Lens distortion — not corrected in v1

`f_px = 1520.9` was measured by NCC homography fitting on real overlaps, which already absorbs most of any residual radial term into the focal estimate, and GROUND_TRUTH §1 notes DJI distortion-corrects these tiles. The metadata itself carries 0.5–2° of angle error, i.e. **11–45 px** of misregistration at the 8192 master (22.756 px/deg), so a radial coefficient would have to displace the corner by more than that to matter: with `r_corner = 0.8219`, `Δr_px = f_px · r_corner · |k1| · r² = 844.6·|k1|`, requiring `|k1| ≳ 0.013`. Implausible for a camera that applies its own lens profile.

**Policy:** pinhole only. Measure `k1, k2` **once** by the plumb-line method on the ~170 near-zero-pitch horizon tiles in the archive (sub-pixel argmax of the vertical luminance gradient per column in `v ∈ [400,1100]`, Huber line fit, accept a tile only if ≥1400 columns survive with residual RMS < 1.5 px, pool and minimise), store in `calibration.json`, and enable distortion only if `|k1| > 0.01`. The method resolves `k1` to ~±0.001, so it is sufficient to *prove* distortion negligible — which is the expected outcome.

If ever enabled, the model is applied in the **forward** direction (ray → pixel), inserted between §3.2 steps 4 and 5:

```
x_n = d_c.x/d_c.z ; y_n = d_c.y/d_c.z ; r2 = x_n^2 + y_n^2
s   = 1 + k1*r2 + k2*r2*r2
u   = f_px*s*x_n + cx ;  v = f_px*s*y_n + cy
```

Forward is exactly what inverse mapping needs — **no iterative undistortion is ever required.** That is a structural advantage of the §3 formulation and the reason to keep it.

---

# 10. Outputs, cache, and orchestration

## 10.1 Files per set

```
out/panos/<set_id>/
  pano.json                    the per-set record AND the cache entry (written LAST)
  equirect_8192.jpg            tone-mapped master, q92, 4:4:4, sRGB ICC embedded
  equirect_4096.jpg            q88          (derived from the master)
  equirect_2048.jpg            q88
  equirect_1024.jpg            q82
  equirect_0512.jpg            q82
  thumb_320.jpg                q88, for the gallery grid
  linear_2048.rgbe.png         RGBE linear radiance, RGBA8 -> the viewer's exposure slider
  coverage_2048.png            8-bit grey: 255 captured, 128 synthetic fill, 0 no-data
  ghost_0512.png               optional disagreement map overlay
  cube_{px,nx,py,ny,pz,nz}.jpg optional, q94, S = 2048
  linear_master.npy            optional, --keep-linear, float16
```

**Chroma subsampling must be 4:4:4.** Not for the reason the geometry spec gave (it claimed 4:2:0 chroma is *stretched* near the poles — inverted: a 2-column chroma block always covers 2 columns, i.e. `2·(360/W)·cos(el)` degrees of arc, which is 5.8× *finer* at el 80° than at the equator, so the poles are where 4:2:0 would be least damaging). The real reasons: **the equator, where 4:2:0 is coarsest (0.088° of arc per chroma sample at W=8192), is where the content lives in every mode** — especially the strip and wide modes; and the output is re-resampled downstream (cube faces, the pyramid, the viewer's own reprojection), so 4:2:0 artefacts get amplified rather than staying subpixel. **Corrected.**

Pillow writes JPEG up to 65 500 px per side, so 8192 and 12288 are both fine. Note that an 8192×4096 JPEG decodes to ~134 MB RGBA — do not hand it to the browser interactively (§8.4).

## 10.2 GPano XMP

Pillow does not write XMP; either inject the packet into the JPEG's APP1 segment list manually or call `exiftool -XMP-GPano:...`. Preserve `GPSLatitude/Longitude/Altitude`, `DateTimeOriginal` and `Make/Model` from the first tile.

```
GPano:UsePanoramaViewer             True
GPano:ProjectionType                equirectangular
GPano:FullPanoWidthPixels           round(360 * px_per_deg)          # the VIRTUAL full sphere
GPano:FullPanoHeightPixels          FullPanoWidthPixels / 2
GPano:CroppedAreaImageWidthPixels   <actual image width>
GPano:CroppedAreaImageHeightPixels  <actual image height>
GPano:CroppedAreaLeftPixels         round((wrap180(theta_left - az_center) + 180) * px_per_deg)
GPano:CroppedAreaTopPixels          round((90 - phi_top) * px_per_deg)
GPano:PoseHeadingDegrees            az_center           (0 for full-circle outputs)
GPano:PosePitchDegrees / PoseRollDegrees        0 / 0
GPano:InitialViewHeadingDegrees / PitchDegrees  from viewer_hints
GPano:InitialHorizontalFOVDegrees   90
GPano:SourcePhotosCount             <tile count>
GPano:FirstPhotoDate / LastPhotoDate            min/max DateTimeOriginal, ISO-8601
GPano:StitchingSoftware             "pano <version>"
```

Because §7.2 pads the **angular** span, the declared rectangle and the actual pixel grid agree exactly.

Worked cases: full sphere with the cap filled → `Cropped == Full`, `Left = Top = 0`, 8192×4096. Full sphere **cap not filled** (`el ∈ [−90, +38.8]`) → `CroppedAreaImageHeightPixels = round(128.8·22.7556) = 2931`, `CroppedAreaTopPixels = round(51.2·22.7556) = 1165`, `FullPanoHeightPixels = 4096`, width full — Google Photos, Facebook, Marzipano and Pannellum all letterbox this correctly. 7-tile strip (246.9° × 52.7°, image 5616 × 1200) → `FullPanoWidth 8192`, `FullPanoHeight 4096`, `CroppedAreaTopPixels = round((90 − 26.35)·22.7556) = 1448`, `CroppedAreaLeftPixels` from `theta_left`.

**GPano cannot express a ragged boundary or distinguish captured from synthetic.** Three coverage layers are written, in increasing fidelity, and **all three** are mandatory:

1. `GPano:CroppedArea*` / `FullPano*` — universal, third-party, rectangle only.
2. `coverage_2048.png` — per-pixel captured / synthetic / no-data. Handles the scalloped boundary and inpainted holes.
3. `pano.json → coverage` — the authoritative record. **The project's own viewer reads this and nothing else.**

## 10.3 Cache — three chained keys

```python
PIPELINE_VERSION   = "stitch/1"
KEY_INPUTS_VERSION = 1              # <-- an INPUT to all three hashes, not just a stored field
EXIF_READER_VERSION = 1

def ingest_key(tiles, backend):
    h = sha256(); h.update(f"ingest/{KEY_INPUTS_VERSION}\x00{backend}\x1f{EXIF_READER_VERSION}\x1e".encode())
    for t in sorted(tiles, key=lambda t: t.file):        # basenames, NOT absolute paths
        h.update(f"{t.file}\x1f{t.bytes}\x1f"
                 f"{t.yaw:.4f}\x1f{t.pitch:.4f}\x1f{t.roll:.4f}\x1f"
                 f"{t.exposure_s:.9g}\x1f{int(t.iso)}\x1f{t.fnumber:.4f}\x1f"
                 f"{t.width}x{t.height}\x1e".encode())
    return h.hexdigest()

def stitch_key(ingest_key, stitch_inputs, camera, classification, coverage_image):
    return sha256(("stitch/%d\x00" % KEY_INPUTS_VERSION + ingest_key + "\x00" + PIPELINE_VERSION
        + "\x00" + cjson(stitch_inputs) + "\x00" + cjson(camera) + "\x00"
        + cjson({"mode": classification.mode, "geometry": classification.geometry})
        + "\x00" + cjson(coverage_image)).encode()).hexdigest()

def derive_key(stitch_key, output_spec):
    return sha256(("derive/%d\x00" % KEY_INPUTS_VERSION + stitch_key + "\x00"
        + cjson(output_spec)).encode()).hexdigest()

# cjson = json.dumps(x, sort_keys=True, separators=(",",":"), ensure_ascii=True)
#         with every float pre-formatted to a fixed precision by the caller.
```

Six corrections, each of which silently defeated incrementality or correctness:

* **`mtime_ns` is not in the identity key.** It is stored per tile as a *hint* only: if all mtimes match, skip even the metadata read and trust the sidecar; if an mtime differs but the recomputed `ingest_key` matches, accept the cache and silently refresh the recorded mtimes. This archive is a set of SD-card backup copies whose own install doc says "or unzip"; ZIP stores 2-second DOS timestamps and exFAT has 2-second granularity, so any normal re-copy rewrites all 2952 mtimes and would force a 12–20 minute rebuild of byte-identical data.
* **Numerics are canonicalised, not `repr()`d.** `{t.exposure_time_s!r}` is representation-dependent: one backend may hand back `0.000625`, another `"1/1600"` or a `Fraction`. Because `--exif-backend auto` can escalate per root, the effective backend for a given root could differ between runs through nothing the user changed, flipping the key and re-stitching up to 107 spheres on a no-op rerun.
* **The camera model is hashed.** `f_px`, `cx`, `cy`, `hfov`, `vfov`, `k1`, `k2`, `tile_size`. Without it the near-certain per-set focal refinement (or any future recalibration) leaves 135 sidecars reporting `up_to_date` with geometry built from the old constants. A test perturbs `f_px` by 0.01 and asserts every `stitch_key` changes.
* **`coverage.image` is hashed**, since the canvas is a function of angles *and* camera and is not otherwise captured.
* **`backend` lives in `stats`, never in `params`.** Installing the optional `opencv-python-headless` accelerator would otherwise flip every `stitch_key` and re-stitch the archive, for a dependency the spec promises is transparent.
* **Master width and quality are on the stitch side; `derive_key` covers only levels strictly below the master, plus thumb, blur and cube.** The master *is* the top level, so honouring `--quality 95` from the derive path would re-encode the q92 master from itself — a second generation of loss on the one artifact that is never box-downsampled — and "add a 16384 level" is simply not derivable from an 8192 master. Restated honestly: *changing the quality of a derived level, or adding a level at or below the master width, costs ~2 s per pano; changing master width or master quality is a re-stitch at ~47 s.*

`reference_tile` stays in `stitch_inputs` because `a_ref = min_k a_k` is a deterministic function of `TileMeta` and is therefore computable at plan time (§4.1). Every other solved value (per-tile gains, seam decisions, the backend actually used) lives in `stitch.solved` and is **not** hashed. `--verify-content` computes a **separate** `content_key` over (first 64 KiB ‖ last 64 KiB) per tile and compares it independently — never folded into `ingest_key`, because that made a 5-second integrity check cost a full 12–20 minute rebuild *in both directions*, forever ping-ponging between the two modes.

## 10.4 `plan()` — status first

```
status is the FIRST discriminator, before any key comparison:

  status == "failed" and error.retryable and cache.attempts < 3   -> retry the failing stage
  status == "failed" and not error.retryable                      -> failed_sticky (report,
        do not re-attempt; cleared automatically when ingest_key changes, or by --retry-failed)
  status == "skipped" and the skip reason still holds             -> stay skipped
  status == "cancelled"                                           -> re-plan normally

then:
  source volume unreachable, pano.json exists   -> source_offline   (stays fully browsable)
  no pano.json  /  ingest_key differs           -> needs_ingest
  ingest_key equal, stitch_key differs          -> needs_stitch
  stitch_key equal, derive_key differs          -> needs_derive
  MASTER file missing or wrong size             -> needs_stitch      (NOT needs_derive)
  a derived file missing or wrong size          -> needs_derive
  all equal, every outputs path exists at size  -> up_to_date
```

Without the status rows the table classifies a transiently failed set as `up_to_date` — `all()` over an empty `outputs` list is vacuously true, and unchanged size/angles mean the keys match forever, so a set that failed because E: browned out mid-read is never retried and `--force` (a full-archive re-stitch) is the only escape. The same hole un-skips deliberately skipped duplicates on the very next run. And routing a missing *master* to `needs_derive` sends it to a stage that reads from the master, producing a permanently dead entry. **All corrected.**

Restate the completion invariant precisely: *a `pano.json` with `status ∈ {ok, ok_partial}` and matching keys is proof of completion* — not merely "the presence of a `pano.json`", since failure and skip records are also `pano.json`.

`derive`/`retryable` classification comes from the exception class, not by hand: `OSError`, `PermissionError`, `TimeoutError`, `BrokenProcessPool`, `MemoryError` → retryable; a structurally invalid JPEG or a missing XMP packet → not.

## 10.5 Orchestration

**Process isolation per set.** `ProcessPoolExecutor`, one whole set per task (a 26-tile set amortises Windows `spawn`'s ~250 ms to under 1 %). An exception handler cannot catch a segfault in a JPEG decoder or an OOM kill, so:

* The worker wraps its whole body in `try/except BaseException` and **returns** `StitchResult(status="failed", error=...)` rather than raising, so ordinary failures never travel through the executor.
* Catch `BrokenProcessPool` around `as_completed`, discard the dead executor, build a fresh one, and requeue every submitted-but-incomplete task with a per-task attempt cap of 2. Abort only if the pool breaks twice with no task completing in between. **Without this, one abruptly-dying worker completes every pending future with `BrokenProcessPool` and kills the whole run** — the literal failure this dimension exists to prevent, and one no spec mentioned.
* `max_tasks_per_child=8` so a leak cannot accumulate across 107 spheres.
* Per-set timeout 600 s → kill, `status: "timeout"`.

**Pool initializer** (and, for the env vars, the parent **before** pool creation, since `spawn` children inherit the environment):

```python
for v in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS",
          "NUMEXPR_NUM_THREADS","VECLIB_MAXIMUM_THREADS"): os.environ[v] = "1"
signal.signal(signal.SIGINT, signal.SIG_IGN)        # only the PARENT handles Ctrl-C
PIL.ImageFile.LOAD_TRUNCATED_IMAGES = True          # set ONCE, never toggled (§6.7)
if cv2: cv2.setNumThreads(1)
```

`SIG_IGN` in the children is what makes the graceful-drain guarantee real. On Windows a console Ctrl-C delivers `CTRL_C_EVENT` to **every** process attached to that console, so as specified all 8 workers raise `KeyboardInterrupt` at the same instant as the parent and the pending futures come back as `BrokenProcessPool` — the in-flight sets die mid-stitch rather than finishing, and the stated "~45 s bound" and "in-flight workers finish" are both false. With `SIG_IGN` the parent can stop enqueuing and `wait(futures, timeout=60)` for genuine completion. A `cancelled` result writes no sidecar and is re-planned next run — never recorded as `failed`. Second Ctrl-C hard-kills. On the `Pano.cmd` path, suppress cmd's "Terminate batch job (Y/N)?" with `<nul`.

**`library.json` is written incrementally, not only at the end.** Three write points: (a) immediately after classify, with all 135 summary records, ingest-generated blur URIs, `status: "queued"`, `levels: []`, `build.state: "running"` — this is what the browser loads at t≈20 s, before any stitching; (b) debounced every ~2 s or every 5 completions; (c) at the end **and** from the SIGINT handler. Cache parsed sidecars in memory so (b) is cheap. As specified — terminal reduce only — `GET /lib/library.json` 404s at the moment `pano up` opens the browser and the headline first-paint promise cannot happen; and a reload at minute 5 loses every `pano_ready` already delivered over SSE.

**SSE payloads are defined**, so the page never has to refetch the whole manifest:

```
event: pano_ready   data: <the full panoramas[] summary object for that id>
event: progress     data: {run_id, done, total, eta_s, current: [ids]}
event: done         data: {run_id, state}          # ALWAYS emitted, even on failure
```

`build.state ∈ {queued, running, complete, interrupted, failed}`. An orchestrator-level `try/finally` writes `failed`; the SIGINT path writes `interrupted`. The client treats a closed stream with no terminal event as `unknown` and falls back to polling `/api/status` — otherwise a died orchestrating thread leaves the topbar at `building 47/133` forever with no signal. Cap concurrent SSE streams at 4 with a 15 s heartbeat comment.

**Atomicity and resume.** Every output goes to `name.tmp-<pid>` in the **same directory** then `os.replace()` (with the §6.6 retry loop). `pano.json` is written **last**. An append-only `out/.run/<run_id>.jsonl` records one line per set (advisory only, never the source of truth). Orphan `.tmp-*` swept at the start of the next run. `--resume` skips sets whose sidecar is complete and whose keys still match. Worst-case loss from a power cut: the N in-flight sets.

**Build order:** duplicates skipped, then `sphere` first (they are what the user wants to see), then newest `captured_at` first. **The preview is the first slice of the single queue, not a separate phase** — compute `plan()` once, pop the first 5 `needs_*` spheres, stitch them, write their sidecars, then continue with the remainder. A separate preview pass re-stitches those 5 while the user watches, and shows a total of 135 rather than 130.

**Parallelism and RAM tiers** (`budget = 0.5 * psutil.virtual_memory().available`, re-checked between sets; allocate the largest buffers **up front and fail fast** rather than OOM-ing in band 14 of set 96):

| Total RAM | Master W | Band B | Workers | Peak/worker |
|---|---|---|---|---|
| ≥ 32 GB | 8192 (12288 opt-in) | 512 | min(12, cpu−2) | ~500 MB |
| 16 GB | 8192 | 256 | min(8, cpu−2) | ~380 MB |
| 8 GB | 4096 | 128 | 2 | ~180 MB |
| < 8 GB | 2048 | 128 | 1 | ~90 MB |

Any downgrade writes `resolution_reason: "auto_downgrade_ram_8gb"` so the user is never confused about why some panoramas are 4096 wide. Enforce a hard per-worker cap (700 MB at the 16 GB tier): abort that set, record it, continue.

**Source I/O discipline.** Never write outputs, caches or temp files to the source volume (`--allow-output-on-source` exists and warns). Default `out_dir` and `work_dir` on `C:` (`%LOCALAPPDATA%\pano\`). **One reader thread per physical source volume**, not per core — parallel reads from a single USB HDD reduce throughput and cause seek thrashing; parallelise decode and resampling across cores instead. Read each tile with one sequential whole-file read into memory, then decode from memory; never mmap over USB. Prefetch the next set (depth 1). If measured read throughput < 20 MB/s, **copy-first**: copy each set folder (~65 MB) to `work_dir`, process, delete — and record the decision so timings are interpretable. Preflight free space: refuse to start if free < 1.5× the estimated output.

Disk estimate: sphere ≈ 5.0 MB master + 1.9 MB pyramid/thumb + 3 MB RGBE ≈ 10 MB; non-sphere 2–4 MB; **total ≈ 1.2 GB** for 135 sets. Recommend 3 GB free. Source read is 7.4 GB once (~92 s at 80 MB/s, overlapped).

**Exit codes:** 0 = all ok/warn; 1 = at least one set failed/crashed/timed out; 2 = batch aborted (volume lost, disk full). Plus `report.csv` and a Health tab: every non-ok set with code, plain-language message, thumbnail, sortable — one screen the user can work through.

## 10.6 CLI

```
pano up [FOLDER]        # THE command: picker if omitted, preview + view + background build
pano doctor [--compare-exif]
pano scan [ROOT ...] [--json]
pano build [ROOT ...] [--out DIR] [--workers N] [--width 8192] [--quality 92]
           [--preview N] [--only ID ...] [--modes sphere,strip] [--include-duplicates]
           [--zenith-fill band|harmonic|flat|crop] [--compression 0.42] [--keep-linear]
           [--force | --force-derive | --force-id ID ...] [--retry-failed]
           [--dry-run] [--verify-content] [--exif-backend exiftool|builtin]
pano view [--out DIR] [--port 8777] [--no-browser] [--editable] [--basemap DIR]
pano check [--fix] [--unlock]
pano calibrate [--sets N]        # the one-time focal / distortion / mount-offset run (§11.1)
pano export <ID> --format equirect|cube|planet|video-orbit [...]
pano synth --from <equirect> --pattern sphere26 --out <dir>
pano relabel trip <id> "..."
pano vendor-three [--npm | --from <tgz|dir>]
```

`--include-duplicates` (default off) replaces `--exclude-duplicates` — the opt-in polarity contradicted the specified behaviour everywhere else, so an implementer reading only the CLI table would build all 135 sets. `--force` means *ignore cache keys* only; it no longer also means "build duplicates", and `--force-id` exists so a targeted rebuild does not imply a 12–20 minute run.

**Folder picking** — never a hand-edited config file. Four routes:

1. **Drag a folder onto `Pano - Add Folder.cmd`.** Document this first. The shim must quote:
   ```bat
   @echo off
   setlocal
   for %%A in (%*) do call :one "%%~A"
   goto :eof
   :one
   "%~dp0.venv\Scripts\python.exe" -m pano up "%~1"
   ```
   Bare `%*` splits on spaces (ten of the fourteen roots contain spaces) and cmd strips trailing whitespace from the command tail, so it **structurally cannot** carry the trailing-space root that holds 81 of the 135 sets. Defensive recovery in `__main__.py`: if `len(argv_paths) > 1` and `Path(" ".join(argv_paths)).is_dir()`, treat it as one path; if a path is not a dir, retry `path + " "` before erroring; then fall back to the picker with "could not read the dropped path, please pick the folder".
2. **`pano up` with no argument** → native Vista dialog via PowerShell `-NoProfile -STA` + `System.Windows.Forms.FolderBrowserDialog`, start folder `E:\`, result verified with `Path(out).is_dir()`. Fallback chain: PowerShell → `tkinter.filedialog.askdirectory` → typed prompt.
3. **In-viewer "Add folder…"** (`--editable`) → `POST /api/pick-folder`; the server raises the dialog on the desktop. PowerShell specifically, because tkinter must own the main thread while `ThreadingHTTPServer` is serving. Show an in-page "a folder dialog has opened on your desktop" notice and time out after 120 s — if the browser is fullscreen the dialog can appear behind it with no cue.
4. **A pinned Desktop shortcut** created by `Setup.cmd`.

**Setup must actually work.** `pyproject.toml` needs a real backend and entry point:

```toml
[build-system] requires = ["setuptools>=68"]
               build-backend = "setuptools.build_meta"
[project.scripts] pano = "pano.__main__:main"
```

`Setup.cmd`: locate `python` (no `py` launcher on this machine) → `python -m venv .venv` → `pip install -U pip -r requirements.txt` → **`pip install -e .`** → `pano vendor-three --npm` → `pano doctor` → offer a Desktop shortcut. Without the editable install, `Setup.cmd` dies on its own third step with `'pano' is not recognized`, and every documented `pano ...` invocation is unrunnable.

`vendor-three`: `npm pack three@0.171.0` — three.js publishes semver `0.171.0`, **not** a tag `r171`, so `npm pack three@r171` fails with ETARGET and leaves the viewer with no three.js. `VERSION` stores the npm semver; the display string `r171` is derived (`r{minor}`); `doctor` compares the normalised semver; `vendor_three.py` asserts the extracted set is exactly `package/build/three.module.js` + `package/build/three.core.js` and **fails loudly** otherwise (a silently-wrong vendor step produces a blank canvas with a module-resolution error that is easily misdiagnosed as a shader problem). If the directory is empty, `pano view` still starts and the page shows a panel with the three copy-paste commands — it never reaches for a CDN.

Dependencies: `numpy>=2.1,<3`, `pillow>=11,<14` (both already present at compatible versions). Optional: `opencv-python-headless>=4.10` (faster `remap`/`resize`, plus AKAZE for §9.2's fallback), `psutil`. Dev: `pytest`. Everything else is stdlib. No Flask/FastAPI, no click/typer, no scipy, no bundler, no npm at runtime.

## 10.7 JSON schemas

**`calibration.json`** (one-time, per camera body, shared by all sets):

```json
{
  "schema": "pano/calibration@1",
  "camera_model": "FC7303",
  "profile_key": "FC7303_2000x1500",
  "tile_size": [2000, 1500],
  "source": "measured_ncc_fixed_roi",
  "source_note": "GROUND_TRUTH.md section 1: 5 sets, 4 countries, 5 years; per-set HFOV 66.50/67.00/66.50/66.50/67.00; median 66.50, mean 66.70. SUPERSEDES any FOV derived from EXIF.",
  "focal_px": 1520.9,
  "hfov_deg": 66.647,
  "vfov_deg": 52.494,
  "dfov_deg": 78.856,
  "corner_half_angle_deg": 39.428,
  "principal_point_px": [999.5, 749.5],
  "focal_scale": 1.0,
  "roll_sign": -1,
  "roll_sign_source": "GROUND_TRUTH.md section 2: roll-scale sweep on the 9-tile set, unimodal bowl, minimum at exactly -1.00 (-1.25 0.0692 / -1.00 0.0651 / -0.75 0.0682 / 0.0 0.0842 / +1.00 0.1163); flat control sweep on the roll~0 sphere.",
  "distortion": {
    "model": "brown_radial", "enabled": false,
    "k1": 0.0, "k2": 0.0,
    "measured_k1": null, "measured_k2": null,
    "enable_threshold_abs_k1": 0.01,
    "method": "plumbline_horizon", "n_horizon_tiles": null, "residual_rms_px": null
  },
  "mount_offset_deg": { "yaw": 0.0, "pitch": 0.0, "roll": 0.0 },
  "mount_offset_source": null,
  "nadir_occlusion_mask": "profiles/fc7303_nadir_occlusion.png",
  "nadir_occlusion_status": "not_derived",
  "solved_utc": null
}
```

**`pano.json`** (per set; the viewer's contract):

```json
{
  "schema": "pano/set@1",
  "set_id": "100_0679-a3f19c",
  "set_key": "a3f19c4d2b",
  "content_id": "7c1e0b93f4a2d8e6",
  "label": "100_0679",
  "alias_of": null,
  "duplicate_of": null,
  "built_from_duplicate": null,

  "source": {
    "volume": { "key": "9C4A1E77", "basis": "serial", "label": "Backup4TB", "letter_at_scan": "E:" },
    "rel_path": "drone 2023/panorama/100_0679",
    "path_at_scan": "\\\\?\\E:\\Drone 2023\\PANORAMA\\100_0679",
    "path_flags": [],
    "online_at_build": true
  },

  "capture": {
    "camera_model": "FC7303",
    "camera_profile": "FC7303_2000x1500",
    "tile_count": 26,
    "tile_size": [2000, 1500],
    "captured_at_local": "2023-07-14T18:32:07",
    "captured_at_utc": "2023-07-14T22:32:07Z",
    "tz_source": "gps",
    "capture_duration_s": 41.2,
    "gps": { "lat": 51.477800, "lon": -0.001470, "alt_m": 375.2, "rel_alt_m": 98.4,
             "source": "median_of_tiles", "spread_m": 1.7, "tiles_with_gps": 26 },
    "yaw_reference": "drone_heading_uncalibrated",
    "magnetic_declination_deg": null,
    "xpcomment": "Type=P, Mode=P, DE=None"
  },

  "camera": {
    "focal_px": 1520.9, "cx": 999.5, "cy": 749.5,
    "hfov_deg": 66.647, "vfov_deg": 52.494, "corner_half_angle_deg": 39.428,
    "focal_scale_applied": 1.0,
    "distortion_applied": false, "k1": 0.0, "k2": 0.0
  },

  "classification": {
    "capture_mode": "sphere",
    "geometry": "full_sphere",
    "confidence": 0.98,
    "evidence": {
      "yaw_clusters_deg": [-146.39, -101.41, -56.41, -11.40, 33.61, 78.61, 123.59, 168.61],
      "yaw_cluster_counts": [3, 3, 5, 3, 3, 3, 3, 3],
      "yaw_step_deg": 45.00, "yaw_estimator": "circular_median",
      "yaw_span_centres_deg": 360.0, "yaw_full_circle": true,
      "az_span_deg": 360.0, "az_span_rule": "yaw_centre_extent_plus_hfov",
      "pitch_clusters_deg": [-89.89, -54.91, -19.92, -0.10, 14.86],
      "pitch_cluster_counts": [1, 8, 8, 1, 8],
      "pitch_step_deg": 34.88,
      "pitch_template": "sphere_3row", "pitch_template_residual_deg": 0.11,
      "has_nadir": true, "has_zenith": false, "horizon_extra": true,
      "max_abs_roll_deg": 0.40, "roll_significant": false, "roll_per_column": false,
      "roll_tags_present_all": true,
      "tiles_missing_angles": 0,
      "overlap_deg": { "yaw": 21.65, "pitch": 17.61 }
    },
    "warnings": ["zenith_cap_not_captured"]
  },

  "geometry": {
    "world_frame": "X=East, Y=Up, Z=North (LEFT-handed ordered basis; det(R) = -1 is CORRECT)",
    "camera_frame": "x=image right, y=image down, z=optical axis forward",
    "basis_formula": "f=[sin(yaw)cos(pitch),sin(pitch),cos(yaw)cos(pitch)]; rt=[cos(yaw),0,-sin(yaw)]; dn=cross(rt,f); rodrigues(rt,dn about f by -roll); R=cols[rt,dn,f]; v_world=R@v_cam",
    "roll_sign": -1,
    "angle_units": "degrees, coerced from exiftool STRINGS (strip leading '+') before use",
    "refinement": {
      "applied": false, "method": null,
      "pairs_possible": null, "pairs_used": null, "pairs_rejected": null,
      "max_delta_yaw_deg": null, "max_delta_pitch_deg": null, "roll_refined": false,
      "baseline_score": null, "refined_score": null, "accept_threshold": 1.005,
      "accepted": false, "rejected_reason": "disabled_by_default"
    }
  },

  "projection": {
    "type": "equirectangular",
    "image_width": 8192, "image_height": 4096,
    "px_per_deg": 22.7556,
    "theta_left_deg": -180.0,
    "phi_top_deg": 90.0,
    "az_center_deg": 0.0,
    "crop_mode": "full",
    "row0_is": "zenith",
    "flipY": false,
    "azimuth_of_column": "theta = wrap180(theta_left_deg + (i+0.5)/px_per_deg)",
    "elevation_of_row": "phi = phi_top_deg - (j+0.5)/px_per_deg",
    "azimuth_increases": "clockwise_from_north, left_to_right",
    "full_pano_width": 8192, "full_pano_height": 4096, "crop_left": 0, "crop_top": 0
  },

  "coverage": {
    "captured_azimuth_deg": [-180.0, 180.0],
    "azimuth_is_full_circle": true,
    "captured_elevation_deg": [-90.0, 41.24],
    "guaranteed_elevation_deg": [-90.0, 38.80],
    "boundary_is_scalloped": true, "scallop_lobes": 8,
    "boundary_min_deg": 38.80, "boundary_max_deg": 41.24, "boundary_mean_deg": 40.42,
    "boundary_islands": 0,
    "solid_angle_fraction_captured": 0.825,
    "pixel_fraction_captured": 0.725,
    "nadir_only_annulus_deg": 8.8,
    "nadir_ring_overlap_deg": 16.8,
    "synthetic_regions": [
      { "kind": "zenith_cap", "elevation_deg": [38.80, 90.0],
        "method": "band_median_percolumn+progressive_blur+trimmed_median_pole+grain",
        "seed_band_deg": [-3.0, -0.5], "sky_like_fraction": 0.94,
        "solid_angle_fraction": 0.175, "image_row_fraction": 0.275 }
    ],
    "holes_filled_px": 0, "inpainted_sr": 0.0,
    "nadir_occluded_frac": null, "shadow_masked_frac": null,
    "mask_file": "coverage_2048.png",
    "mask_semantics": { "255": "captured", "128": "synthetic_fill", "0": "no_data" }
  },

  "photometry": {
    "rule": "a = ExposureTime * ISO / FNumber^2 ; a_ref = min_k a_k ; g_k = a_ref/a_k",
    "a_ref": 0.0079719,
    "reference_tile": "DJI_0002.JPG",
    "reference_triple": { "exposure_time_s": 0.000625, "iso": 100, "fnumber": 2.8 },
    "exposure_spread_stops": 0.678,
    "transfer": "sRGB IEC61966-2.1; linearised before all scaling/resampling/blending",
    "linear_stats": { "p1": 0.00023, "p50": 0.00163, "p99": 0.684, "max": 1.149,
                      "dr_stops": 12.31, "population": "captured_unclipped_cos_el_weighted" },
    "tone_map": { "operator": "durand_guided",
                  "compression": 0.42, "detail_gain": 1.12,
                  "guided_radius_frac": 0.035, "guided_eps": 1e-3,
                  "base_resolution_divisor": 4,
                  "p995_target": 0.92, "highlight_knee": 0.8, "saturation": 0.92,
                  "output_stats": { "mean": 0.384, "p1": 0.155, "p99": 0.910, "clipped": 0.0 } },
    "dither": "tpdf_1lsb_unfiltered",
    "lut_max": 1.0,
    "residual_gain_solve": { "applied": false, "per_channel": true, "max_abs_ev": null,
                             "pairs_used": null, "clamped_tiles": [],
                             "seam_dlogl_before": null, "seam_dlogl_after": null,
                             "renormalisation_G": 1.0 }
  },

  "blending": {
    "method": "linear_feathered_hann_separable",
    "feather_px": { "u": 343, "v": 260 },
    "feather_fraction_of_overlap": 0.5,
    "overlap_px": { "u": 685.5, "v": 519.2 },
    "overlap_deg": { "u": 21.65, "v": 17.61 },
    "tile_edge_erosion_px": 4,
    "no_data_weight_threshold": 0.05,
    "clip_ramp_source_codes": [250, 255],
    "tile_priors": { "DJI_0005.JPG": 0.35 },
    "nadir_polar_ramp": "w *= clamp((25 - rho_deg)/15, 0, 1)",
    "fully_clipped_policy": "max_k(g_k * L_k)"
  },

  "tiles": [
    { "file": "DJI_0001.JPG", "bytes": 2219481, "mtime_ns": 1689362327000000000,
      "width": 2000, "height": 1500, "orientation": 1,
      "yaw_deg": -56.40, "pitch_deg": -0.10, "roll_deg": 0.00,
      "d_yaw_deg": 0.0, "d_pitch_deg": 0.0,
      "exposure_time_s": 0.000625, "iso": 100, "fnumber": 2.8,
      "a_k": 0.0079719, "g_k": 1.0000, "residual_gain_rgb": [1.0, 1.0, 1.0],
      "prior_weight": 1.0,
      "gps": [51.477800, -0.001470, 58.3],
      "datetime": "2023-07-14T18:32:07", "subsec": "12",
      "row": 3, "col": 2, "role": "horizon",
      "tags_present": ["GimbalYawDegree","GimbalPitchDegree","GimbalRollDegree",
                       "AbsoluteAltitude","RelativeAltitude"],
      "yaw_source": "gimbal", "angles_estimated": false,
      "tile_status": "ok", "valid_rows": 1500,
      "clipped_fraction": 0.004, "sharpness": 14.8,
      "contributed_px": 1712004 }
  ],

  "checks": [
    { "id": "CHK-COV",  "verdict": "pass", "value": 0.725, "unit": "pixel_fraction",
      "pass_range": [0.715, 0.735], "value2": 0.825, "unit2": "solid_angle_fraction" },
    { "id": "CHK-SEAM", "verdict": "pass", "value": 0.006, "unit": "mean_rel_dL",
      "value2": 1.08, "unit2": "grad_ratio" },
    { "id": "CHK-LOOP", "verdict": "pass", "value": 0.020, "unit": "deg_meta_maxdev",
      "value2": 0.21, "unit2": "deg_image_closure" },
    { "id": "CHK-OVL",  "verdict": "pass", "value": 0.91, "unit": "ncc_median",
      "value2": 0.09, "unit2": "stops_median", "pairs": 47 },
    { "id": "CHK-REG",  "verdict": "pass", "value": 0.19, "unit": "deg_median_greatcircle",
      "value2": 0.44, "unit2": "deg_p90", "value_px_at_master": 4.3 },
    { "id": "CHK-HOR",  "verdict": "n/a",  "reason": "coherent edge in only 41% of columns" },
    { "id": "CHK-SUN",  "verdict": "n/a",  "reason": "sun not visible in 2+ tiles" },
    { "id": "CHK-TONE", "verdict": "pass", "value": 0.384, "unit": "output_mean" }
  ],

  "quality": {
    "Q": 97, "band": "A", "needs_attention": false,
    "components": { "COV": 100, "REG": 97, "PHO": 93, "SEAM": 100,
                    "GHOST": 88, "SHARP": 94, "EXPO": 100 },
    "applicable_weight": 1.00, "penalty": 1.0,
    "top_deductions": [
      "ghosting on 4.0% of overlap area (moving water to the west)",
      "photometric spread 0.09 stops between sky and ground tiles"
    ]
  },

  "flags": ["I_ZENITH_FILLED", "W_GHOSTING"],

  "outputs": {
    "dir": "panos/100_0679-a3f19c",
    "master": { "path": "equirect_8192.jpg", "width": 8192, "height": 4096,
                "bytes": 5012884, "quality": 92, "subsampling": "4:4:4" },
    "levels": [
      { "kind": "thumb",   "path": "thumb_320.jpg",     "w": 320,  "h": 160,  "bytes": 14882 },
      { "kind": "level",   "path": "equirect_0512.jpg", "w": 512,  "h": 256,  "bytes": 31204 },
      { "kind": "level",   "path": "equirect_1024.jpg", "w": 1024, "h": 512,  "bytes": 103882 },
      { "kind": "level",   "path": "equirect_2048.jpg", "w": 2048, "h": 1024, "bytes": 372110 },
      { "kind": "level",   "path": "equirect_4096.jpg", "w": 4096, "h": 2048, "bytes": 1394027 },
      { "kind": "master",  "path": "equirect_8192.jpg", "w": 8192, "h": 4096, "bytes": 5012884 }
    ],
    "linear_tier": { "path": "linear_2048.rgbe.png", "w": 2048, "h": 1024,
                     "encoding": "rgbe_rgba8",
                     "decode": "rgb = texel.rgb * exp2(texel.a*255.0 - 136.0)" },
    "mask": "coverage_2048.png",
    "blur": { "w": 24, "h": 12, "source": "equirect_master",
              "data_uri": "data:image/jpeg;base64,/9j/4AAQ..." },
    "cubemap": null,
    "linear_master_npy": null,
    "total_bytes": 9928500
  },

  "cache": {
    "ingest_key": "a5f1c209d48b0e77...",
    "stitch_key": "9c02af71e3d5b840...",
    "derive_key": "771e4b0a9c8d2f31...",
    "key_inputs_version": 1,
    "pipeline_version": "stitch/1",
    "exif_backend": "exiftool", "exif_reader_version": 1,
    "content_key": null, "content_verified": false,
    "id_key_version": 1,
    "attempts": 1
  },

  "grouping": { "trip_id": "trip_0003", "location_id": "loc_5a1c3f02", "sequence_in_trip": 4 },

  "viewer_hints": {
    "default_mode": "immersive",
    "initial_heading_deg": 168.6, "initial_pitch_deg": -10.0,
    "initial_vfov_deg": 75.0, "min_vfov_deg": 25.0, "max_vfov_deg": 110.0,
    "clamp_pitch_deg": [-85.0, 85.0],
    "soft_clamp_pitch_above_deg": 38.80,
    "little_planet_rho_edge_deg": 108.9, "little_planet_rho_edge_max_deg": 111.9,
    "globe_mirror_default": false, "globe_orientation": "planet_down",
    "allow_full_rotation": true, "letterbox_required": false,
    "gates": { "full_sphere": true, "wide_patch": true },
    "modes_enabled": ["immersive","globe","little_planet","tunnel","flat","pannini",
                      "stereo_wide","mirror_ball","cube_cross","coverage","vr"],
    "modes_disabled": {}
  },

  "perf": { "elapsed_s": 47.1, "stage_s": { "ingest": 0.4, "probe": 0.6, "render": 46.8,
                                            "zenith": 0.3, "tonemap": 9.4, "export": 3.1 },
            "decodes": 31, "peak_rss_mb": 381, "bytes_read": 66412188, "read_mb_s": 81.3,
            "backend": "numpy" },

  "status": "ok",
  "error": null,
  "warnings": ["zenith_cap_synthetic", "nadir_tile_may_show_aircraft"],
  "built_at": "2026-09-29T14:07:53-04:00",
  "generator": { "name": "pano", "version": "1.0.0" }
}
```

Failure form:

```json
{
  "schema": "pano/set@1", "set_id": "100_0691-ab41c0", "status": "failed",
  "error": { "stage": "stitch", "kind": "TileDecodeError",
             "message": "OSError: image file is truncated (8 bytes not processed)",
             "file": "DJI_0017.JPG", "traceback_path": ".run/err/100_0691-ab41c0.txt",
             "at": "2026-09-29T14:09:31-04:00", "retryable": false, "attempts": 1 },
  "cache": { "ingest_key": "...", "stitch_key": null, "derive_key": null,
             "key_inputs_version": 1, "attempts": 1 },
  "outputs": { "dir": "panos/100_0691-ab41c0", "levels": [] },
  "quality": { "Q": null, "band": "F", "needs_attention": true },
  "flags": ["E_TILE_CORRUPT"]
}
```

**`library.json`** — the only Python↔JS contract. Top level plus a `panoramas[]` of *summaries*:

```json
{
  "schema": "pano/library@1",
  "generated_at": "2026-09-29T14:07:53-04:00",
  "generator": { "name": "pano", "version": "1.0.0", "pipeline_version": "stitch/1" },
  "library_root": "C:/Users/rzram/Claude-DroneImages-Pano/out",
  "defaults": {
    "master_width": 8192, "px_per_deg": 22.7556,
    "level_widths": [512, 1024, 2048, 4096, 8192],
    "jpeg_quality_by_level_from_top": [92, 88, 88, 82, 82],
    "camera": { "profile": "FC7303_2000x1500", "label": "DJI Mavic Air 2",
                "focal_px": 1520.9, "hfov_deg": 66.647, "vfov_deg": 52.494,
                "source": "measured (GROUND_TRUTH section 1); NOT derived from EXIF" }
  },
  "counts": { "sets_found": 135, "ok": 0, "ok_partial": 0, "queued": 135,
              "stitching": 0, "failed": 0, "skipped": 0, "source_offline": 0,
              "duplicates_flagged": 0 },
  "build": { "run_id": "run_20260929T140211", "state": "running",
             "done": 0, "total": 135, "elapsed_s": 21, "eta_s": 780, "workers": 8 },
  "volumes": [ { "key": "9C4A1E77", "basis": "serial", "label": "Backup4TB",
                 "letter_at_scan": "E:", "online": true } ],
  "sources": [ { "id": "src_2f1b8c04", "volume_key": "9C4A1E77",
                 "path": "E:\\Drone 2023\\PANORAMA", "label": "Drone 2023",
                 "set_count": 13, "online": true,
                 "scanned_at": "2026-09-29T14:02:14-04:00", "notes": [] } ],
  "trips": [ { "id": "trip_0004", "label": "India 2023", "label_source": "path",
               "source_ids": ["src_7b04c1de","src_9ac30f11","src_44b2e810",
                              "src_1d07fa63","src_bb51c209"],
               "started_at": "2023-11-02T07:41:12", "ended_at": "2023-11-19T17:03:44",
               "pano_count": 81, "cover_pano": null,
               "bbox": { "lat": [51.4700, 51.4850], "lon": [-0.0100, 0.0050] },
               "centroid": { "lat": 51.4775, "lon": -0.0025 },
               "location_ids": ["loc_5a1c3f02"],
               "rule": { "time_gap_h": 36, "distance_km": 25, "merged_across_sources": true } } ],
  "locations": [ { "id": "loc_5a1c3f02", "label": "51.478, -0.001", "label_source": "auto",
                   "centroid": { "lat": 51.4778, "lon": -0.0015 }, "radius_m": 143,
                   "pano_count": 3, "trip_ids": ["trip_0003"],
                   "first_at": "2021-08-14T00:00:00", "last_at": "2026-08-03T00:00:00" } ],
  "clusters": [ { "id": "c07", "label": "Lake Mendota north shore",
                  "center": [51.4778, -0.0015], "radius_m": 143,
                  "sets": ["100_0056-b71e0c","100_0679-a3f19c","100_0114-4c8e10"],
                  "dates": ["2021-08-14","2023-06-14","2026-08-03"] } ],
  "tour_edges": [ { "a": "100_0679-a3f19c", "b": "100_0681-1f2e9d",
                    "distance_m": 184.2, "bearing_deg": 57.3,
                    "elevation_to_camera_deg": -3.7, "elevation_to_ground_deg": -28.1 } ],
  "panoramas": [
    { "id": "100_0679-a3f19c", "label": "100_0679",
      "detail": "panos/100_0679-a3f19c/pano.json",
      "source_id": "src_2f1b8c04", "source_path": "E:\\Drone 2023\\PANORAMA\\100_0679",
      "capture_mode": "sphere", "geometry": "full_sphere", "confidence": 0.98,
      "tile_count": 26,
      "captured_at_local": "2023-07-14T18:32:07", "captured_at_utc": "2023-07-14T22:32:07Z",
      "gps": { "lat": 51.477800, "lon": -0.001470, "alt_m": 375.2, "rel_alt_m": 98.4 },
      "projection": { "image_width": 8192, "image_height": 4096, "px_per_deg": 22.7556,
                      "theta_left_deg": -180.0, "phi_top_deg": 90.0, "az_center_deg": 0.0,
                      "row0_is": "zenith", "flipY": false },
      "coverage": { "lon_span_deg": 360.0, "lat_span_deg": 180.0,
                    "guaranteed_elevation_deg": [-90.0, 38.80],
                    "solid_angle_fraction_captured": 0.825,
                    "synthetic_fraction": 0.175, "mask_file": "coverage_2048.png" },
      "levels": [ { "w": 512, "h": 256, "path": "equirect_0512.jpg", "bytes": 31204 } ],
      "linear_tier": "linear_2048.rgbe.png",
      "thumb": "thumb_320.jpg",
      "blur": "data:image/jpeg;base64,/9j/4AAQ...",
      "gates": { "full_sphere": true, "wide_patch": true },
      "viewer_hints": { "default_mode": "immersive", "initial_heading_deg": 168.6,
                        "initial_pitch_deg": -10.0, "initial_vfov_deg": 75.0,
                        "little_planet_rho_edge_deg": 108.9 },
      "trip_id": "trip_0003", "location_id": "loc_5a1c3f02", "sequence_in_trip": 4,
      "duplicate_of": null, "alias_of": null,
      "cache_key": "9c02af71e3d5b840...",
      "quality": { "Q": 97, "band": "A", "needs_attention": false },
      "status": "ok", "error": null,
      "warnings": ["zenith_cap_synthetic"], "flags": ["I_ZENITH_FILLED"],
      "built_at": "2026-09-29T14:07:53-04:00" }
  ]
}
```

`status` enum: `ok | ok_partial | queued | stitching | failed | failed_sticky | cancelled | source_offline | skipped`. The `counts` histogram is **exhaustive and disjoint over the status enum** and must sum to `sets_found`, asserted at reduce time (`duplicates_flagged` is an overlapping tally, documented as such). Summary-only keeps the file around 300 KB including the 135 inline blur URIs — instant from localhost, and never invalidated by a per-pano rebuild.

**Level widths for cropped modes** derive from the canvas, not from the sphere ladder, and quality is keyed by **level index from the top**, not by width (a width-keyed map has no entry for 5616/2808/1404, and the fallback chosen would silently become part of `derive_key`). Ladder predicate: halve while `max(w, h) >= 256`, listed small-to-large. For the 7-tile strip (5616×1200): `[702×150, 1404×300, 2808×600, 5616×1200]`. For the 9-tile wide (3936×2448): `[492×306, 984×612, 1968×1224, 3936×2448]`. For the 4-tile column (3424×2784): `[428×348, 856×696, 1712×1392, 3424×2784]`. A self-test asserts each fixture's ladder equals the generator's output — the dimension specs' worked examples violated their own stated predicate in both directions.

**`overrides.json`** (user edits, merged at reduce time, never overwritten by a build): `{schema, updated_at, trips:{<id>:{label, cover_pano, hidden}}, locations:{<id>:{label}}, panoramas:{<id>:{label, favorite, hidden, tags[], initial_view:{mode, lon_deg, lat_deg, vfov_deg, roll_deg}}}}`.

## 10.8 Grouping

**Locations** — greedy leader clustering, time-ordered, haversine, radius **500 m**: each pano joins the first cluster whose running-mean centroid is within 500 m, else starts one. No-GPS panos go to a synthetic `loc_nogps`. `radius_m` = max member distance from the final centroid.

**Trips** — connected components over time-consecutive panos, edge when `Δt ≤ 36 h` **OR** (`Δd ≤ 25 km` **AND** `Δt ≤ 14 d`). Components **must be allowed to span source roots**: GROUND_TRUTH shows `India-2023-Media\Drone-SD-1..4` is one trip across four SD cards (81 sets over 5 roots) and Scotland is one trip across three date-named roots (16 sets). Per-root grouping would produce 14 meaningless trips. Label = the longest alphabetic token common to the component's source-root paths after stripping digits, dots, and the stopwords `Drone DCIM PANORAMA BU SD Media Review Mix Unknown`; else the majority root's most distinctive component; else `"<Mon YYYY> – <Mon YYYY>"`.

Acceptance test, phrased so it cannot fail for a legitimate reason: **assert the India sets land in ≤ 3 trips and that no trip is split by source root** — rather than asserting an exact count of 8, which depends on timestamps nobody has seen (Jaipur/Mumbai/Delhi are > 25 km apart and may exceed a 36 h gap between card sessions).

`haversine` with `R = 6 371 008.8 m`; bearing `θ = (deg(atan2(sin Δλ cos φ₂, cos φ₁ sin φ₂ − sin φ₁ cos φ₂ cos Δλ)) + 360) mod 360`.

**GPS hygiene:** treat `|lat| < 0.001 and |lon| < 0.001` as no-fix (the classic 0,0 trap); flag `W_GPS_SUSPECT` if lat/lon varies > 1 km within one set (a 41 s hover cannot move 1 km) or `GPSAltitude == 0` while `RelativeAltitude > 20`. GPS is display-only; `gps: null` still stitches and does not affect `Q`.

**Timezones:** `DateTimeOriginal` is naive local with no offset and the archive spans four countries. Prefer `GPSDateStamp`+`GPSTimeStamp` (UTC) when present; otherwise derive from lat/lon and set `tz_source: "naive_local"` and say so in the UI. Never sort the whole archive chronologically and claim correctness.

**Magnetic declination:** `GimbalYawDegree` references the drone's compass. Declination spans ~0° (India, Wisconsin) to ~−25° (South Africa). This does **not** affect the stitch, only a "north" overlay, the map wedge and the sun overlay. `yaw_reference: "drone_heading_uncalibrated"`; offer a WMM correction for display only; **never validate yaw against a map** and never flag a constant yaw offset as an error.

---

# 11. Validation, QA and the quality score

## 11.1 Checks

Every check returns **`pass` / `fail` / `n/a`**, and an `n/a` never degrades a panorama's score or status. Across 135 sets spanning four countries there are single-tile sets, sun-free sets, horizon-free forest sets and strips with no pole; a binary scheme would mark most of the archive red for reasons that are not defects.

**Blocking checks** (a `fail` sets `status: "failed"` — output still written, marked `suspect: true` with the failing check named — and applies the `Q` penalty): `CHK-COV`, `CHK-SEAM`, `CHK-LOOP(b)`, `CHK-OVL`, `CHK-REG`, `CHK-TONE`. **Diagnostic checks** (`CHK-HOR`, `CHK-SUN`, `CHK-DET`) report only and can never move `Q` — the original left `CHK-SUN` and `CHK-HOR` out of the action table while `Q` applied a 0.70 multiplicative penalty for "any check fails", so a false positive on a P2 diagnostic silently cost 30 % of the score.

**CHK-COV** — rasterise the coverage count at 1024×512, integrate solid angle `dΩ = (2π/W')(π/H')cos φ`. Self-test the integrator: total over the full grid = `4π ± 0.1 %`.
For `sphere`: **pixel fraction covered in [0.715, 0.735]** (GROUND_TRUTH §9 measured 0.725), **solid-angle fraction in [0.818, 0.832]**, boundary min in [38.3, 39.5], boundary max in [40.7, 41.8], dominant nonzero FFT harmonic of `b(az)` = **k=8**, and **exactly zero interior holes below the boundary** (GROUND_TRUTH §9: "below the cap there are NO holes, gaps or slivers anywhere"). Fail → `E_COVERAGE_ANOMALY` with each hole's centroid and solid angle. A hole where geometry says there must be none means a tile was mis-placed — this catches a convention error before a human looks at a picture. (Every asserted range here is at the measured FOV; the dimension specs' ranges — 0.830–0.846 covered, boundary 40.3–41.5 / 43.0–43.7 — **exclude the truth and would fail on the primary fixture on day one.** Corrected.)

**CHK-SEAM** — columns `0` and `W−1` are `360/W = 0.044°` apart and must be nearly identical. Mean `|ΔL|/L` over covered rows ≤ 0.02; NCC ≥ 0.98; median `|dI/dx|` at the seam vs the global median ≤ 1.5. Fail → `E_SEAM_BROKEN`: almost always mixed `[0,360)`/`[−180,180)` conventions or a non-wrapping accumulator index (§3.4). Microseconds, catches a whole bug class.

**CHK-LOOP** — (a) *metadata*: wrapped consecutive differences of the **median**-estimated yaw cluster centres. Measured: `[44.98,45.01,45.00,45.01,45.00,44.98,45.02,45.00]`, sum 360.00, max deviation **0.020°**. Pass ≤ 0.5°, warn ≤ 3.0°. (The estimator is pinned: the mean gives 0.074° and top-row-only gives 0.23°; all pass, but a golden asserting 0.020 would break on an estimator change for no reason. Golden tolerance ±0.1°.) (b) *image-measured*: chain the measured horizontal shifts from CHK-REG around the 8 columns in a ±10° band about the horizon; the accumulation must return to zero. Pass ≤ 0.5°, warn ≤ 1.5°. This is the only test that catches a yaw **scale** error (0.5 % scale → ~1.6° closure), which pairwise checks cannot see. `n/a` for non-full-circle modes.

**CHK-OVL** — per adjacent pair with predicted overlap > 1 % of tile area: reproject both, intersect, **erode 8 px**, exposure-normalise, decimate to ≤ 200 k samples. Zero-mean NCC on luminance: pass ≥ 0.85, warn 0.70–0.85, fail < 0.70. Median `|Δlog₂ L|`: pass < 0.15 stops, warn < 0.35, fail ≥ 0.35. Exclude blocks flagged `churn` (§11.4) so the sea is not punished.

**Sign-flip coverage — why CHK-OVL/REG suffice:** negating all yaws reverses the columns' *placement* without mirroring their *content*, so every pair abuts the wrong edges and CHK-OVL fails hard. Negating pitch swaps row order. Negating roll only shows up on a set with nonzero roll — which is exactly why `100_0691` is a mandatory fixture.

**CHK-REG** — within each eroded overlap, 12 patches of 129×129 at highest local gradient energy, subpixel phase correlation, **measured in a local gnomonic frame tangent at the overlap centroid** (not in equirect pixels), converting px → deg with `f_px = 1520.9`.

```
Report the GREAT-CIRCLE residual:  d_angle = sqrt((d_theta * cos(phi))^2 + d_phi^2)
   Median   pass <= 0.25 deg   warn <= 0.75   fail > 0.75
   p90      pass <= 0.60 deg   warn <= 1.50   fail > 1.50
Report per-latitude-band medians alongside the aggregate, so a pole-only problem is visible.
```

`deg = px·360/W` is a **longitude** increment, not an angle; away from the equator it inflates the residual by `1/cos φ` — 1.74× at the −54.9° row, 6.5× at the −81° row — so every sphere set would breach the thresholds for purely projective reasons, and a 129×129 equirect patch at −81° is 6.4:1 anisotropic so the correlation is dominated by the projection's stretch. **Corrected.** Thresholds are calibrated against measured reality: GROUND_TRUTH shows intra-column yaw scatter up to 0.17° (`−146.22` vs `−146.39`; `+168.74` vs `+168.61`), and the entrance pupil sits centimetres off the gimbal rotation centre so near-field parallax is irreducible for a rotation-only model. 0.25° = 6.6 tile px on axis, 5.7 px at the 8192 master.

**CHK-HOR** — diagnostic. Per covered column, find the row of max vertical luminance gradient within ±6° of `r_horizon = H/2 − 0.5`; fit **constant + k=1 harmonic + k=N_columns harmonic**.

```
Subtract the HORIZON DIP before reporting any pitch bias:
    dip_deg = 57.296 * sqrt(2*h / 6371000)      h = RelativeAltitude (AbsoluteAltitude over water)
    at h = 97 m this is 0.317 deg BELOW the level line, with the same sign on every set.
Attribution:
    constant (after dip removal) -> camera-side pitch offset
    k=1 amplitude/phase          -> world-side levelness error      pass amplitude <= 0.3 deg
    k=N_columns amplitude        -> camera-side ROLL offset, phi = excursion / tan(HFOV/2)
                                    pass <= 0.2 deg (i.e. a roll offset of ~0.3 deg)
Gate: run only if a coherent edge is found in >= 60% of columns, else n/a.
Aggregate levelness across the archive ONLY over sets whose detected horizon is consistent
with a SEA horizon (near-constant row across >= 80% of columns, within 0.5 deg of the predicted
dip). A terrain skyline must not vote.
```

Two corrections: the dip is the same size as the tolerance and would otherwise be reported as a third of the "expect < 1°" gimbal-bias budget and then fed into calibration as a mount offset; and a constant camera-side roll error does **not** produce a 1-cycle sinusoid — it tilts the horizon within each tile's own azimuth span, giving a sawtooth with one lobe per yaw column (k=8 on a sphere, excursion ≈ `roll·tan(HFOV/2) = 0.66·roll`), which a constant+1-cycle fit cannot represent and partly absorbs into its residual. Since roll is ~0 on the 107 sphere sets, the k=8 term is the only witness there to a roll convention error.

**CHK-SUN** — diagnostic. The sun is at infinity, so if visible in 2+ tiles its reprojected centroid must land at the same `(az, el)` from every tile. **Use the ephemeris** (GPS + each tile's own `DateTimeOriginal`) as the reference rather than comparing tiles to each other — that removes the solar-motion term (0.25°/min × a 41 s spread = up to 0.17°, over half a 0.3° budget) and additionally validates `yaw_reference` against true north for the first time, which across four countries with declinations 0° to −25° settles the magnetic-vs-true question empirically. Estimate the centroid from a fixed **iso-radiance contour** (0.5 of peak) after exposure normalisation, not from the saturation blob (whose radius depends on the 0.678-stop exposure spread). Reject blobs touching the frame edge; require area > 200 px in both tiles. **Pass ≤ 0.6°, warn ≤ 1.2°.** `n/a` otherwise.

Low-precision Meeus is ample (±0.01°):

```
n   = JD_UTC - 2451545.0
L   = (280.460 + 0.9856474*n) mod 360 ;  g = (357.528 + 0.9856003*n) mod 360
le  = L + 1.915*sin(g) + 0.020*sin(2g) ;  eps = 23.439 - 0.0000004*n
RA  = atan2(cos(eps)*sin(le), cos(le)) ;  Dec = asin(sin(eps)*sin(le))
GMST= (18.697374558 + 24.06570982441908*n) mod 24 ;  LMST = (GMST + lon/15) mod 24
HA  = LMST*15 - deg(RA)
alt = asin(sin(phi)sin(Dec) + cos(phi)cos(Dec)cos(HA))
az  = atan2(-cos(Dec)sin(HA), sin(Dec)cos(phi) - cos(Dec)sin(phi)cos(HA))   # true N, clockwise
```

**CHK-TONE** — assert the tone-mapped output matches GROUND_TRUTH §7's verified signature at `compression = 0.42`: `mean 0.384 ± 0.03`, `p1 0.155 ± 0.03`, `p99 0.910 ± 0.02`, no clipping. Scale the expectation with the chosen compression.

**CHK-DET** — stitch the same fixture twice, assert byte-identical output. Requires fixed RNG seeds (including the §4.6 dither and §5.2 grain, which must be seeded from `set_id`) and a deterministic reduction order.

**CHK-XV** — the decisive cross-validation, three fixtures only, CI-gated. Run OpenCV SIFT + rotation-only bundle adjustment and compare recovered per-tile rotations to the metadata rotations.

```
MANDATORY gauge alignment first: rotation-only BA recovers rotations only up to a single global
rotation (OpenCV fixes the reference image's R to identity). Solve
    R_g = argmin_{R in SO(3)}  sum_i d_geo(R_g @ R_meta_i, R_sfm_i)     (SVD Procrustes)
THEN assert  max_i d_geo(R_g @ R_meta_i, R_sfm_i) <= 0.5 deg,
and report R_g separately as an independent estimate of the MOUNT OFFSET
(cross-checked against CHK-HOR, never against CHK-CAL).
Also assert the recovered focal is within 1.5% of 1520.9.
```

Without the alignment the raw geodesic difference equals roughly the reference tile's own metadata rotation — on `100_0679`, a yaw of −56.4° — so the "decisive gate" reports tens of degrees on a perfectly correct pipeline and blocks every release. **Corrected.** What this proves and no per-set check can: **no single rotation `R_g` can absorb a reflection**, so a mirrored or wrong-handed convention shows up as an irreducible residual rather than being soaked up by the alignment. A `fail` here is a code bug, not a data problem: **block the release.**

**CHK-CAL** — optional archive-wide refinement, `pano calibrate`. Fit `(f_px, cx, cy, k1, Δroll)` over many sphere sets.

```
The OBJECTIVE IS PRESCRIBED by GROUND_TRUTH §1 and the alternative is FORBIDDEN:
  - Evaluate the residual over a FIXED region of interest, defined independently of the
    current parameter estimate, and normalise by the sample count inside that fixed ROI.
  - Score with CONTRAST-NORMALISED CROSS-CORRELATION on the exact pure-rotation homography
    H = K (R2^T R1) K^-1.
  - NEVER minimise mean absolute difference over "whatever overlaps": that metric is biased
    toward small FOV (shrinking the FOV shrinks the overlap toward tile centres where
    alignment is intrinsically easier), decreases MONOTONICALLY, and is NOT a valid estimator.
  - REQUIRE each per-set objective curve to be UNIMODAL WITH AN INTERIOR PEAK, as GROUND_TRUTH
    observed on all 5 of its sets. Abort the fit if any curve is monotonic - that is the
    signature of the invalid metric.
Sanity gate: f_px within 2% of 1520.9.
Conditioning gate, NOT a holdout gate: report the Jacobian condition number and per-parameter
standard errors; FAIL if the cx-dyaw correlation exceeds 0.95 (they differ only at second order
in r/f_px - 1 px = 0.0377 deg - so a 70/30 holdout on total residual keeps both residuals small
while the individual parameter values are meaningless).
Absolute level/heading offset comes from CHK-HOR, not from here: overlap disagreement is
invariant to any rotation applied on the world side, so it is unidentifiable from this objective
no matter how many sets are added.
Draw calibration sets from DIFFERENT roots, dates and locations, so a shared compass bias from
one session is not absorbed into the geometry.
```

The dimension spec's CHK-CAL minimised total overlap disagreement with `f_px` free — **exactly the estimator GROUND_TRUTH forbids** — and expected `f_px` "within a few percent of 1386.750", a gate that would reject the correct answer. It would have driven `f_px` away from the truth archive-wide and then frozen the result. **Corrected.**

## 11.2 Tests

| # | Test | Assertion |
|---|---|---|
| **T1** | **Round-trip through a known equirect.** Build a synthetic 4096×2048 equirect with (a) a 1° graticule, (b) band-limited noise, (c) a hard step edge exactly at `φ = 0`, (d) 8 unique fiducial glyphs at known `(az, el)` including one at `az = +180` and one at `el = −85`. Resample into 26 fake tiles **at the exact 26 measured angles of `100_0679`**, stitch, compare. **The test's forward model must be written independently in the test file** (explicit math + `map_coordinates`), never by calling the production projection — otherwise you only prove the code agrees with itself. | fiducial centroid error median ≤ **0.01°**, max ≤ 0.04° (tight enough to catch a half-pixel sampling offset, which the original 0.03° tolerance could not); smooth+edge layers PSNR ≥ 45 dB / SSIM ≥ 0.97 excluding a 3 px band at tile boundaries; noise layer PSNR ≥ 34 dB, p99 abs error ≤ 6/255; recovered horizon edge within 0.05° of `H/2 − 0.5` at every column, sinusoid amplitude ≤ 0.02°; pixel coverage fraction 0.725 ± 0.008; boundary in [38.3, 41.8] for every azimuth with dominant harmonic k=8; zero interior holes; **zero NaN/Inf** anywhere |
| **T2** | **Convention regression** (not discovery — GROUND_TRUTH §2 measured the answer). Enumerate the 32 sign/structure variants of the *actual* §2.2 construction (sign of yaw, pitch, roll; `rt` vs `−rt`; `dn = cross(rt,f)` vs `cross(f,rt)`; Rodrigues by `+roll` vs `−roll`). | On a roll≈0 fixture, the passing set is **exactly** the equivalence class that is degenerate when roll = 0 (roll sign free); every other variant fails by > 5° on fiducial position. Then a **synthetic T1 case with injected nonzero roll** (reuse `100_0691`'s −11.5/0/+11.5) pins the roll axis and sign against the test's independent forward model. `100_0691`'s CHK-OVL run is the physical confirmation. *(The original "exactly one of 48 passes" is provably false: with roll = 0 the roll sign is unobservable and the 6 orderings collapse to 2 products, so 48 variants reduce to 8 equivalence classes of 6 — six variants pass identically, and the gate could never pass.)* |
| T3 | Seam / wrap. Single synthetic tile centred at `az ∈ {179.9, 180.0, −180.0, 180.1}`. | footprint splits across both edges; total footprint area equals the unwrapped area within 0.1 %; **no duplicated and no missing column**; CHK-SEAM passes; and the §3.4 flat-index remap is exercised (a test with `n_slice != W`) |
| T4 | Pole. Single tile at `pitch = −90` with `roll ∈ {0, 37°}`. | bottom equirect row fully filled; footprint is a closed region containing the pole; the **exact pole-containment test fires** and clamps `el_lo` to −90; adjacent columns at row `H−1` differ by less than the noise floor; the inverse map is finite and in range at `φ = ±89.999` and exactly `±90.0` |
| T5 | Solid-angle integrator | sum of `dΩ` = `4π ± 0.1 %` at 512×256, 1024×512, 2048×1024 |
| T6 | Analytic exposure normalisation. Two tiles from one equirect at 1/1600 and 1/1000, ISO 100, f/2.8. | recovered linear ratio within 1 % of 1.6; `|median(A) − median(B)| / median ≤ 0.01` in the overlap; `a_ref` equals the recorded rule applied to the recorded reference triple |
| T7 | Metadata parsing, table-driven, no images (~30 cases) | each produces a defined result and **never raises**: `"+14.85"`, `"-0.00"`, `"+0.00"` vs absent roll, a bare number, empty string, decimal comma, `pitch = -181`, yaw given as 0..360, `GimbalPitch` present but `FlightPitch` absent, a scalar where a list is expected, JSON with a BOM, non-ASCII filename, **a path with a trailing space**, exiftool exit ≠ 0, exiftool `Warning` alongside valid tags |
| T8 | Corrupt JPEG corpus: truncated at 50/95/99.9 %, byte-flips in the scan, zero-length, a PNG renamed `.JPG`, EXIF-only. Plus a **real sun-in-frame tile**. | each detected at scan time with the right `tile_status`; the set still stitches; the manifest records it; batch exit code reflects *warn*, not crash; the sun tile asserts `tile_status == "ok"`; a good tile decoded **concurrently** with a truncated one is still reported `ok` |
| T9 | CHK-DET on two fixtures | byte-identical |
| T10 | Golden regression: 512×256 downsamples of the fixture outputs plus their manifests in the repo (~100 KB each) | any change beyond PSNR 45 dB or `Q ± 1` must be acknowledged by explicitly updating the golden. **Goldens are generated in CI from the reference implementation, never hand-written** — the dimension spec's own schema example did not satisfy its own `Q` formula |
| T11 | Linear-light blend: 0/255 checkerboard downsampled 2× | encodes to **188 ± 1**, not 128 |
| T12 | **Path and constant lint** (static) | no `.strip()` near a path variable; no `TILE_W`/`TILE_H` constant; no read of `Composite:FOV`; no read of `Flight{Pitch,Roll}Degree` in the projection module; every file open goes through `win_long()`; no arithmetic or comparison on an un-coerced exiftool tag value; no hard-coded `360`/`180` divided by `W`/`H` outside `projection.py` |
| T13 | Decode-count budget | decodes per set ≤ 1.35 × tile count on the 26-tile fixture (guards a band loop that silently re-decodes every tile for every band — a 16× slowdown no correctness test would catch) |
| T14 | Resume idempotence | kill mid-set, resume, final manifest equals the uninterrupted manifest. Plus: build 13 sets from one root, then a different root, and assert `library.json` contains **both** (the reduce is a union of on-disk sidecars and the current plan, never a wholesale regeneration — otherwise `pano up "E:\Drone 2023\PANORAMA"` drops the other 122 panos, and opening with the drive unplugged destroys the library) |
| **T15** | **Refinement sign** (§9.3) | inject `Δyaw = +1.0°` into one tile's estimate, one GN step, recover `−1.0° ± 0.1°` — not `+1.0°`, not `−2.0°` |
| T16 | Cache key stability | build `TileMeta` from a fixture through both EXIF backends → byte-identical `ingest_key`; perturb `f_px` by 0.01 → every `stitch_key` changes; toggle `--verify-content` → no `stitch_key` changes; change `backend` → no key changes |
| T17 | Handedness and basis | `cross(r̂, d̂) ≈ f̂` for every `(r̂,d̂,f̂)` triple in §2.3 and §8.3 (frame V); `det(R) == −1` for §2.2 (frame W); all six cube faces satisfy the rule and all four side faces share `d̂ = Down` |
| T18 | Missing-nadir sphere: `100_0679` with `DJI_0005.JPG` withheld | `capture_mode == "sphere"`, `W_NADIR_MISSING` present, `hole_solid_angle_sr ≈ 0.075`, `COV < 95` (see §11.3) |
| T19 | Server smoke test | GET `/`, extract every `src`/`href`/import-map target, assert each returns 200 |
| T20 | Ladder generator | each fixture's level ladder equals the generator's output; the §4.5 feather table regenerates from the formula at import time |
| T21 | Little-planet inverse map | evaluate the **inverse** (`ρ = 2·atan(r/2)` for the shader's law, `azimuth = atan2(dy,dx)` with `r = 0` special-cased to azimuth 0) at `r = 0`, at the frame corners, and along the whole diagonal: finite, in range, monotonic `ρ`. *(The original specified guarding `atan2(0,0)` and `1/tan(θ)` at `θ = ±90` — but stereographic-from-nadir has no `1/tan` term, is zero at the nadir, and diverges only at the antipode, so the guard protected the wrong place and the test could not be satisfied by correct code.)* |

**Fixtures — the specific real sets, and why each earns its place:**

| Set | Role |
|---|---|
| `E:\Drone 2023\PANORAMA\100_0679` | **Primary.** All 26 `(yaw, pitch)` values are enumerated in GROUND_TRUTH, so the test asserts the *parsed* angles against a literal table — this alone validates the whole exiftool path including the string-coercion trap. Contains the nadir tile, the horizon tile, the zenith hole, and a 0.678-stop spread. It is also the Wisconsin **sunset** sphere, so it is the HDR and clipping fixture. Drives T1, T2, CHK-COV, CHK-LOOP, CHK-TONE, CHK-XV. |
| `E:\Drone 2023\PANORAMA\100_0690` | 7-tile strip, single row, all pitch ≈ 0, **30°** yaw spacing — proves the spacing is *inferred*, not assumed to be 45. All tiles straddle the horizon → the canonical CHK-HOR fixture. Also the cropped-canvas, `theta_left`, GPano-crop and seam-straddling fixture. |
| `E:\Drone 2023\PANORAMA\100_0691` | **Mandatory in CI.** 3×3 with per-column roll −11.5/0/+11.5. The only fixture that validates the roll axis and sign; a wrong sign displaces content 500 px from the tile centre by `2·500·sin(11.5°) = 199 px` — unmissable. Also the `E_ROLL_REQUIRED` fixture (feed it with the roll tags stripped and assert `capture_mode == "wide_grid"` **and** the flag; the original spec's logic would assert `partial` + `W_ROLL_ABSENT`, which is the bug). |
| `E:\Drone 2021\Drone\PANORAMA\100_0056` | 4-tile aborted sphere, one yaw column. Must classify `vertical_strip`/`sector` (**not** `sphere`), produce a 3424×2784 output, return `n/a` (not `fail`) for CHK-LOOP and the pole checks, and still receive a sensible `Q` with `needs_attention: false`. |
| the 5 single-tile and 2 two-tile sets | Trivial path: viewable output, `single`/`pair`, no stitch, no crash, and **no divide-by-zero in any per-overlap statistic** — an empty overlap list is the classic NaN source in the score. |
| `E:\Drone-Review\DCIM\PANORAMA` (10 sets) | A folder named "Review" in a backup is the likeliest home of re-copied sets → the `content_id` dedup fixture, including a deliberately truncated winner to exercise duplicate promotion. |
| the cross-root `100_0679` pair | ID-collision fixture: distinct `set_id`s; matching `content_id` → `alias_of`, not two stitches. |
| `E:\India-2023-Media\Drone-SD-1..4 and -India-and-SD \DCIM\PANORAMA` | **The path fixture — trailing space in `SD \DCIM`.** Verified real, 81 of 135 sets. Must be traversed via `win_long()` and `PureWindowsPath`, never a shell string, never anything that could `.strip()`. Also the volume/throughput fixture and the drag-and-drop fixture. |
| `pano synth` output | A synthetic tile set rendered from a known equirect at the exact ground-truth angles, so **every lane can be tested with E: unplugged** and stitch PSNR can be measured against a known answer. |

## 11.3 Quality score

```
COV   0.30   covered solid angle / that MODE TEMPLATE's theoretical maximum
             (0.825 for a complete sphere26; computed per mode from the TEMPLATE, never
              from the observed lattice)
REG   0.25   CHK-REG median great-circle residual m (deg): 100*clamp((1.5-m)/1.35)
PHO   0.15   CHK-OVL median |d stops| s: 100*clamp((0.60-s)/0.55)
SEAM  0.12   post-blend seam gradient ratio g: 100*clamp((3.0-g)/1.9)
GHOST 0.08   100 - 300*(ghost_frac + 0.5*churn_frac)
SHARP 0.06   100*min(1, median(s_i)/s_ref) - 10 per tile with s_i < 0.4*median(s_i)
             s_i = mean |Laplacian| over the central 60% of tile i
EXPO  0.04   100 - 200*max(0, clip_frac - 0.005), measured on the TONE-MAPPED output

Every component is CLAMPED to [0, 100] before weighting.

A component is APPLICABLE only when its underlying check returned pass or fail.

Q = round( P * sum(w_i * C_i over applicable i) / sum(w_i over applicable i) )
  If sum(w_i over applicable i) < 0.5  ->  Q = null, band = "n/a"
  (a score built from a 0.06 and a 0.04 component is noise)

P = (1 - 0.5 * tiles_dropped/tiles_found)
  * (1.00 if all tiles have gimbal yaw+pitch else 0.85)
  * (1.00 if lattice_residual_rms <= 3 deg else 0.90)
  * (1.00 if no BLOCKING check has verdict "fail" else 0.70)

Bands: A >= 85, B 70-84, C 50-69, D 25-49, F < 25 or status != ok.
needs_attention = (band in {C,D,F}) or any(blocking check == "fail")
  EXCEPT for modes single / pair / fragment, where COV is n/a and needs_attention comes
  from the flags, not from the band.
```

Four corrections: the `n/a` semantics were never defined, so a single-tile set (where REG, PHO, SEAM and GHOST are all inapplicable by construction) scored either 10 or 25 → band F/D → `needs_attention: true`, contradicting the spec's own fixture assertion; `GHOST` goes negative above `ghost_frac` 0.33 and `SHARP` is unbounded below, violating the stated "every component is 0–100"; the 0.70 penalty is restricted to blocking checks (§11.1); and **`COV`'s denominator comes from the mode template, not the observed lattice.** That last one is the important one: a sphere missing its nadir tile fails `has_nadir`, demotes to `partial`, skips CHK-COV's sphere assertions and CHK-LOOP, has the missing cell removed from its own COV denominator (ratio ≈ 1.0, COV = 100), and has `tiles_dropped = 0` because the tile was never found — so a 25-tile sphere with a 0.075 sr hole under the drone lands in band **A** with `needs_attention: false`. T18 pins this.

Also: the nadir (`pitch < −80`) and the column-1 horizon shot become **expected-but-optional template cells**, so a sphere without them still classifies as `sphere` and emits `W_LATTICE_HOLE` + `W_NADIR_MISSING` with the hole's solid angle, rather than being silently reclassified.

**The score is never the only signal.** Every card carries the three components that cost the most points as plain sentences (`"registration residual 1.1 deg — gimbal angles may be off for 3 tiles"`), a 512×256 coverage-mask thumbnail, and a toggleable seam overlay. A number without a reason is not actionable across 135 items.

## 11.4 Failure catalogue (codes carried in `flags[]`)

`E_` = not renderable as intended · `W_` = renderable, degraded · `I_` = informational.

```
E_NO_GIMBAL          100% of tiles lack gimbal angles -> contact-sheet output, mode unstitchable
E_ROLL_REQUIRED      wide_grid with absent roll tags -> contact sheet only, NO stitched output
E_TILE_CORRUPT       tile unusable after salvage
E_COVERAGE_ANOMALY   a hole where the geometry says there must be none
E_SEAM_BROKEN        +/-180 discontinuity
E_OVERLAP_MISMATCH   CHK-OVL fail
E_NOT_A_PANO_SET     > 500 tiles, or XPComment absent on > 20% -> skipped with a report line
E_VOLUME_LOST        batch-level; exit 2

W_PARTIAL_SET  W_SEQUENCE_GAP  W_LATTICE_HOLE  W_NADIR_MISSING  W_TOPROW_GAP
W_MERGED_SET   W_MERGED_SET_UNRESOLVED  W_DUPLICATE_TILE
W_GIMBAL_ESTIMATED  W_ROLL_ABSENT  W_YAW_FROM_FLIGHT
W_GPS_SUSPECT  W_UNKNOWN_CAMERA  W_MIXED_MODEL
W_TILE_TRUNCATED  W_TILE_SUSPECT  W_BAD_SECTOR
W_PATH_ODDITY  W_LARGE_SET
W_GHOSTING  W_FLARE  W_EXPOSURE_SWING
W_NADIR_OCCLUDED  W_NADIR_MASK_UNRELIABLE  W_ZENITH_NOT_SKY_LIKE
W_VIEWER_DOWNGRADE

I_SINGLE_TILE  I_NAME_COLLISION  I_CONTENT_DUPLICATE  I_NO_GPS  I_ZENITH_FILLED  I_HIGH_DR
I_TEXTURE_CHURN
```

Notable handling:

* **`W_YAW_FROM_FLIGHT`:** yaw *may* be substituted from `FlightYawDegree` when `GimbalYawDegree` is missing (in pano mode the gimbal yaws with the airframe), recorded as `yaw_source: "flight"`. **Pitch and roll must never be substituted** — airframe pitch during a hover is a few degrees while gimbal pitch sweeps −90..+15. Lint T12 asserts the code cannot read `Flight{Pitch,Roll}Degree` in the projection module.
* **`W_ROLL_ABSENT`:** safe for sphere/strip (measured roll ≈ 0), **not** for `wide_grid` → escalates to `E_ROLL_REQUIRED` via the §7.1 separate gate.
* **`W_MERGED_SET`:** raise when `tiles_found > 1.25 × expected_from_lattice`, **or** when ≥ 25 % of template cells hold more than one tile. A duplicated lattice cell is *sufficient* — the original required a timestamp spread > 300 s *and* a lattice collision, which misses the likeliest case on this archive (a pano re-shot immediately after an aborted attempt: two 26-tile spheres at ~41 s each plus a short gap is ~90 s total, with no 120 s gap anywhere). Split by timestamp cluster when it separates cleanly, else by lattice-occupancy order (first tile to occupy each cell → set *a*) and flag `W_MERGED_SET_UNRESOLVED`. **Never silently overlay two different captures** — that is the worst kind of plausible-looking wrong output.
* **`W_GHOSTING` vs `I_TEXTURE_CHURN`:** in each overlap, after registration and exposure normalisation, on 16×16 blocks: `disagree = median|dL| > max(3σ_noise, 0.08·L_local)` with `σ_noise` from the robust MAD of the difference image in agreeing blocks. Classify: **ghost** = structured (spectral energy above 0.25 cycles/px < 60 %, area < 40 % of the overlap) → full penalty; **churn** = high-frequency, low-structure, area > 40 % → sea/foliage, **half weight**, because it is physically unfixable by any stitcher and is not a stitching defect. The archive spans coasts in four countries, so scoring them identically would drag the score down on many of the user's best panoramas. Mitigation: "winner-take-most" — in disagreeing blocks take the single highest-weight tile instead of averaging, turning a ghost into a hard but coherent edge. Emit `ghost_0512.png`.
* **Ghosting from metadata error is real and must be stated in the UI, not hidden.** 0.5–2° of gimbal error is 11–45 px of misregistration at the 8192 master, appearing as a double edge ~5–21 px apart in a 90° viewport on a 1920 px screen: clearly visible on rooflines, masts and power lines; effectively invisible on foliage, water, grass and cloud. Mitigation order: (1) freeze the measured focal and roll sign — done; (2) §9 refinement; (3) narrow the feather to `0.15 × overlap_px` paired with multi-band low-frequency correction; (4) future: graph-cut seams.
* **Parallax:** the gimbal rotates the camera about axes offset a few cm from the entrance pupil. At 30 m subject distance that is `0.03/30 = 1 mrad = 0.057°` ≈ 1.3 px at the master — invisible. Sets flown within a few metres of a structure (`0.03/2 = 0.86° = 20 px`) are **not** a rotation error and §9 cannot fix it; it will burn its 2° budget on an unfixable pair, and the §9.3 guard will reject the refinement, which is correct behaviour. Flag on low `RelativeAltitude` + high guard disagreement.
* **Viewer downgrades:** query `MAX_TEXTURE_SIZE` (an 8192 texture **fails silently to black** on a 4096-capped driver); prefer the cubemap for the immersive view on WebGL1 (equirect-on-sphere shows a mipmap-derivative seam line); handle `webglcontextlost`; decode with `createImageBitmap` on a worker.

## 11.5 What genuinely needs eyes (`VISUAL-ONLY` — deliberately small)

Four items, each with an auto-generated one-screen contact sheet so the review takes minutes: (1) zenith fill naturalness — a 135-thumbnail little-planet grid, where the fill shows at the rim; (2) ghost vs churn classification — the 20 highest `ghost_frac` sets with the map overlaid, to tune the 0.08 threshold **once**; (3) the nadir occlusion mask — inspected once, then frozen and regression-tested; (4) framing of cropped sets — is the inscribed-rectangle choice pleasant. Everything else in this spec is automated. If a proposed check can only be evaluated by looking, it belongs in this list, not in the per-set pipeline.

---

# 12. Modules — one file each, exact public interfaces

Every module owns whole files. The interfaces below are the **only** coupling, and each lane can be built against `tests/fixtures/` **with the source drive unplugged**.

| Lane | Files | Depends on (interface only) |
|---|---|---|
| **A** shell | `__main__ config logging_setup progress build server picker doctor vendor_three` | everyone's signatures |
| **B** ingest | `scan exif_exiftool exif_builtin` | nothing |
| **C** semantics | `classify grouping synth` | `TileMeta` |
| **D** geometry | `camera geometry projection` | `TileMeta`, `Classification` |
| **E** render | `render blend zenith` | `CameraModel`, `Coverage`, `TileImage` |
| **F** photometry | `photometry tonemap` | `TileMeta`, arrays |
| **G** artifacts | `pyramid manifest cache export` | `StitchResult` |
| **H** QA | `checks quality calibrate` | `StitchResult`, `pano.json` |
| **I** viewer shell | `viewer/js/*.js` (not `modes/`) | `library.json` only |
| **J** projections | `viewer/js/modes/* shaders/*` | the mode contract + `ctx` |

```
C:\Users\rzram\Claude-DroneImages-Pano\
├── Pano.cmd  "Pano - Add Folder.cmd"  Setup.cmd  README.md
├── pyproject.toml  requirements.txt  requirements-optional.txt  requirements-dev.txt
├── docs\  GROUND_TRUTH.md  BUILD_SPEC.md  MANIFEST_SCHEMA.md
├── profiles\  fc7303.json  fc7303_nadir_occlusion.png
├── pano\
│   ├── __init__.py            __version__, PIPELINE_VERSION, KEY_INPUTS_VERSION
│   ├── __main__.py      [A]   argparse subcommands; `if __name__ == "__main__"` guard
│   ├── config.py        [A]   Settings dataclass, RAM tiers, path resolution
│   ├── logging_setup.py [A]   UTF-8 stdout reconfigure, .run\ logs
│   ├── progress.py      [A]   console printer + the in-process event bus SSE reads
│   ├── util.py          [A]   win_long, atomic_replace (with retry), slug, haversine, cjson
│   ├── scan.py          [B]   §6.4 walk, two-pass acceptance, dedupe, content_id
│   ├── exif_exiftool.py [B]   §6.1 argfile batch, JSON -> TileMeta, §6.2 coercion
│   ├── exif_builtin.py  [B]   §6.3 optional pure-Python reader
│   ├── classify.py      [C]   §7.1
│   ├── grouping.py      [C]   §10.8
│   ├── synth.py         [C]   `pano synth`
│   ├── camera.py        [D]   §1 constants, CameraModel, calibration.json I/O
│   ├── geometry.py      [D]   §2.2 basis, §3.3 bbox + pole test, coverage math
│   ├── projection.py    [D]   §2.4 canvas mapping, §7.2 canvas sizing, inscribed crop
│   ├── render.py        [E]   §3.2-3.5 banded inverse map.  POOL ENTRY POINT
│   ├── blend.py         [E]   §4.5 weights, feather derivation, accumulator normalisation
│   ├── zenith.py        [E]   §5 boundary, band-median fill, harmonic fill, nadir mask
│   ├── photometry.py    [F]   §4.1-4.2 gains, LUTs, clip maps, §4.4 residual solve
│   ├── tonemap.py       [F]   §4.3 Durand + guided filter at 1/4 res, §4.6 dither
│   ├── pyramid.py       [G]   §10.1 ladder, thumb, blur, RGBE tier, cube faces
│   ├── manifest.py      [G]   §10.7 read/write pano.json, build library.json (union reduce)
│   ├── cache.py         [G]   §10.3-10.4 keys, plan(), lock, sweep
│   ├── export.py        [G]   `pano export`: cube, planet stills, video-orbit -> ffmpeg
│   ├── checks.py        [H]   §11.1 all CHK-*
│   ├── quality.py       [H]   §11.3
│   ├── calibrate.py     [H]   §11.1 CHK-CAL, CHK-XV, plumb-line distortion
│   ├── build.py         [A]   orchestration: plan -> pool -> incremental reduce; SIGINT
│   ├── server.py        [A]   §8.1 ThreadingHTTPServer, static + /api + SSE + token + Host
│   ├── picker.py        [A]   §10.6 PowerShell -> tkinter -> prompt
│   ├── doctor.py        [A]   environment report, --compare-exif
│   └── vendor_three.py  [A]   install/verify viewer\vendor\three, semver normalisation
├── viewer\
│   ├── index.html                       import map, DOM skeleton, token placeholder
│   ├── css\app.css
│   ├── js\ main.js library.js gallery.js viewerctx.js geometry.js texture.js
│   │      controls.js hud.js map.js keys.js overrides.js progress.js        [I]
│   ├── js\modes\ index.js immersive.js globe.js littlePlanet.js tunnel.js
│   │             flat.js pannini.js stereoWide.js mirrorBall.js
│   │             cubeCross.js coverage.js                                   [J]
│   ├── shaders\ quad.vert.js project.frag.js sphere.frag.js rgbe.glsl.js    [J]
│   └── vendor\three\ VERSION three.module.js three.core.js
├── out\   library.json overrides.json panos\<set_id>\... .run\
└── tests\ test_scan test_exif test_classify test_grouping test_geometry test_projection
        test_render_synth test_blend test_zenith test_photometry test_tonemap
        test_cache test_manifest test_checks test_quality test_lint test_server
        fixtures\ angles_sphere26.json angles_wide9.json angles_180_7.json
                  angles_partial4.json exif_sample.json tree_synthetic\
```

## 12.1 Python contracts

```python
# ---------- shared dataclasses (pano/config.py) ----------
@dataclass(frozen=True)
class TileMeta:
    file: str; path: Path; bytes: int; mtime_ns: int
    width: int; height: int; orientation: int
    yaw_deg: float | None; pitch_deg: float | None; roll_deg: float | None
    tags_present: tuple[str, ...]
    exposure_time_s: float; iso: int; fnumber: float
    datetime_local: str | None; subsec: str | None; tz_offset_min: int | None
    gps: tuple[float, float, float] | None
    abs_alt_m: float | None; rel_alt_m: float | None
    model: str; focal_35mm: float | None; xpcomment: str | None
    warnings: tuple[str, ...]

@dataclass(frozen=True)
class CameraModel:
    profile_key: str; focal_px: float; cx: float; cy: float
    tile_size: tuple[int, int]; hfov_deg: float; vfov_deg: float
    corner_half_angle_deg: float; roll_sign: int          # -1, from calibration.json
    k1: float; k2: float; distortion_enabled: bool
    mount_offset_deg: tuple[float, float, float]
    @property
    def t_h(self) -> float: ...
    @property
    def t_v(self) -> float: ...

@dataclass(frozen=True)
class Classification:
    capture_mode: str; geometry: str; confidence: float
    yaw_clusters_deg: tuple[float, ...]; yaw_cluster_counts: tuple[int, ...]
    pitch_clusters_deg: tuple[float, ...]; pitch_cluster_counts: tuple[int, ...]
    yaw_step_deg: float; yaw_span_centres_deg: float; yaw_full_circle: bool
    az_span_deg: float; pitch_step_deg: float
    pitch_template: str | None; pitch_template_residual_deg: float
    has_nadir: bool; has_zenith: bool; horizon_extra: bool
    max_abs_roll_deg: float; roll_significant: bool; roll_per_column: bool
    roll_tags_present_all: bool; tiles_missing_angles: int
    evidence: dict; warnings: tuple[str, ...]

@dataclass(frozen=True)
class CanvasSpec:                     # everything §2.4 and the viewer need
    width: int; height: int; px_per_deg: float
    theta_left_deg: float; phi_top_deg: float; az_center_deg: float
    crop_mode: str                    # "full" | "bbox" | "inscribed"

@dataclass(frozen=True)
class Coverage:
    canvas: CanvasSpec
    mask: np.ndarray                  # uint8 (H,W): 255/128/0
    captured_az_deg: tuple[float, float]; captured_el_deg: tuple[float, float]
    guaranteed_el_deg: tuple[float, float]
    boundary_per_column_deg: np.ndarray            # float32 (W,), -inf where uncovered
    boundary_min_deg: float; boundary_max_deg: float; boundary_mean_deg: float
    boundary_islands: int; scallop_lobes: int
    solid_angle_fraction: float; pixel_fraction: float
    holes: tuple[dict, ...]           # {"centroid_deg": (az, el), "solid_angle_sr": float}

@dataclass(frozen=True)
class StitchResult:
    set_id: str; status: str                       # ok|ok_partial|failed|cancelled|timeout
    classification: Classification; coverage: Coverage
    master_path: Path | None; linear_tier_path: Path | None; mask_path: Path | None
    photometry: dict; blending: dict; tiles: tuple[dict, ...]
    checks: tuple[dict, ...]; perf: dict
    error: dict | None; warnings: tuple[str, ...]; flags: tuple[str, ...]

# ---------- B: ingest ----------
# pano/scan.py
def find_sets(roots: Sequence[Path], opts: ScanOpts) -> list[CandidateSet]
def content_id(tiles: Sequence[Path]) -> str
def set_identity(set_dir: Path) -> SetIdentity      # volume_key, basis, rel_path, set_key, set_id

# pano/exif_exiftool.py   (and exif_builtin.py, same signature)
def read_many(paths: Sequence[Path], *,
              progress: Callable[[int, int], None] | None = None
              ) -> dict[str, TileMeta]              # keyed by str(path)
def coerce_angle(v, lo: float, hi: float) -> float | None
READER_VERSION: int

# ---------- C: semantics ----------
# pano/classify.py
def classify(tiles: Sequence[TileMeta], cam: CameraModel) -> Classification

# pano/grouping.py
def group(records: Sequence[PanoSummary], overrides: dict
          ) -> tuple[list[Trip], list[Location], list[Cluster], list[TourEdge]]

# pano/synth.py
def synth_tiles(equirect: Path, angles: Sequence[tuple[float, float, float]],
                cam: CameraModel, out_dir: Path) -> list[Path]

# ---------- D: geometry ----------
# pano/camera.py
def load_calibration(path: Path | None = None) -> CameraModel
def camera_for(meta: TileMeta, calib: CameraModel) -> CameraModel   # per-tile; §6.7

# pano/geometry.py
def camera_rotation(yaw_deg: float, pitch_deg: float, roll_deg: float,
                    roll_sign: int = -1) -> np.ndarray      # (3,3) float64, engine frame W
def ray_from_angles(az_deg, el_deg) -> np.ndarray           # frame W, broadcasts
def to_viewer_frame(d_W: np.ndarray) -> np.ndarray          # (x, y, -z)
def tile_bbox(R: np.ndarray, cam: CameraModel, dilate_deg: float
              ) -> TileBBox               # el_lo, el_hi, az_ranges[], az_full, cos_thresh
def projection_basis(f_hat: np.ndarray, lambda_c_deg: float
                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray]   # frame V; §2.3 rule
def coverage_from_masks(masks: Sequence[np.ndarray], canvas: CanvasSpec) -> Coverage

# pano/projection.py
def canvas_for(cls: Classification, tiles: Sequence[TileMeta], cam: CameraModel,
               px_per_deg: float, crop_mode: str, max_mpx: float = 80.0) -> CanvasSpec
def rays_for_band(canvas: CanvasSpec, row0: int, nrows: int,
                  cols: np.ndarray | None = None) -> np.ndarray     # (nrows, ncols, 3) f32
def largest_covered_arc(covered_cols: np.ndarray) -> tuple[int, int]      # wrap-aware
def inscribed_rect(mask: np.ndarray) -> tuple[int, int, int, int]

# ---------- E: render ----------
# pano/render.py
def stitch_set(job: StitchJob) -> StitchResult      # <-- POOL ENTRY POINT: module-level,
                                                   #     picklable args only, never raises
def render_equirect(tiles: Sequence[TileImage], canvas: CanvasSpec, cam: CameraModel,
                    weights: WeightSpec, band_rows: int,
                    on_band: Callable[[int, np.ndarray, np.ndarray, np.ndarray], None]
                    ) -> RenderStats
def render_gnomonic_patch(tiles, f_hat, lambda_c_deg, size_px: int, scale_px_per_deg: float,
                          cam: CameraModel) -> tuple[np.ndarray, np.ndarray]   # img, mask

# pano/blend.py
def feather_widths(cls: Classification, cam: CameraModel) -> dict[str, float]   # {"u","v"}
def tile_weight(u, v, cam: CameraModel, F_u: float, F_v: float,
                prior: float, clip: np.ndarray) -> np.ndarray
def nadir_polar_ramp(rho_deg: np.ndarray) -> np.ndarray
def normalise(C: np.ndarray, W: np.ndarray, K: np.ndarray, eps: float = 0.05
              ) -> tuple[np.ndarray, np.ndarray]           # rgb, mask

# pano/zenith.py
def boundary_per_column(mask: np.ndarray, canvas: CanvasSpec) -> np.ndarray
def fill_zenith(rgb: np.ndarray, mask: np.ndarray, canvas: CanvasSpec,
                method: str = "band", seed: int = 0) -> tuple[np.ndarray, dict]
def sky_like_fraction(rgb: np.ndarray, mask: np.ndarray, canvas: CanvasSpec) -> float
def nadir_occlusion_mask(disagreement_stack: np.ndarray) -> tuple[np.ndarray, dict]

# ---------- F: photometry ----------
# pano/photometry.py
def tile_gain(meta: TileMeta) -> float                      # a_k = t * ISO / N^2
def gains(tiles: Sequence[TileMeta]) -> tuple[dict[str, float], str, float]  # g_k, ref, a_ref
def build_lut(g: float, residual_rgb: tuple[float,float,float]) -> np.ndarray  # (256,3) f32
def clip_map(img_u8: np.ndarray) -> np.ndarray               # float32 (h,w), 0..1
def solve_residual_gains(pairs: Sequence[OverlapSample]) -> tuple[dict, dict]

# pano/tonemap.py
def linear_stats(rgb: np.ndarray, mask: np.ndarray, canvas: CanvasSpec) -> dict
def choose_compression(dr_stops: float) -> float
def durand(rgb: np.ndarray, mask: np.ndarray, *, compression: float, detail_gain: float,
           guided_radius_frac: float = 0.035, eps: float = 1e-3,
           base_divisor: int = 4) -> tuple[np.ndarray, dict]
def to_srgb8(rgb_lin: np.ndarray, seed: int) -> np.ndarray   # incl. TPDF dither

# ---------- G: artifacts ----------
# pano/pyramid.py
def level_ladder(canvas: CanvasSpec) -> list[tuple[int, int]]
def derive(master: Path, spec: OutputSpec, out_dir: Path) -> Derivatives
def encode_rgbe(rgb_lin: np.ndarray) -> np.ndarray           # uint8 (h,w,4)
def blur_from_tile(tile: Path) -> str                        # 24x12 data URI, ingest-time
def cube_faces(tiles, cam, size: int, out_dir: Path) -> dict[str, Path]

# pano/manifest.py
SCHEMA_SET = "pano/set@1"; SCHEMA_LIBRARY = "pano/library@1"
def write_pano(result: StitchResult, out_dir: Path) -> Path   # writes pano.json LAST
def read_pano(path: Path) -> dict
def build_library(out: Path, plan_records: Sequence[PanoSummary], trips, locations,
                  clusters, tour_edges, overrides: dict, build_state: dict) -> dict
#   build_library UNIONS on-disk sidecars with plan_records; never regenerates wholesale.

# pano/cache.py
def ingest_key(tiles, backend: str) -> str
def stitch_key(ingest_key: str, stitch_inputs: dict, camera: dict,
               classification: dict, coverage_image: dict) -> str
def derive_key(stitch_key: str, output_spec: dict) -> str
def plan(candidates, metas, out_dir: Path, spec: BuildSpec) -> dict[str, list[Task]]
def acquire_lock(out: Path) -> LockHandle                     # pid-liveness based
def sweep_temp(out: Path) -> int

# ---------- H: QA ----------
# pano/checks.py
def run_checks(result: StitchResult, ctx: CheckContext) -> list[dict]
BLOCKING = ("CHK-COV","CHK-SEAM","CHK-LOOP","CHK-OVL","CHK-REG","CHK-TONE")

# pano/quality.py
def score(checks: Sequence[dict], result: StitchResult) -> dict   # Q, band, components, ...

# pano/calibrate.py
def estimate_focal_ncc(pairs: Sequence[TilePair], roi: FixedROI,
                       candidates: Sequence[float]) -> FocalCurve   # must be unimodal
def solve_calibration(sets: Sequence[Path], out: Path) -> CameraModel
def cross_validate_sfm(set_dir: Path, cam: CameraModel) -> dict     # CHK-XV, gauge-aligned
def plumbline_distortion(horizon_tiles: Sequence[Path]) -> dict
```

## 12.2 JS contracts

```js
// viewer/js/library.js
export async function loadLibrary(url = "/lib/library.json")           // -> LibraryModel
export function filterSort(model, {query, mode, trip, starred, dups, sort})  // -> ordered ids
export function summaryFor(model, id)

// viewer/js/viewerctx.js   -- THE three.js facade; the only file that imports "three"
export function createContext(canvas)   // -> {renderer, scene, camera, quad, mesh,
                                        //     material, uniforms, state, resize(), dispose()}
export function setProjection(ctx, projectionId)      // sets uProjection + law uniforms
export function frame(ctx, dt)

// viewer/js/geometry.js
export function sphereForCoverage(coverage)   // SphereGeometry(phiStart/Length, thetaStart/Length)
                                              // with uv.y rewritten from 1-v to v
export function projectionBasis(fHat, lambdaCDeg)  // frame V; asserts cross(r,d) ~ f

// viewer/js/texture.js
export function loadLadder(summary, ctx, signal)       // blur -> level ladder, LRU, mips
export function chooseLevel(summary, {path, canvasCssWidth, dpr, hfovDeg, ballDiameterPx, cap})
export function loadLinearTier(summary, ctx)           // RGBE tier for the exposure slider

// viewer/js/controls.js
export function attach(ctx, {onChange})   // ray-based drag, wheel, pinch+twist, gyro, keys
export function setMode(ctx, modeId, opts)
export function dive(ctx, {from, to, durationMs})      // continuous r -> 0, §8.2

// viewer/js/modes/index.js
export const MODES = {/* id -> mode object, §8.2 contract */}
export function enabledFor(summary)       // -> {enabled:[ids], disabled:{id: reason}}
```

**The only Python↔JS contract is `library.json`.** `manifest.py` (lane G) and `library.js` (lane I) are the two sides; `docs/MANIFEST_SCHEMA.md` plus `tests/test_manifest.py` are the enforcement. Every field the viewer reads must be present for **all** mode branches including the degenerate 1- and 2-tile sets: fields that are meaningless there are **`null`, never absent** (`pitch_step_deg`, `scallop_lobes`, `synthetic_regions: []`, `quality.Q: null`). This is stated explicitly because it is the exact shape that crashes a viewer on the 7 degenerate sets.

---

# 13. Implementation phases

Each phase leaves the tool in a **working state**.

## Phase 1 — Vertical slice (working end to end)

Goal: **scan one folder, stitch one sphere, view it immersively and as a little planet.**

| Lane | Work |
|---|---|
| A | `Setup.cmd` + `pyproject` with `[project.scripts]` + `pip install -e .`; `Pano.cmd`; `pano up FOLDER` (explicit path only, no picker yet); `logging_setup` UTF-8; `util.win_long` with the **space/dot** trigger; `server.py` with `viewer/` as document root + `/lib/*` + token + Host check; `vendor_three` with semver normalisation and file-set assertion |
| B | `scan.find_sets` (single folder, two-pass acceptance); `exif_exiftool.read_many` with §6.2 coercion; `set_identity` from volume serial |
| C | `classify` — sphere branch only, but with the full §7.1 clustering, `az_span` rule and estimator pinning |
| D | `camera.load_calibration` (ships `profiles/fc7303.json` with the measured constants); `geometry.camera_rotation`; `geometry.tile_bbox` **with the exact pole test**; `projection.canvas_for` (full-sphere path); `rays_for_band` |
| E | `render.stitch_set` at 4096×2048 — banded, bbox-culled, boolean-mask-first, `np.take` gather, flat fancy-index `+=`, **with the column remap**; `blend.tile_weight` + `nadir_polar_ramp`; `zenith.fill_zenith(method="band")` |
| F | `photometry.gains` + `build_lut` + `clip_map`; `tonemap.durand` with the base at 1/4 res; `to_srgb8` with dither |
| G | `pyramid.derive` (ladder + thumb + RGBE tier); `manifest.write_pano` + a minimal `build_library` (one set); `cache` keys + `plan` (single-set path) |
| H | T1, T2, T3, T4, T5, T11, T12, T17, CHK-COV, CHK-SEAM, CHK-LOOP(a), CHK-TONE |
| I | `index.html` with import map; `viewerctx`; `texture.loadLadder` with `flipY:false`; `controls` (ray-based drag + wheel) |
| J | `quad.vert` + `project.frag` with **gnomonic and stereographic only**; `modes/immersive.js`; `modes/littlePlanet.js` |

**Phase 1 exit criteria:** `pano up "E:\Drone 2023\PANORAMA\100_0679"` produces a 4096×2048 tone-mapped equirect, opens the browser, and the user can look around immersively and switch to a little planet whose corners contain no synthetic sky. CHK-COV reports pixel coverage 0.725 ± 0.01. T1's fiducials land within 0.01° median. T17 passes.

## Phase 2 — The archive

| Lane | Work |
|---|---|
| A | `picker` chain; the drag-and-drop shim with quoting + trailing-space recovery; `build.py` orchestration — pool with `SIG_IGN` initializer, `BrokenProcessPool` recovery, `max_tasks_per_child`, per-set timeout, JSONL journal, `--resume`, exit codes; **incremental `library.json`** (queued-first, debounced, terminal on SIGINT); SSE with defined payloads + terminal `done`; RAM tiers; single-reader-per-volume + copy-first; `doctor` |
| B | duplicate `content_id` + integrity-ranked winner + promotion on failure; damaged-tile detection and truncation salvage via `ImageFile.Parser`; OneDrive/reparse pruning |
| C | all classification branches; `grouping`; `synth` |
| D | `canvas_for` cropped path, `largest_covered_arc`, `inscribed_rect`, angular-span padding |
| E | all modes render; master at 8192 |
| G | full `cache.plan` status table; `content_key`; lock with pid liveness; `manifest.build_library` union reduce; GPano XMP injection |
| H | T7, T8, T13, T14, T16, T18, T20; `report.csv` |
| I | virtualised grid with ingest-time blur; rail; filmstrip; search/filter/sort; SSE client; Health tab; `overrides` |

**Exit:** `pano up "E:\"` scans 135 sets, shows all of them on the first paint, and builds the archive in 12–20 minutes with a live progress channel, resumable, with no set able to kill the run.

## Phase 3 — The full viewing suite

| Lane | Work |
|---|---|
| J | `project.frag` gains the remaining laws (equidistant, equisolid, orthographic, Pannini with the `[0,1]` **and** domain clamps, cylindrical, Mercator with its own focal, linear); `modes/`: `tunnel` (off-axis default, permanent label), `flat`, `pannini` + the **Ribbon preset** (default for the 19 non-sphere sets), `stereoWide`, `mirrorBall` (`ρ = 2·asin(r)`), `cubeCross`, `coverage` |
| I/J | `globe` mesh + the **continuous dive** (`r: 4.15 → 0`, `DoubleSide`, `depthWrite:false`, `near 1e-3`, handoff to the shader at `r ≤ 0.05`); camera-relative `mirror` toggle; the 1.5 s intro |
| I | RGBE linear tier wired to a live exposure slider + a tone-map `compression` control; `map.js` offline canvas plot with the live FOV wedge; synthetic-region tint (`z`) |
| G | `export`: cube faces, print-size planet stills (tiled in 2048² chunks — a 6000² RGBA readback is 144 MB and will fail on modest GPUs), video-orbit frames piped raw into ffmpeg |
| H | T21; the four `VISUAL-ONLY` contact sheets |

Video export notes: drive a **deterministic virtual clock** (`t += 1/fps` per rendered frame, blocking readback), not `requestAnimationFrame`; **unwrap angles before interpolating** (a full revolution is `λ: 168.7 → 528.7`, never lerped across ±180); `-vf vflip` is mandatory because `readPixels` is bottom-up; round every dimension to even for `yuv420p`.

**Exit:** all ten modes, gated correctly per set, with the dive working continuously and the strips opening as ribbons.

## Phase 4 — Quality and calibration

| Lane | Work |
|---|---|
| H | CHK-OVL, CHK-REG (tangent-frame, great-circle), CHK-LOOP(b), CHK-HOR (with dip + k=N harmonic), CHK-SUN (ephemeris), CHK-DET; `quality.score` with n/a semantics; **CHK-XV with gauge alignment** — the release gate; `pano calibrate`: fixed-ROI NCC focal refinement, plumb-line `k1`, mount offset from CHK-HOR, conditioning gates |
| E | `zenith.nadir_occlusion_mask` from all 107 sphere sets, absolute threshold + contrast gate; drone-shadow detection |
| F | §4.4 residual per-channel gain solve with the log-domain acceptance gate and the LUT renormalisation |
| I | per-card quality badge, plain-language deductions, coverage thumbnail, seam overlay |
| H | T9, T10 goldens generated in CI |

**Exit:** every set carries a `Q` with reasons; CHK-XV agrees with SIFT to ≤0.5° after gauge alignment on all three fixtures; `calibration.json` carries a measured `k1` and mount offset or a documented "negligible".

## Phase 5 — Extras (ranked by value ÷ effort)

§9 geometric refinement with the §9.3 guard and T15 · graph-cut seams · narrow-feather + multi-band low-frequency correction · map/timeline brushing · walkable tour (**gate the effort on the measured inter-set distance histogram** — emit it in phase 2 and only build the tour if the 250 m / 60 m edge count is non-trivial) · date comparison (A/B wipe with locked camera state) · annotations · WebXR (`http://127.0.0.1` **is** a secure context, so no TLS needed; mono only — a single nodal point carries zero stereo information; **gnomonic only**, and the XR path must unproject per view from `projectionMatrixInverse` because per-eye frusta are asymmetric and no symmetric vFOV can express one) · sun overlay · Hosek-Wilkie synthetic sky driven by the solar position (matters only where you look up, i.e. VR and tunnel) · monocular 360 depth estimation.

**Explicitly do not ship:** fake yaw-offset anaglyph. A single camera rotating about its own nodal point produces **zero parallax**; the usual hack yields a pan the brain reads as a wobble. The honest routes are hyper-stereo from the existing neighbour graph (where two sets 10–100 m apart give real baseline) or monocular depth — the latter is the one high-effort bet worth taking eventually, because it upgrades every other mode.

---

# 14. Open questions — genuinely unresolved

1. **Per-set focal refinement.** GROUND_TRUTH measured 66.50–67.00 across 5 sets (0.5° spread) and suggests a fixed camera constant while allowing optional per-set refinement. Whether the 0.5° is measurement noise or real per-set variation (thermal, firmware) is unknown. The spec freezes 1520.9 and hashes it into `stitch_key`; if `pano calibrate` finds structured per-set variation, the decision of whether to store a per-set focal (and therefore invalidate the archive's cache on every refinement) is deferred. **Recommended probe:** run the fixed-ROI NCC estimator on 20 sets drawn from different roots and test whether the spread correlates with root, date or altitude.
2. **The built-in EXIF reader's fate.** It is specified and disabled. Whether it is ever worth enabling depends on whether `doctor --compare-exif` reports exactly zero angle deviation over a sample including all ten `wide_grid` sets. If it does, it saves ~30 s once per archive — which may not be worth the maintenance surface at all. Unresolved: keep it, or delete it.
3. **Tone-map compression adaptivity.** `compression = clamp(0.62 − 0.020·DR_stops, 0.32, 0.62)` is a linear interpolation between GROUND_TRUTH's two named endpoints (0.32 flatter, 0.55 contrasty) anchored on the one measured set (12.3 stops → 0.42). It is a **guess at the shape**, not a fit. Needs one calibration pass across the archive's measured dynamic-range distribution, judged visually on the §11.5 contact sheet.
4. **The 9-tile roll mechanism.** The measured per-column roll is −11.5/0/+11.5, and GROUND_TRUTH §2 measured that it must be negated in this convention — but *why* DJI writes it is unknown. If it is something other than a rotation about the optical axis (e.g. a residual of the levelling loop expressed in a different frame), those 10 sets could misalign even with the correct sign, and the inscribed-rectangle crop would silently shrink the canvas rather than reporting a problem. **Mitigation now:** an explicit CHK-OVL NCC floor on `wide_grid` sets that **warns** rather than trimming quietly.
5. **Inscribed vs bbox default per mode.** GROUND_TRUTH §11 says default presentation views to inscribed. Whether the *archival master* should also be inscribed (losing captured pixels) or bbox-with-alpha (ragged, but complete) is a taste call not settled by measurement. The spec chooses bbox+alpha for the master and inscribed for presentation; the ragged-edge severity per mode is a §11.5 visual item.
6. **`Q` weights and the ghost/churn split at 40 %.** Both are judgement calls with no data behind them. They will need one tuning pass against the real 135-set distribution. Shipping them as if measured risks the user distrusting the score after seeing one obviously-good panorama scored C. **Do not treat the current numbers as calibrated.**
7. **Magnetic declination.** Whether DJI applies declination before writing `GimbalYawDegree` is unknown. It does not affect the stitch, only the north overlay, the map wedge, and the sun overlay. CHK-SUN's ephemeris comparison across four countries (declination ~0° in India/Wisconsin to ~−25° in South Africa) will settle it empirically — until then `yaw_reference: "drone_heading_uncalibrated"` and no feature may depend on absolute north being correct.
8. **Timezone provenance.** `DateTimeOriginal` is naive local. Whether DJI wrote `GPSDateStamp`/`GPSTimeStamp` on these firmware versions is not in GROUND_TRUTH. If absent for a root, that root's UTC times are derived from lat/lon and are approximate; the archive must not be sorted chronologically and presented as correct.
9. **Nadir occlusion mask reliability.** The derivation assumes the drone appears at a *fixed* nadir-tile image coordinate across all 107 sets. If gimbal yaw relative to the airframe differs between sessions or firmware versions, the median smears and the mask is empty or too large. The contrast gate makes this fail *safe*, but it means the aircraft may simply remain visible at nadir on some sets — which the polar ramp mitigates but does not remove.
10. **Whether §9 refinement should be enabled by default.** Metadata carries 0.5–2° of error = 11–45 px at the master, visible on man-made structure across a meaningful fraction of 135 sets. The guard makes enabling it safe in principle, but it has not been validated on 20 sets. Decision deferred to after phase 5's first validation run.
11. **Timing.** The 46.8 s/sphere figure is **measured** on this machine, so it is trustworthy — but GROUND_TRUTH's aspirational 15–20 s target after further optimisation is not, and the thermal behaviour of an 8-worker sustained load on a laptop 12900H is unmodelled. Plan against 46.8 s; verify the target rather than assuming it.