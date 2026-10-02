I have everything verified. Writing the merged spec now.

# BUILD SPEC — `panolib print`: print-ready plates with a caption of record

**Version 1.0 · supersedes the three dimension specs (typography, printmech, provenance) and the adversarial findings against them.** Every correction applied below is marked `[CORRECTED]` with the reviewer claim it resolves; every contradiction between reviewers or between a spec and its reviewer is marked `[RULING]` with the reasoning. Facts marked `[VERIFIED]` were re-measured on this machine with the project venv (Pillow 12.3.0, `C:\Windows\Fonts`) during this merge — not carried over on trust.

---

## 0. The finding that reframes all three specs

**All three dimension specs propose new modules for a feature that is already half-built, and none of them read the package.** `[CORRECTED — not raised by any reviewer]`

The README lists only `panolib/exif.py`, `geometry.py`, `build.py`. The package actually contains:

| File | Lines | What it already does |
|---|---|---|
| `panolib/capture.py` | 279 | `Capture` dataclass, `read_capture()`, `PHOTO_TAGS`, `dms()`, `decimal_degrees()`, `format_latlon/altitude/heading/datetime/exposure()`, `map_url()`, `enrich_from_library()` |
| `panolib/paper.py` | 196 | `Paper`, `NAMED_SIZES` (17 entries), `parse_size()`, `fit_box()`, `assess_fit()`, `FitReport`, `max_print_size()`, `GOOD_DPI/ACCEPTABLE_DPI/MAX_UPSCALE` |
| `panolib/typeset.py` | 189 | `FONT_STACKS`, `load_font()` + cache, `pt_to_px()`, `has_glyphs()`, `text_width()`, `draw_tracked()`, `draw_small_caps()`, `TypeScale` |
| `panolib/plate.py` | 325 | `PlateStyle`, `STYLES` (gallery/survey/overlay), `build_rows()`, `render()`, `_draw_caption()`, `_render_overlay()`, `save_print()`, `caption_point_size()` |
| `panolib/_print_cmd.py` | 164 | `cmd_print()`, `resolve_targets()`, `write_sidecar()` |
| `panolib/__main__.py` | 305 | `print` subcommand, 18 flags, wired at line 265 |

So `python -m panolib print DJI_0163.JPG` **runs today**. This spec is therefore not a greenfield design. It is a set of corrections and extensions to a working v1, and that is what makes phase 1 small.

**`[RULING]` Module layout: extend the five existing modules. Do not create `panolib/printing.py` (printmech §7.1) or a third plate module.** Create exactly one new module, `panolib/provenance.py`, and only in phase 3, because digests/checks/ledger/signing is a genuinely separable concern with no layout coupling. Rejecting the new-module proposals is not conservatism: `paper.py` already owns the fit/resolution surface `printing.py` would duplicate, and two modules both named for printing with overlapping `Paper`/`PrintLayout` types is the kind of ambiguity that produces two divergent fit algorithms within a month.

A second consequence: several reviewer findings are already correct in the shipped code and wrong only in the specs. The heading selector is the important one (§3.4).

---

## 1. The honesty constraint, as binding rules

The brief's constraint is not a tone preference; it is the spec's governing rule, and it decides design questions that would otherwise be matters of taste.

**R1 — The plate transcribes, it does not verify.** Every printed value is sourced to a named tag in a named file. The governing verb is **recorded**. *The file records X* is a true statement about a file whether or not the file is honest. *The photograph was taken at X* is a statement about the world that editable EXIF cannot support.

**R2 — Never invent a value.** A missing field produces an omitted line or a named absence. Never a zero, never a default, never a value silently borrowed from a sibling file.

**R3 — Anything done to the pixels is disclosed.** A stitched panorama is 26 exposures, locally tone-mapped, with ~17 % of the sphere above +41° synthesised (README; `docs/GROUND_TRUTH.md`). A plate that attests provenance while implying one untouched exposure undercuts itself.

**R4 — Banned vocabulary**, enforced by a test over the rendered string set (§9.4): *verified, certified, authenticated, authentic, validated, confirmed, guaranteed, proof of, tamper-proof, tamper-evident, provenance-verified, blockchain, notarised, unaltered, genuine, trusted, original* (as a claim about this file), **and `local`** as a time-zone token.

`[RULING]` The ban applies to assertions only, and is permitted in explicit negation ("not a verification", "nothing here is certified"). Without this carve-out the lint rule flags the spec's own best sentences — §7's explanation paragraph contains three of the banned words, all negated — and the pressure would then be to soften them, which is the one direction this rule must not move. The lint matches a banned word only when no negator (`not`, `never`, `nothing`, `no`) appears earlier in the same clause. *(Both provenance reviewers raised versions of this; this is the adjudicated form.)*

**R5 — Typography out-signals words.** No seal, rosette, guilloche, checkmark, ribbon, reticle, crosshair, compass rose, map pin, or certificate-style frame. A plate whose wording is scrupulous and whose decoration says *certificate* has lied in the channel people read first. One hairline is permitted and only because it does semantic work (§5.6).

**R6 — No face badge for consistency checks.** "5 of 5 checks passed" converts a transcription into a certificate in a reader's mind, and the checks detect carelessness, not forgery. Terminal and sidecar only.

**R7 — The footnote is not optional when recorded values are printed.** `[CORRECTED — provenance R1 "default footnote omits the honesty clauses"; provenance R2 same]` The existing `--no-footnote` flag (and the proposed `--footnote none`) would strip the datum, the accuracy clause and the "metadata can be edited" sentence while leaving the data block and the apparatus of attestation. That is strictly worse than no plate. `--no-footnote` now errors unless `--no-provenance` is also given, which produces a title-only print with no recorded values at all.

---

## 2. Sheet, fit and resolution (`panolib/paper.py`)

### 2.1 Layout defined in inches; pixels derived

Everything geometric is defined in inches so the resolution test is independent of the dpi finally written. `S` = short paper edge in inches, `L` = long edge, `D` = diagonal.

```
margin    m = clamp(0.050 * S, 0.25, 1.00)   in
band      c = clamp(0.085 * S, 0.50, 1.10)   in     # FLOOR for the caption band, not its budget
gap       g = clamp(0.250 * c, 0.12, 0.30)   in     # image bottom -> band top
```

**Pixel conversion, normative and single-valued** `[CORRECTED — printmech R1 and R2 both: "the published tables are not produced by the px() rule they advertise", 10 of 28 cells off by 1 px]`:

```python
def px(inches: float, dpi: int) -> int:        # inches -> pixels, round half UP
    return math.floor(inches * dpi + 0.5)      # NOT round(): round(82.5) == 82
def rpx(pixels: float) -> int:                 # round half up on a pixel count
    return math.floor(pixels + 0.5)
```

`px()` applies to the paper and to `m`, `g`, `c` **only**. Every derived box comes from pixel subtraction, never from `px()` of a derived inch value:

```
Wp = px(W_in, dpi) ; Hp = px(H_in, dpi) ; mp = px(m, dpi) ; gp = px(g, dpi) ; cp = band_px
box_w_px = Wp - 2*mp
box_h_px = Hp - 2*mp - gp - cp
```

This is the direction the arithmetic must run, because it is the only one in which the parts sum exactly to the canvas. Both printmech reviewers independently proposed it; it is also what the shipped `plate.py` already does.

`[CORRECTED — my own finding, missed by all four reviewers]` **`c` is a floor, not a budget, and on the three commonest consumer sizes the floor is too small.** Measured: the default 3-row block plus footnote needs 0.647 in on a 5x7 against `c = 0.500`; 0.717 in on 8x10 against 0.680; 0.727 in on A4 against 0.703. It fits from 11x14 up. Printmech §1.1 presents the per-size point budgets ("5×7 → 36 pt … 2 lines of 9 pt") as if `c` were the allowance, which would overflow the band on exactly the sizes most of this archive will be printed at. The `band = max(plate.measure(...), px(c))` contract already permits the plate to ask for more; what was wrong was presenting `c` as the answer. **Normative: `band_px = max(measure_band(...), px(c, dpi))`, and the measured value governs at and below A4.**

### 2.2 Named sizes

Use the shipped `paper.py::NAMED_SIZES` (5x7, 6x4, 8x10, 11x14, 12x18, 16x20, 16x24, 20x30, 24x36, a5, a4, a3, a2, a1, pano-2to1, pano-3to1, square). Add `letter` (8.5×11), `legal` (8.5×14), `12x36` (12×36), `10x30` as an alias of `pano-3to1`.

`parse_size()` already handles names, `WxH`, and `in|mm|cm`. Two fixes `[CORRECTED — printmech R1 and R2 both]`:

1. The integer part is capped at 3 digits, so `1000x1500mm` is unparseable and the advertised metric range is unreachable. Widen to `\d{1,4}`.
2. Whitespace before a unit word kills the match (`"11.7 by 16.5 in"`). Normalise with `re.sub(r"\s+", "", spec)` before matching, and accept `by` and `×` as `x`.
3. Add a bounds check **after** the match so an oversized sheet gets the range message rather than the "no such size" message: reject either edge outside 2.0–60.0 in.

**Canvas guard:** refuse above 250 Mpx unless `--allow-huge`.

### 2.3 `native_dpi` — the formula, corrected

`[CORRECTED — printmech R1 blocking: "silently overstates resolution by 1/r_img for portrait sources"]` **I re-derived this and it is worse than the reviewer's example.** Printmech §3.1 defines `native_dpi = source_long_edge_px / printed_long_in` with `printed_long_in = min(box_w_in, box_h_in * r_img)` — which is the printed *width*, not the printed long edge. Measured for a 2250×4000 portrait frame on 16x20 portrait (box 7.200 × 13.025 in): the spec's formula returns **555.6 dpi**; the true figure is **312.5 dpi**. The gate therefore passes silently where it should warn, and the module writes an upscale while believing it refused one. Portrait sources are explicitly supported, so this is reachable.

**Normative:**

```python
def native_dpi(source_px, box_in, fit) -> float:
    sw, sh = source_px ; bw, bh = box_in
    if   fit == "contain":   ipp = min(bw/sw, bh/sh)
    elif fit == "cover":     ipp = max(bw/sw, bh/sh)
    else:                    ipp = bw/sw            # fit-width
    return 1.0 / ipp
```

Both axes give the same answer, there is no long/short edge to confuse, and every landscape figure in printmech §3.4 is unchanged (I reproduced all 27).

### 2.4 Which fit, by aspect ratio

With `r_img = sw/sh` and `r_box = box_w/box_h`:

1. `|r_img − r_box| / r_box ≤ 0.02` → **cover**. The shapes agree; filling discards under 2 % and letterboxing a 2 % mismatch reads as a mistake.
2. `r_img ≥ 1.9` **and** `r_img > r_box` **and** fit-width crops ≤ 25 % of the source height → **fit-width**.
3. Otherwise → **contain**.

`[CORRECTED — printmech R2 blocking: "r_img ≥ 2.5 makes fit-width unreachable for every 2:1 equirect"]` The 2.5 threshold is the archive's flagship failure: 107 of 135 sets are 2:1 spheres, `r_img = 2.0 < 2.5`, so rule 2 could never fire — which also made printmech's own §2.4 advisory message and §7.4 example B unreachable, and contradicted its own key decision about the 16.5 % crop on 10x20. 1.9 admits the spheres; the added `r_img > r_box` clause prevents fit-width firing where it would be identical to contain. Verified: a 4000×2250 photo on 10x20 implies a 25.7 % crop and still falls back to contain.

**Contain is the default everywhere else on purpose.** The plate attests what the file records; a silent crop removes part of what is being attested.

**Aspect guard** `[CORRECTED — typography R3 major: "a mat absorbs all of them is quantifiably false"]`. The claim that a mat handles any aspect ratio is false on pinned paper, and the counterexamples are the pano sheets themselves: an 8192×4096 equirect on a 9x36 sheet fills 33.6 % of the width, giving 11.96 in side margins against a 0.79 in top mat — a 15× mismatch. Before layout, compute `A_win = r_img` and `A_av = box_w/box_h`; if `max(A_win/A_av, A_av/A_win) > 1.25`, do not centre silently:

- with `--size` given: warn, naming the sheet that fits — `image is 2.00:1, 10x30 offers 3.65:1; try --size 10x20 (516 dpi, no crop) or --fit fit-width (282 dpi, crops 16.5%)`
- with `--size auto`: the ratio term in §2.6 already prevents it.

### 2.5 Resolution policy

```
DPI_LADDER = (300, 240, 200, 180)
MIN_DPI    = 160        # refuse below this native dpi
WARN_DPI   = 240
```

`--dpi auto` writes the largest rung not exceeding `native_dpi`. Writing dpi = 240 with pixel dimensions `240 × inches` gives the lab the exact physical size while shipping no interpolated pixel. **If no rung fits, do not upscale: write `--dpi native`.** `[CORRECTED — printmech R2 major: "the spec never considers the option its own argument implies"]` I verified Pillow 12.3 round-trips a non-integer dpi on a TIFF: saved 117.6, reopened 117.5999984741211. So a 4000×2250 frame can print at exactly 34.00 in tagged 117.6 dpi with no interpolation, which is strictly better than the 1.53× Lanczos upscale the 180-rung floor would force. JFIF density is integer, so on JPEG the dpi rounds and the resulting size change is reported — a reason to keep TIFF the master, not a reason to upscale.

| native_dpi | action | file dpi | message |
|---|---|---|---|
| ≥ 300 | downsample | 300 | silent |
| [240, 300) | downsample | 240 | silent |
| [180, 240) | **downsample** (s = 0.75–1.00) | 200 or 180 | INFO: the real dpi |
| [160, 180) | downsample to 180 | 180 | **WARNING** + largest named size reaching 240 |
| < 160 | **REFUSE**, exit 3 | — | the honest maximum, and the three overrides |

`[CORRECTED — printmech R1 and R2 both marked the 180–240 row "resample (≤ 1.11× up)" as wrong]` The ladder never exceeds `native_dpi`, so that band is always a downscale; 1.11 is 200/180, a combination the ladder cannot produce. It matters because §3.6's sharpening branches on `s < 1`.

**The floor, derived honestly** `[RULING — printmech R1 and R2 both blocking, and they disagree on the remedy]`. Printmech §3.3 says "1.5 × its 43.3 in diagonal = 1.10 m". I measured: 43.267 in **is** 1.0990 m, so the multiplier and the distance contradict each other. At 1.0× diagonal one arcminute subtends **0.320 mm**; at 1.5× it subtends **0.480 mm** — and at 0.480 mm the 24x36 the section refuses would be exonerated. The supporting sentence is also internally inconsistent: it states a 3-output-pixel edge and then uses 2 px at 1.20× and 3 px at 1.53×.

R1 proposes MIN_DPI 160 with a single k=2 model; R2 proposes 140 with a linear interpolation between the spec's own two anchors and keeps 150 as a margin. **Ruling: R1.** A single stated model beats interpolating between two figures that were themselves inconsistent, and "the crossing is at 140, we use 150 for margin" invites the next reader to delete the margin. Final text:

> A 24×36 is viewed at about its own 43.3 in diagonal = 1.10 m, where one arcminute subtends 0.320 mm. A Lanczos-3 upscale of factor *k* spreads a step edge over roughly 2*k* output pixels, so at a 180 dpi file the interpolated edge is 50.8/native mm. That crosses 0.320 mm at **native 159 dpi**. MIN_DPI is 160. At native 117.6 — the 24x36-from-a-photo case — the edge is 0.432 mm, well above the limit, and no sharpening recovers detail that is absent.

Overrides: `--min-dpi N` moves the floor; `--dpi native` resamples nothing; `--allow-soft` prints anyway and **appends `-soft` to the filename**.

`[CORRECTED — printmech R2 major: "--dpi 300 can ship a 1.9× upscale with no -soft token"]` A forced `--dpi` above `native_dpi` is an upscale and is treated as one: it requires `--allow-soft`, emits the warning, sets `upscaled: true` in the sidecar, and takes the `-soft` token. Without `--allow-soft` the forced rung is clamped to the largest rung ≤ native and the clamp is reported. Otherwise §8's self-identification promise is false.

Keep the shipped `MAX_UPSCALE = 1.6` in `paper.py` as an independent hard stop above the dpi gate.

### 2.6 `--size auto`

`[RULING — printmech R1 and R2 both blocking; their published distances are paper-ratio while the stated formula is box-ratio, and the two give different winners]` Both reviewers converge on scoring the **box** ratio, which is the quantity that actually predicts empty paper, and both recomputed the same figures. R1 keeps the 0.12 window; R2 tightens to 0.06. **Ruling: box ratio, window 0.06**, because with 0.12 the "largest area" tiebreak reaches across genuinely different shapes and selects A2 for a sphere that wants 10x20.

```
1. For every size in NAMED_SIZES (aliases are honoured when named but NEVER auto-selected),
   in the orientation --orient auto would pick, compute native_dpi under the auto fit.
2. Keep native_dpi >= 300. If none, retry >= 240, then >= 160. If still none, raise
   ResolutionTooLow naming the honest maximum in inches.
3. distance = |ln(box_w_in / box_h_in) - ln(r_img)| ; keep everything within 0.06 of the
   best ; take the largest area.
```

`[CORRECTED — printmech R1 major]` The alias exclusion is load-bearing, not housekeeping: with `legal` in the pool, `--size auto` on an ordinary 16:9 drone frame returns US legal.

**Verified outcomes.** 4000×2250 photo: box distances 5x7 0.058, A4 0.071, 11x14 0.185, 8x10 0.205 → survivors {5x7, A4} → **A4 landscape, 300 dpi**. 8192×4096 sphere: survivors {12x18 0.126, 16x24 0.151} → **16x24 landscape, 366 dpi**.

`[CORRECTED — printmech R1 and R2 both major]` Never describe a paper-ratio match as "no letterbox". A 2:1 source on a 10x20 sheet still letterboxes 1.56 in each side, because the caption band takes 1.06 in off the short edge, making `r_box` 2.39 rather than 2.00. Paper whose ratio equals the image ratio can never give a flush fit while a band is reserved. The summary line states the empty paper in inches.

`[CORRECTED — printmech R1 and R2 both minor]` The claim that 5x7 is the only size the 1111×761 wide grid can reach is false: 6x4 clears 240 dpi at 265, and A5 (165), 6x18 (160) clear the floor. The correct sentence names 6x4.

### 2.7 EXIF Orientation

`[CORRECTED — typography R3 major: "Wi, Hi are taken as stored pixel dimensions and Orientation is never read"]` **Confirmed against the code: no module calls `exif_transpose`.** `PHOTO_TAGS` requests `Orientation` and `capture.py` discards it. A portrait crop carrying Orientation 6 lays out on the wrong aspect — wrong sheet derivation, wrong mat, wrong caption measure. Normative: open through `ImageOps.exif_transpose()` before `Wi, Hi` are read, and write `Orientation=1` on output.

---

## 3. What the plate says (`panolib/capture.py`, `panolib/plate.py`)

### 3.1 Coordinates

**Five decimal places, hemisphere letters, no signs.** At 51.48° one unit in the fifth place is 1.113 m N–S and 0.693 m E–W (`111320 × cos 51.48° = 69 329 m` `[CORRECTED — provenance R1 minor: the spec's two earlier figures were both wrong]`). Consumer GNSS is good to 3–5 m, so the fifth place sits about four times finer than the error. Four places is 11.1 m — coarser than the instrument's own error, which displaces the point by up to 5.6 m and collapses positions the receiver genuinely distinguished. Six claims 11 cm.

**DMS to 0.1″** when `--coords dms` (the default on the face): 0.1″ is 3.092 m N–S, which lands almost exactly on the instrument's accuracy, so the last printed digit is also the last meaningful one. Never the EXIF's native 0.01″ (31 cm).

`[RULING]` **Both forms, in different registers, but never both at value size.** DMS is the display form (reads as a place, cartographic convention); decimal degrees is the machine form (what you paste into a map box, far less error-prone to retype). Gallery: DMS in the data block, DD in the footnote. Survey: DD and DMS in adjacent columns. Typography §4.2's claim that DMS's fixed-width structure "makes the two coordinate lines column up" is struck — the gallery sets both coordinates on one line `[CORRECTED — typography R1 major]`.

**Glyphs:** degree U+00B0, prime U+2032, double prime U+2033, real minus U+2212. Never `'` or `"` or a hyphen. Datum **WGS 84**, mandatory in the footnote — not pedantry at this archive's scale, since South Africa's older local datum is offset from WGS 84 by roughly 300 m.

### 3.2 The formatters — and the blocking bug in all three specs

`[CORRECTED — typography R1 blocking, typography R2 blocking, both independently]` **Every proposed `dms()`/`dd()` emits a literal U+2009 THIN SPACE while the prose two paragraphs above forbids exactly that.** I measured the consequence at size 72:

| font | U+2009 | advance |
|---|---|---|
| **georgia.ttf** | **.notdef** | **1.000 em** (a 72 px tofu box) |
| **ARIALN.TTF** | **.notdef** | 0.222 em |
| **constan.ttf** | **.notdef** | 0.486 em |
| **LBRITE.TTF** | **.notdef** | 0.500 em |
| pala.ttf | present | 0.194 em |
| cambria.ttc | present | 0.167 em |
| consola.ttf | present | 0.556 em |

So the character is .notdef in four of the candidate faces *and delivers the wrong metric in every face where it exists* — the spec asks for 0.16 em and gets 0.167 to 0.556. A pen advance is not a refinement here, it is the only correct mechanism.

**Normative: formatters return segments, never a string containing any space character.**

```python
Segment = tuple[str, float]          # (text to draw, em advance to add AFTER it)

def dms_segments(deg: float, is_lat: bool, *, sec_dp: int = 1,
                 pad_deg: bool = False) -> list[Segment]:
    """51.477800, is_lat=True -> [('51°28′40.1″', 0.16), ('N', 0.0)]"""
    hemi = ("N" if deg >= 0 else "S") if is_lat else ("E" if deg >= 0 else "W")
    a = abs(deg)
    d = int(a); m = int((a - d) * 60); s = round(((a - d) * 60 - m) * 60, sec_dp)
    if s >= 60: s -= 60; m += 1
    if m >= 60: m -= 60; d += 1
    sw = 2 if sec_dp == 0 else 3 + sec_dp          # CORRECTED: see below
    dw = 3 if pad_deg else 1                       # CORRECTED: see below
    pad = " " if (pad_deg and is_lat) else ""      # reserve the degree cell
    return [(f"{pad}{d:0{dw}d}\u00b0{m:02d}\u2032{s:0{sw}.{sec_dp}f}\u2033", 0.16),
            (hemi, 0.0)]

def dd_segments(deg: float, *, dp: int = 5, signed: bool = False,
                is_lat: bool = True) -> list[Segment]:
    if signed:
        sign = "\u2212" if deg < 0 else " "        # reserve the sign cell
        return [(f"{sign}{abs(deg):.{dp}f}\u00b0", 0.0)]
    hemi = ("N" if deg >= 0 else "S") if is_lat else ("E" if deg >= 0 else "W")
    return [(f"{abs(deg):.{dp}f}\u00b0", 0.16), (hemi, 0.0)]
```

Three further corrections inside these eight lines:

- `[CORRECTED — typography R1 minor, confirmed by running it]` The seconds field width `3+sec_dp` is only right for `sec_dp ≥ 1`, because it budgets a column for the decimal point unconditionally. At `sec_dp=0` and `s=14.0`, `format(14.0, '03.0f')` returns `'014'`. `sec_dp` is a public parameter and §2.5's `--round-position` path reaches for it.
- `[CORRECTED — typography R1 major]` `pad_deg` with `dw = 2 if is_lat else 3` produces 13 cells for latitude and 14 for longitude, so left-aligning the survey DMS column puts latitude's `°` where longitude's `7` sits — the same one-cell defect the spec caught for the minus sign and missed one column to its left. `dw = 3` for both plus a leading space on latitude gives 14 cells each.
- `[CORRECTED — typography R1 minor]` The spec's own §8.2 mock-up conceals this by right-aligning where §3.1 and §7.2 specify left-aligned. The mock-up was wrong, not the alignment rule.

The shipped `capture.py::dms()` has the correct carry handling and the correct width comment but hard-codes `3 + seconds_dp` and emits no space at all (it writes `13.7″N` with no gap). Fix the width; replace the string return with segments.

**Test:** `len("".join(t for t,_ in dms_segments(51.48, True, pad_deg=True))) == len("".join(t for t,_ in dms_segments(-0.0015, False, pad_deg=True)))`.

### 3.3 Altitude — two numbers that must never merge

| source | what it is | printed | label |
|---|---|---|---|
| `XMP:RelativeAltitude` +19.40 | metres above the **take-off point**, barometric | `19 m` | `above take-off point` |
| `XMP:AbsoluteAltitude` +206.58 / `GPSAltitude` 206.5 | metres above **sea level**, barometric | `207 m` | `above sea level` |

```
19 m above take-off point   ·   207 m above sea level
```

**`[RULING]` Whole metres for both.** Typography §4.4 wants 1 dp on the relative figure ("a barometer referenced to a known datum is good to ≈0.5 m, so the tenth is real"); provenance §3.3 wants 0 dp on both ("the reference point is wherever this flight happened to take off, possibly a sloping hillside"). **Provenance wins.** The printed figure's error is dominated by the ambiguity of the reference, not by sensor resolution, and `19.4 m` beside `207 m` advertises two different precisions for no gain — it reads as one number being careful and the other being careless. One metre is already finer than either deserves and is kept only because it is the unit a reader thinks in.

**`[RULING]` "above take-off point", not "above launch point" or "above launch".** Provenance prefers "launch point"; the brief itself says "metres ABOVE TAKEOFF POINT". Use the brief's phrase: it is unambiguous to a reader who does not fly, and "launch" has a rocket connotation.

**Three prohibitions, each a real failure mode:**

1. **Never "AGL" or "above ground".** `RelativeAltitude` is referenced to the launch point, not the terrain under the aircraft. Fly off a cliff at a constant 19 m relative and you are 200 m above the ground. There is no safe abbreviation.
2. **Never add, subtract or reconcile the two on the face.** Different datums, different error models. (The difference is computed in the sidecar as a cross-check only — §6.3.)
3. **Never print one alone under the bare label "Altitude".** `[CORRECTED — against the shipped code]` `plate.py::build_rows()` currently labels the row `Altitude` and puts ASL first. Both must change: the row label becomes `ALTITUDE` only because the values carry their own full phrases, and the take-off figure is printed **first** — it is the number people mean by "how high was the drone".

Rounding: `math.floor(v + 0.5)`, not `round()`. `[CORRECTED — provenance R2 minor]` `round(206.5)` returns 206 under banker's rounding, and `GPSAltitude` is quantised to 0.1 m so an x.5 input arrives about one time in ten — the working example's own `GPSAltitude` is literally 206.5.

Imperial (`--units ft`): `× 3.280839895`, both to 0 dp. The shipped `format_altitude()` already does this.

**Open question, flagged not papered over — §10.1.** The file never records whether the sea-level figure is orthometric or ellipsoidal.

### 3.4 Heading — the tag both specs got backwards

`[CORRECTED — provenance R1 blocking, provenance R2 blocking, and the shipped code is already right]`

**Typography §4.5 says "Use `GimbalYawDegree`… Never `FlightYawDegree`". Provenance §1.5 says "`FlightYawDegree` is never printed on the plate". Both are inverted for the standalone frames this feature exists to serve.** `panolib/capture.py` records the measurement, which I read directly:

> Measured over 1,843 ordinary frames from three different cards: GimbalYawDegree is EXACTLY 0.00 on every single one, while FlightYawDegree spans the full circle. The Mavic Air 2's gimbal has no independent yaw axis — it only pitches, and the aircraft turns to aim the camera — so on a normal photo the gimbal yaw is a relative value pinned at zero and the airframe heading IS the camera heading.

Corroborated by `docs/BUILD_SPEC.md:924` and spot-checked in the code's comment against a Chicago lakefront frame. The brief's own working example is `Type=N` with `GimbalYaw +0.00` and `FlightYaw +47.60` — the signature of the pinned field. So typography's plate prints `camera facing 000° N` for a camera that faced north-east, which is a false statement on the face produced by printing a default (violating R2), and provenance's §4 Tier 3 calls the only field holding the answer "worse than noise".

**Normative — use the selector the shipped code already has:**

```python
# capture.py, unchanged and correct
if gimbal_yaw is not None and abs(gimbal_yaw) > 0.005:
    heading, heading_source = gimbal_yaw, "gimbal"     # panorama tiles: real bearing
elif flight_yaw is not None:
    heading, heading_source = flight_yaw, "airframe"   # Type=N: the camera heading
```

Carry `heading_source` into the sidecar. For the working example the correct line is **`camera facing 048° NE`**, verified: 47.60 falls in the 33.75–56.25° north-east sector and rounds to 048.

Two further corrections:

- `[CORRECTED — typography R1 minor]` `round(bearing)` is not reduced mod 360, so any yaw in (−0.5, 0) prints `360° N`, which is not a bearing. `b = round(bearing) % 360`. DJI yaw sits near zero constantly, so this is the common case, not an edge case. The shipped `format_heading()` already does `d = deg % 360.0` and is correct.
- `[CORRECTED — provenance R1 minor]` Delete the "47.6° pan is ordinary" note from the check list — this airframe cannot command an independent 47.6° gimbal pan. Replace with a `note`: `gimbal yaw pinned at 0.00 as expected for a Type=N frame on this model; camera heading taken from the airframe field`.

**Declination qualifier — required.** `[CORRECTED — provenance R2 major]` `docs/BUILD_SPEC.md:701` rules: "`GimbalYawDegree` references the drone's compass. Declination spans ~0° (India, Wisconsin) to ~−25° (South Africa)… **never validate yaw against a map** and never flag a constant yaw offset as an error." A 25° offset is wider than a 16-point sector (22.5°), so in the South African material the printed compass *word* can be wrong. Inviting a reader to check an uncalibrated magnetic heading against a true-north map manufactures a disagreement the file is not responsible for — the inverse of this feature's purpose. **So: keep the face line clean (`camera facing 048° NE`), strike "the number lets a reader check it on a map", and carry the qualifier in the footnote whenever a heading is printed** (§4.3). Sidecar: `heading.reference = "drone compass, uncalibrated; declination not applied (up to ~25° in this archive)"`, mirroring the project's existing `yaw_reference: "drone_heading_uncalibrated"`.

**Pitch gate.** A heading is meaningless when the camera points down.

| `GimbalPitchDegree` | printed |
|---|---|
| −20 ≤ p ≤ +90 | `camera facing 048° NE` |
| −75 ≤ p < −20 | `camera facing 048° NE, tilted 38° down` |
| p < −75 | `camera pointing straight down` (no compass word, no degrees) |
| **absent** | **heading line omitted; sidecar records the absence** |

`[CORRECTED — provenance R1 major]` The absent row is not cosmetic: `exif.py::as_float` defaults to 0.0, which lands a missing tag in the level row and prints a confident compass direction for a nadir frame. Read pitch with `as_opt_float`, never `as_float`. `Capture` has no pitch field today — add `gimbal_pitch: float | None`.

**Full sphere: no heading at all.** A 360 image has no facing. The shipped `build_rows()` already suppresses it on `is_panorama`. For a partial, print the measured coverage from the pipeline instead.

### 3.5 Date and time

`DateTimeOriginal` carries no zone, and the archive spans five places across four zones (−06:00/−05:00, +00:00/+01:00 BST, **+05:30**, +02:00). `[CORRECTED — provenance R2 minor]` The spec calls Scotland "+01:00" flatly; that is BST, standard is +00:00 — and the five named places are four distinct zones, since Wisconsin and Chicago are both America/Chicago.

**Print the clock exactly as recorded, attributed to the camera, no zone, no conversion:**

```
26 July 2026, 12:10:03 by the camera's clock
```

`[RULING — typography R1 major]` Typography §8.1 prints `12:10 local`, which asserts the inference typography §4.6 itself forbids. A drone's clock is routinely left on the operator's home zone while travelling — which is how this archive was made — so "local" can be wrong by hours, and wrong in the one way this feature exists to prevent. **Provenance's wording wins, and `local` joins the banned list (R4).** The shipped `format_datetime()` already prints no zone, but drops the seconds; keep them, because the seconds are what make a timestamp useful across a 26-frame set.

Where `OffsetTimeOriginal` exists, print it. **`[RULING — provenance R1 major]`** Where a satellite clock is present, print the **instant**, not the offset: `26 July 2026, 17:10:03 UTC (from the satellite clock in the file)`. Printing `12:10:03 −05:00` re-commits the localisation error — the offset measures how the camera's clock was *set*, not the zone of the place.

Never label it UTC without a satellite clock, never convert. `--infer-timezone` is off by default and sidecar-only; a coordinate gives the zone of the place, not the zone the clock was set to, so the inference answers a question nobody asked.

---

## 4. The exact strings

These are final. No label, separator or footnote clause is to be reworded during implementation.

### 4.1 Labels (style `gallery`, label column on)

`POSITION` · `ALTITUDE` · `RECORDED` · `CAMERA` (opt-in) · `TITLE` has no label.

Set as letterspaced capitals at `+0.14 em` in Segoe UI Semibold, right-aligned to the gutter, values left-aligned from it — the convention for spec plates, and far better than a left-aligned column ragged between `POSITION` (8) and `RECORDED` (8) and `ALTITUDE` (8). (These three happen to be equal length, which is a small gift; the right-align rule still holds for `CAMERA`.)

### 4.2 Field lines (default output, working example)

```
POSITION   51°28′40.1″ N   0°00′05.3″ W
ALTITUDE   19 m above take-off point   ·   207 m above sea level
RECORDED   26 July 2026, 12:10:03 by the camera's clock   ·   camera facing 048° NE
```

Separator between paired values: U+00B7 MIDDLE DOT with `0.16 em` pen advance each side — never U+2009, which is .notdef in four candidate faces (§3.2). Separator between the two coordinates: `0.42 em` pen advance.

**Measured widths in Palatino** (verified, `sum(getlength(c))` at the real integer size): line 1 / 2 / 3 = 372 / 654 / 895 px at 5x7 (measure 1950), 422 / 748 / 1019 at A4 (3260), 626 / 1103 / 1509 at 24x36 (10200). Every line fits with large margin on every sheet, so no shrink-to-fit is needed for the default field set.

`[CORRECTED — typography R1 minor]` Character budget handed to layout: field lines run **23–72 characters**, not the 23–50 the typography spec states. The over-50 cases are all ones the spec itself mandates: the `RECORDED` line with a heading (72), a panorama position line with an aggregation annotation (61), an interval timestamp (56), a worst-case tilt clause (53). Qualifiers wrap to a continuation line; they never truncate, and a break never falls inside a parenthesis.

### 4.3 Footnote — three lengths, composed from clauses

The footnote is assembled from clauses gated on what was actually printed, so it can never describe a field the plate omitted. `[CORRECTED — typography R3 blocking: "the no-GPS case silently prints a footnote claiming a position that was never read"]`

```
SOURCE   = "as recorded by the camera in {filename}."
PREFIX   = "Position, altitude and time "                 # iff position printed
PREFIX_NP= "Altitude and time "                           # iff position absent
PRECISION= " WGS 84, satellite position good to a few metres; heights barometric."
HEADING  = " Camera direction is the aircraft's own compass, not corrected for magnetic declination."
NOPOS    = " This file records no position."
COMPOSITE= " Stitched from {n} frames; position and time are the first frame's."
ZENITH   = " Sky above about +41° was not photographed and is filled from the sky below it."
ROUNDED  = " Position rounded to the nearest {r} before printing."
CLOSER   = " Metadata can be edited: this is the file's record, not proof of where the picture was taken."
```

`CLOSER` is **fixed, non-removable, and never shortened** — it is the one sentence that carries R1. 92 characters `[CORRECTED — provenance R1 and R2 both: the spec says "fixed at 91"]`.

**`short`** = `PREFIX + SOURCE + CLOSER`
**`standard`** (default) = `PREFIX + SOURCE + PRECISION + [HEADING] + [COMPOSITE] + [ZENITH] + [ROUNDED] + CLOSER`
**`full`** (back of print / `--footnote full`) = §7.2 below.

`[CORRECTED — provenance R1 major, provenance R2 major]` `PRECISION` must be in the **default** footnote, not only the full one. §3.1's entire justification for printing five decimal places is that "the honesty is carried by the stated uncertainty rather than by truncation" — on the default path the typography and provenance specs both omitted it, voiding the justification.

Measured: `standard` with heading = 232 characters = 2519 px in Palatino at the footnote size, which wraps to **2 lines on 5x7** and **1 line at A4 and above**. Budget accordingly.

`[CORRECTED — typography R3 major, typography R2 minor]` The footnote needs a **line budget and a measured wrapper**, not "the longest that fits on one line". Pillow does not wrap (`multiline_text` only honours existing newlines; `textwrap` counts characters, meaningless for a proportional face). Budget: 1 line for `short`, 3 for `standard`, 4 for `full`; pick the longest variant whose wrapped height fits; grow the band by one leading per extra line.

### 4.4 Named absences

`No position recorded in this file.` · `Date not reliably recorded.` · `No camera metadata found in this file.`

Never `Unknown`, never `N/A`, never `—`, never `0.00000`. `[CORRECTED — typography R1 minor]` U+2014 em dash is for the sidecar `.txt`'s label/value alignment only, never the plate; the typography spec allocated a code point for a behaviour §5 forbids.

### 4.5 Camera row (opt-in)

`DJI FC7303   ·   f/2.8  1/500 s  ISO 100  4.5 mm`

`[CORRECTED — typography R1 minor, provenance R1 minor]` **Never "Mavic Air 2" in the data block.** The file records `Make=DJI`, `Model=FC7303`; the marketing name comes from a lookup table, which makes it the same class of claim as a reverse-geocoded place name — and R1 rules those out: everything in the data block is transcribed from the file. The trade name may appear in the `full` footnote as `written by a DJI Mavic Air 2 (model FC7303)`, where it is scoped by "as recorded", or in the sidecar as `trade_name`. Implementation: `" ".join(filter(None, (cap.make, cap.model)))`.

---

## 5. Composition and type

### 5.1 The default composition: MAT

`[RULING]` Typography argues MAT; printmech's bordered print with a reserved band is the same composition reached independently; the shipped `plate.py` already does a bottom-weighted mat. All three converge. **MAT is the default**, and the quantitative argument is typography's and is decisive: over a bright sky (Y ≈ 0.85) white text is CR 1.04:1 unaided and needs a scrim at α = 0.91 — an essentially opaque black slab laid across the picture. Drone frames are mostly sky, so the instinctive "white text on a dark gradient" is the *worst* option for this archive specifically. On a mat every ink/ground pair is fixed at ≥7:1 with no scrim, on every image in the archive, forever. It also leaves the photograph untouched, which matters for files that cost a 38-minute stitch.

`bar` and `overlay` remain available. The shipped `STYLES` dict already has `overlay`; `bar` is new in phase 4.

### 5.2 Mat geometry, and the weighted mat that never happens

`[CORRECTED — typography R1 major, typography R3 major, both blocking-adjacent; I verified the algebra]`

Typography §2.3 computes `top = max(u, round(free / 2.35))` with `free = SH − Hi' − block_h` and `win_h = SH − 2u − block_h`. **Whenever the image's height binds the contain scale, `Hi' = win_h` exactly, so `free = 2u` identically, and `round(2u/2.35) = 0.851u < u` clamps to `u` — a flat mat, on every such sheet, for every aspect ratio, always.** On a derived sheet `SH` is *constructed* as `Hi' + 2u + block_h`, so `free = 2u` by construction and the mat is flat unconditionally. Neither of the spec's two worked examples achieves the 1:1.35 weighting it advertises as the style's headline property, and the spec only notices for the 5x7, where it proposes a printed apology rather than a fix.

**Normative — reserve the weighting in the window instead of splitting the residual:**

```
top    = mp
bottom = rpx(1.35 * mp)
box_h_px = Hp - top - bottom - gp - band_px
# then contain into (box_w_px, box_h_px); any slack stays at the bottom, where a framer puts it
```

This is deterministically weighted at the cost of a ~3.5 % smaller image, and the "sheet is tight for this aspect" warning disappears. The shipped code's 42/58 leftover split is the same instinct arrived at differently; replace it with the explicit reservation so the ratio is a stated design value rather than a residual.

**Derived sheet (the `--size auto` and `--long-edge` paths).** `[CORRECTED — typography R3 blocking: "the default path cannot be executed at all; the derivation is circular and unstated"]` `m` keys to the short edge while a derived short edge depends on `m` through `band_px`. Resolve as a stated fixed point, and key the margin to the **long** edge when deriving so the mat is aspect-invariant:

```
L_px = px(L_in, dpi)
m_px = rpx(0.045 * L_px)                       # aspect-invariant: 0.54 in at 12 in, every aspect
loop (max 6 passes, keep the last):
    band_px = max(measure_band(box_w_px, dpi), px(c_in, dpi))
    box_w_px = L_px - 2*m_px
    Wi', Hi' = contain(source, box_w_px, <unbounded>)
    Hp = m_px + Hi' + rpx(1.35*m_px) + gp + band_px
    recompute m_px from min(L_px, Hp) and L_px; repeat until stable
```

Verified by the typography reviewer to converge in 2–4 passes for 2:1, 4.68:1, 1.61:1, 1.23:1, 16:9, 9:16 and 10:1. Without the long-edge keying, the mat width varies 3× across aspects at the same print size (0.27 in on a 4.68:1 sweep against 0.78 in on a 1.23:1 partial).

### 5.3 Type scale — keyed to the diagonal

`[RULING]` Three rules are on the table: typography's `11.0 × (L_in/10)**0.35` keyed to the **long** edge, the shipped code's `9.0 × (short_in/11.69)**0.35` keyed to the **short** edge, and printmech is silent. I computed all three across every sheet, including the panoramic ones:

| sheet | diag (in) | **diagonal rule** | short-edge rule | long-edge rule |
|---|---|---|---|---|
| 5x7 | 8.60 | 7.00 pt (floored) | 6.69 | 9.71 |
| A3 | 20.25 | 9.00 | 9.00 | 13.12 |
| 24x36 | 43.27 | 11.74 | 11.58 | 17.22 |
| 10x30 | 31.62 | 10.52 | 8.52 | 16.16 |
| **6x36** | 36.50 | **11.06** | **7.13** | **17.22** |

**Ruling: the diagonal.** It agrees with the shipped short-edge rule to within 0.25 pt on every ordinary aspect from 5x7 to 24x36 — so it preserves a calibration the author already tuned by eye — and it is the only one of the three that behaves on panoramas. The short-edge rule starves a 6×36: 7.13 pt caption on a three-foot print. The long-edge rule gives that same sheet 17.22 pt, absurd on a sheet six inches tall, and gives a 12x36 exactly the type of a 24x36.

```python
REFERENCE_DIAG_IN = 20.25          # A3's diagonal, where base_pt lands unchanged
def caption_point_size(base_pt: float, paper: Paper) -> float:
    d = math.hypot(paper.width_in, paper.height_in)
    return max(7.0, base_pt * (d / REFERENCE_DIAG_IN) ** 0.35)
```

`base_pt = 9.0`. The 0.35 exponent is sub-linear on purpose: a caption is read at reading distance whatever the size of the work, so a 24x36 does not want 3.5× the type of an 8x10 — but it is read from slightly further back, so it does not want the same type either.

**Floor 7.0 pt, not 5.5.** `[CORRECTED — typography R1 major, typography R2 minor]` Both reviewers found the spec's pt floors silently dropped from §3.1 and both JSON blocks, and both found the claim that the floors "engage only at and below ≈6 in" wrong (they reach to ~7.3–7.6 in and ~9.5–9.8 in, so they govern the whole 5x7-to-8x10 range — the sizes most of this archive will be printed at). The shipped floor of 5.5 pt is too low. With 7.0 the smallest value cap measures **1.69 mm** (Palatino) — essentially the 1.70 mm the reviewer demanded for the value role, and the smallest sheet the design supports.

### 5.4 Role table — concrete, with measured cap heights

```python
@dataclass
class TypeScale:
    dpi: int
    base_pt: float = 9.0                     # already scaled by caption_point_size()
    @property
    def value_px(self)    -> int: return pt_to_px(self.base_pt, self.dpi)
    @property
    def label_px(self)    -> int: return pt_to_px(max(self.base_pt*0.72, 6.0), self.dpi)
    @property
    def title_px(self)    -> int: return pt_to_px(self.base_pt*1.55, self.dpi)
    @property
    def footnote_px(self) -> int: return pt_to_px(max(self.base_pt*0.68, 6.0), self.dpi)
    @property
    def line_px(self)     -> int: return int(round(self.value_px*1.55))
```

`[CORRECTED]` The shipped `footnote_px` factor is 0.62 with no floor; raised to 0.68 with a 6.0 pt floor, because the honesty footnote is the one line that must stay readable and typography §6.1's own analysis is that small type needs *more* contrast and size, not less.

Measured at 300 dpi (`cap` via `font.getbbox("H", anchor="ls")`, converted to mm):

| sheet | P_pt | value px | value cap | label px | label cap | footnote px | foot cap | title px | leading |
|---|---|---|---|---|---|---|---|---|---|
| 5x7 | 7.00 | 29 | 1.69 mm | 25 | 1.52 | 25 | 1.44 | 45 | 45 |
| 8x10 | 7.67 | 32 | 1.86 | 25 | 1.52 | 25 | 1.44 | 50 | 50 |
| A4 | 7.97 | 33 | 1.95 | 25 | 1.52 | 25 | 1.44 | 51 | 51 |
| A3 | 9.00 | 37 | 2.20 | 27 | 1.61 | 25 | 1.44 | 58 | 57 |
| 16x24 | 10.19 | 42 | 2.46 | 31 | 1.86 | 29 | 1.69 | 66 | 65 |
| 24x36 | 11.74 | 49 | 2.88 | 35 | 2.12 | 33 | 1.95 | 76 | 76 |
| 12x36 | 11.21 | 47 | 2.71 | 34 | 2.03 | 32 | 1.86 | 72 | 73 |

`[RULING — typography R1 major, partially rejected]` The reviewer demands every role clear 1.70 mm cap at the smallest sheet. Adopted for the **value** role (1.69 mm, met by the 7.0 pt floor). **Rejected for the footnote and label**, where it would force ~7.1 pt on a 0.50 in band: the 1.70 mm figure was derived for a value role read digit-by-digit, and a 6.0 pt footnote at 1.44 mm cap is small but legible in the hand. Stated honestly rather than quietly: this is the design's floor, and below 5x7 the plate refuses.

### 5.5 Fonts

`[VERIFIED]` All named files exist: `pala.ttf`, `seguisb.ttf`, `consola.ttf`, `segoeui.ttf`, `georgia.ttf`, `georgiai.ttf`, `cambria.ttc` (index 0 = Cambria Regular, no index argument needed), `constan.ttf`, `LBRITE.TTF`, `ARIALN.TTF`, `ebrima.ttf`, `times.ttf`, `calibri.ttf`.

| role | font | file | why |
|---|---|---|---|
| Title | Palatino Linotype | `pala.ttf` | book face, not a UI face |
| **Value (gallery)** | **Palatino Linotype** | `pala.ttf` | tabular lining figures, has all four marks |
| Label | Segoe UI Semibold | `seguisb.ttf` | holds letterspaced capitals at 6 pt |
| Footnote | Palatino Linotype | `pala.ttf` | same family as the values it qualifies |
| **Value (survey)** | **Consolas** | `consola.ttf` | the column is a table (below) |
| Prose (field-note) | Georgia | `georgia.ttf` | old-style figures are right in running prose |

**Why Palatino over Georgia for the gallery numerals.** `[VERIFIED]` Georgia's figures are old-style *and* proportional, and `PIL.features.check('raqm')` is **False** on this build — no HarfBuzz, no `lnum`/`tnum` feature access, so there is no way to fix it. Numbers bounce and never column up. Palatino's ten digits share a single advance at every size tested. Rejected with reasons: **Constantia, Lucida Bright, Ebrima render .notdef for U+2032/U+2033** (verified — Lucida Bright also lacks U+2212 and U+00B7); Times is acceptable but reads as a default; Cambria is a genuine second choice, offered as `--serif cambria`.

`[CORRECTED — against the shipped code, missed by all four reviewers]` **`typeset.py::FONT_STACKS` has two latent tofu bugs.** `"serif_tab": ("LBRITE.TTF", "pala.ttf", "constan.ttf")` puts first a face missing U+2032, U+2033, U+2212 **and** U+00B7 — every mark a coordinate line uses. And `"serif": ("constan.ttf", "georgia.ttf", ...)` leads with a face missing both primes. Reorder:

```python
"serif_tab": ("pala.ttf", "cambria.ttc", "times.ttf"),
"serif":     ("pala.ttf", "georgia.ttf", "times.ttf", "constan.ttf"),
```

**Font whitelist at load time.** `[CORRECTED — typography R2 minor, who found the mechanism the spec proposed cannot work]` `font.getmask(ch)` returns an `ImagingCore` and `==` on it is identity comparison — I confirmed two byte-identical masks compare `False`. And Pillow exposes no way to address glyph 0, so the reference must come from an unmapped codepoint:

```python
NOTDEF_PROBE = "\U000E0100"
def missing_glyph(font, ch) -> bool:
    return (bytes(font.getmask(ch)) == bytes(font.getmask(NOTDEF_PROBE))
            and font.getlength(ch) == font.getlength(NOTDEF_PROBE))
```

Check U+2032, U+2033, U+00B0, U+2212, U+00B7 — and note that per §3.2 the plate emits **no space characters at all**, so U+2007/2009/200A/2002 need no check because they are never used. Refuse a role's font that fails, fall back down the stack, log once.

The shipped `has_glyphs()` uses `getmask(ch).getbbox() is None`, which catches only glyphs that render nothing — a .notdef **box** has ink and passes. Replace it with the above.

**`--serif cambria` is viable**; name the file and index in the help, since `cambria.ttc` is the only `.ttc`.

### 5.6 Mechanics: kerning, tracking, cap height, ink bearings

`[CORRECTED — typography R2 major, who contradicted the spec and was right; I re-measured and confirm]` **Pillow's basic layout DOES kern.** Measured, joint minus sum of parts at size 100:

| font | AV | Ta | P. | LA |
|---|---|---|---|---|
| pala.ttf | −0.156 | −0.125 | −0.203 | 0.000 |
| times.ttf | −0.203 | −0.109 | −0.172 | 0.000 |
| segoeui.ttf | −0.094 | −0.172 | −0.250 | **+0.047** |
| seguisb.ttf | −0.094 | −0.156 | −0.234 | **+0.047** |
| **georgia.ttf** | 0.000 | 0.000 | 0.000 | 0.000 |
| **consola.ttf** | 0.000 | 0.000 | 0.000 | 0.000 |

Pillow's fallback layout calls `FT_Get_Kerning`, so legacy `kern`-table pairs apply. Georgia and Consolas carry no kern table, which is why probing Georgia alone — as typography §0.6 did — could not detect it. Rounding does not produce a *positive* delta on `LA` only.

The conclusion survives for a different reason: the magnitude is ~0.0016 em, below the integer-pixel grid Pillow snaps origins to, so glyph-by-glyph drawing is visually free. **But all width arithmetic must use `sum(getlength(c))` consistently and never mix it with `getlength(whole_string)`** — the divergence reaches +0.55 px on a long Segoe UI run, which breaks the 0.2 px tolerance typography §9.4 pins.

`[CORRECTED — typography R2 major; I re-measured]` **Digit advances are integer-hinted; "tabular at exactly 0.500 em" is a design value, not a measurement.** Palatino's digit advance in em: 0.5200 at 25 px, 0.5172 at 29, 0.5161 at 31, 0.5000 at 34/40/46/48/50/72/80. Consolas ranges 0.5385–0.5600 across the same sizes. **The property that matters survives: all ten digits share one integer advance in Palatino, Consolas, Cambria and Segoe UI Regular at every size tested**, so columns align without decimal logic. **Rule: no width is ever computed as em × px. Every fit decision calls `text_width()` on the font instance at its actual integer size.**

`[CORRECTED — typography R1 major, typography R2 blocking, both]` **Do not round `track_px` to an integer.** The stated justification — avoiding a compounding of two fractional sources — is refuted by the measurements above (a Palatino numeral run has no fractional advances at most value sizes), and the cost is that the specified em values are not what gets drawn: `−0.005 em` on the title rounds to 0 px on every sheet up to 24 in, and `+0.015 em` on the secondary rounds to 0 px at 5x7 and 8x10. Keep `track_px = track_em * font.size` as a float in the pen accumulator and round only the draw origin — which bounds the total error at ±0.5 px either way, because Pillow snaps every origin to an integer pixel regardless.

`[CORRECTED — typography R1 minor, typography R2 minor]` **Cap height must be measured, never the constant 0.694.** Verified at 72 px: Palatino 0.6806 em, Consolas 0.6389, Cambria 0.6667, Georgia 0.6944, Segoe UI 0.6944 — and Palatino varies with size (0.6897 at 29 px, 0.6800 at 50). Hard-coding 0.694 breaks `gap` for every face the spec itself offers and makes the survey north arrow 8.8 % taller than the Consolas digits it annotates.

```python
def cap_px(font) -> int:
    bb = font.getbbox("H", anchor="ls")      # verified working under basic layout
    return -bb[1]
```

**Every text call uses `anchor="ls"`** (baseline) and derives cap positions from `cap_px`. The default `"la"` leaves invisible leading that varies from 0.05 em (Palatino) to 0.38 em (Segoe UI).

`[CORRECTED — typography R1 minor, typography R2 minor, both]` **`ink_rsb` is specified in §7.2 and implemented nowhere.** The right-aligned survey label column — the one place the correction was asked for — gets nothing, and so does the gallery spread's right column. `getbbox` returns left = 0 for every glyph (verified: Palatino at 100 px gives `(0, 6, 50, 74)` for `'4'` whose true ink lsb is 0, and `(0, 6, 50, 74)` for `'1'` whose ink lsb is 6), so both bearings must come from rasterising:

```python
@functools.lru_cache(maxsize=512)
def ink_bearings(font_path: str, size: int, ch: str) -> tuple[int, int]:
    """(lsb, rsb) in px, measured from ink. getbbox is the LAYOUT box horizontally."""
    f = ImageFont.truetype(font_path, size)
    pad = size * 2
    im = Image.new("L", (pad * 2, pad * 2), 0)
    ImageDraw.Draw(im).text((pad, int(pad * 1.4)), ch, font=f, fill=255, anchor="ls")
    cols = np.where(np.asarray(im).max(0) > 16)[0]
    if len(cols) == 0: return 0, 0
    return int(cols[0] - pad), int(pad + f.getlength(ch) - (cols[-1] + 1))
```

Palatino's measured ink lsbs at 100 px: `4` 0, `T` 2, `0` 3, `1` 6, `A` 1 — so a caption whose first line starts `41°` and whose second starts `19 m` is ~6 px out of alignment at value size, visible at 300 dpi.

**Revised `draw_tracked`** (replaces the shipped version, which rounds nothing, keeps no float accumulator, and applies no optical correction):

```python
def draw_tracked(draw, xy, segments: list[Segment], font, fill, *,
                 track_em: float = 0.0, align: str = "l", optical: bool = True) -> float:
    """Draw a segment list with letterspacing and inter-segment pen advances.

    segments is [(text, em_advance_after), ...] from the formatters -- never a string
    containing a space character. No-track characters hug their numbers.
    """
```

With:

- `track_px = track_em * font.size`, float accumulator, `round(x)` only at draw time.
- `[CORRECTED — typography R1 minor]` **Marks hug their numbers.** `draw_tracked` as specified adds track after every character including between `8` and `′`, reproducing in miniature the very defect typography spends a section condemning Consolas for. Skip the track when either neighbour is a mark: `MARKS = set("0123456789") | {"°","′","″","."}` → no track when `c in MARKS or nxt in MARKS`.
- `align="r"` subtracts `tracked_width` then adds the final glyph's `rsb`; `align="l"` subtracts the first glyph's `lsb`.
- No trailing track: width is `Σ advance + track × (n−1)`.

**Faux small caps:** the shipped `draw_small_caps` with 0.78 ratio is retained; it is close to the measured-optimal 0.74 and the spec's own measurement shows only a 5.2 % stem loss, so the regular face is right and Bold (40 % heavier) is not. Use for at most one short word; prefer letterspaced full caps.

### 5.7 Colour, contrast, keyline, rule

| token | light plate | CR vs ground | dark plate | CR |
|---|---|---|---|---|
| ground | `#FAF8F4` | — | `#14161A` | — |
| primary ink | `#1A1714` | **16.83:1** | `#F4F2EE` | **16.20:1** |
| secondary ink | `#4A443C` | **9.07:1** | `#BDB8AE` | **9.17:1** |
| footnote ink | `#5A544A` | **7.07:1** | `#ADA79D` | **7.58:1** |
| hairline | `#CBC5BE` | ≈30 % ink | `#3A3D42` | ≈30 % |
| keyline | `#D6D1C9` | ≈22 % ink | `#2A2D32` | ≈22 % |

Every pair clears 7:1 (WCAG relative luminance on linearised sRGB). `#6E675C` measures 5.27:1 and is rejected — small type needs more contrast, not less.

`[CORRECTED — against the shipped code]` `plate.py::STYLES["gallery"]` uses `ink_soft = (122,118,112)` = `#7A7670`, which measures **4.84:1** against its `#FAF8F4` ground. That is below 7:1 and it is the colour the footnote is drawn in. Replace with `#5A544A` for the footnote and `#4A443C` for secondary values.

The mat is deliberately not `#FFFFFF`: a paper-white mat beside a photograph with a blown sky makes the sky look grey. `--mat paper` gives true white for inkjet work where laying ink across the whole mat is wasteful. `[RULING]` Printmech §4 requires the paper surround to be exactly `(255,255,255)` because on an inkjet 250 means a faint grey wash. That argument is sound for a *borderless* surround beyond the trim; it is not an argument against a designed warm mat, which is ink on purpose. **Both: the mat is `#FAF8F4`; any bleed extension beyond the trim is `#FFFFFF`.**

**Keyline on by default on light mats:** `#D6D1C9`, `max(2, rpx(0.5*dpi/72))` px (2 px at 300, 4 at 600), immediately outside the image rect. Drone frames have bright skies and the image's top edge otherwise dissolves into the mat. Off on dark mats.

**Exactly one hairline**, between the data block and the footnote, because it marks the boundary between what the file says and what we say about what the file says — the only ornament in the design that carries meaning. `#CBC5BE`, **`max(2, rpx(0.5 * dpi / 72))` px** `[CORRECTED — typography R1 major]`: the spec's `max(2, round(0.5 pt))` omits the pt-to-px conversion it states two sections earlier, evaluates to 0, and pins the rule at 2 px forever — at 600 dpi it would print 0.24 pt, half the keyline beside it. And §8.2's `max(1, keyline/2)` evaluates to 1 px at 300 dpi, which is the 1/300 in hairline §6.4 says will vanish in print. Survey group separators: `#CDCBC7` at `max(2, rpx(0.35*dpi/72))`, taking the lighter reading from value, not from width. **Assert in test that no rule or keyline is ever emitted below 2 px at any dpi.**

The shipped code draws the rule at `width=max(1, dpi//300)` = 1 px at 300 dpi. Fix.

**Survey group separators, not per-row rules** `[CORRECTED — typography spec's own self-correction, retained]`: four groups (position / altitude / time / camera), plus one above the first row and one below the last. Rules between every row read as a ledger.

### 5.8 Overlay: automatic polarity

`[CORRECTED — against the shipped code, which is exactly the failure typography measured]` `_render_overlay()` computes `alpha_max = 255 * (0.30 + 0.45 * lum)` and always composites **black**. So the brighter the band, the heavier the black slab — reaching α ≈ 0.72 over a bright sky, over the whole lower band of a photograph whose sky is the reason the slab is needed. That is the single worst option for aerial photography.

**Normative:** sample the band the plate will occupy, downsample to 64 px short side, linearise, take percentiles, and solve for the required opacity on both polarities **against the weakest ink the plate will use (the footnote, 7:1), not the primary**:

```python
Y_lo, Y_hi = percentile(Y, 10), percentile(Y, 90)
Y_req_L = 7.0 * (Y_ink_dark + 0.05) - 0.05          # = 0.3618
Y_req_D = (Y_ink_light + 0.05) / 7.0 - 0.05         # = 0.0842
a_L = 0.0 if Y_lo >= Y_req_L else (Y_req_L - Y_lo) / (Y_panel_light - Y_lo)
a_D = 0.0 if Y_hi <= Y_req_D else (Y_hi - Y_req_D) / (Y_hi - Y_panel_dark)
polarity = "light" if a_L <= a_D else "dark"
alpha = clamp(min(a_L, a_D), 0.18, 0.72)
```

Two rules fall straight out, and they are the whole argument: **above Y = 0.362 a light plate needs no scrim at all** (dark ink on a bright sky is CR 15.3:1 unaided); **below Y = 0.084 a dark plate needs none**. Crossover at Y = 0.1165 (sRGB ≈ 96). Above the 0.72 ceiling a "scrim" has become an opaque slab: **abandon the overlay, fall back to MAT, and say so in the log.** High-variance guard: if `Y_hi − Y_lo > 0.50`, force a solid panel or move to the quietest corner first.

**Scrim spans the full image width and ramps to zero upward — never vertical edges.** `α(t) = α_target · t^1.6` over `4 × leading`. A floating rounded box at low α reads as a smudge. The shipped code already spans full width and feathers at `t**1.45`; keep the shape, change the polarity and the exponent.

`[CORRECTED — typography R3 blocking]` Overlay geometry must not borrow `block_h` from the mat, which contains `gap` (clear space from the image edge to the first baseline) and double-counts the padding `pad_y` already provides — a third of the scrim on an 8192×4096 equirect would be empty space. Use `block_text = sum of leadings + ceil(0.29*footnote_px)` and `scrim_h = block_text + 2*pad_y + 4*leading`. `m` is defined only for MAT and BAR.

`[CORRECTED — typography R3 major]` Overlay type must be intrinsic to the image, not keyed to a print size that does not exist. For `--composition overlay` with no paper: `value_px = clamp(rpx(0.021 * min(Wi, Hi)), 18, 160)`. Otherwise the same 48 px lands as 1.17 % of the short edge on an equirect (illegible at 1080 px wide) and 4.44 % on a square crop.

### 5.9 Descender allowance

`[CORRECTED — typography R2 minor]` `round(0.29 * 50)` is exactly 14.5 and Python's banker's rounding returns **14**, not 15. A descender allowance must never round down — rounding down clips the descenders the allowance exists to protect. Use `math.ceil(0.29 * footnote_px)`. Elsewhere use `rpx()` from §2.1.

### 5.10 Rendering resolution

Render at the target dpi and downscale for preview with `Image.LANCZOS`. Rendering at 96 dpi and upscaling collapses the grid. Peak memory ≈ `canvas_Mpx × 3 B × 2` (canvas plus the resized image before paste) **+ the decoded source**: 77.8 Mpx → 467 MB + 101 MB for an 8192×4096 source ≈ **570 MB** `[CORRECTED — printmech R1 minor: the spec's "233 MB + 25 MB" mixes one buffer with the ×2 formula and uses the source's file size rather than its decoded size]`. The 250 Mpx guard therefore caps near 1.6 GB, which is what `--allow-huge` is really gating.

---

## 6. Provenance: what is printed, what is not

### 6.1 Field set and priority

| # | field | source | gallery | survey | slot |
|---|---|---|---|---|---|
| 1 | lat/lon, DMS | `GPSLatitude`/`GPSLongitude` | **on** | **on** | POSITION |
| 2 | altitude above take-off | `XMP:RelativeAltitude` | **on** | **on** | ALTITUDE |
| 3 | altitude above sea level | `XMP:AbsoluteAltitude` / `GPSAltitude` | **on** | **on** | ALTITUDE |
| 4 | date + time | `DateTimeOriginal` | **on** | **on** | RECORDED |
| 5 | camera facing | §3.4 selector | on (single frames) | on | RECORDED |
| 6 | lat/lon decimal | derived | **on** (footnote) | **on** (column) | — |
| 7 | honesty footnote | fixed | **on, required** | **on, required** | — |
| 8 | title | **user only** | on iff `--title` | on iff `--title` | TITLE |
| 9 | source digest, 16 hex | computed | on (phase 3) | on (phase 3) | footnote |
| 10 | camera + exposure | EXIF | off | **on** | CAMERA |
| 11 | pixel hash | computed | off | opt-in | footnote |
| 12 | **aircraft serial** | `SerialNumber` | **off** | **off** | never |

**#12 is off everywhere on privacy grounds.** `<aircraft serial>` identifies the specific aircraft and would appear on anything the user hangs or posts; it links prints to each other and to the owner; it is uncheckable by anyone who does not already hold the user's other files; and printing it hands a forger a real serial. Same reason #8 is never reverse-geocoded — that is a network call and an inference the file does not make.

`[CORRECTED — against the shipped code]` `_print_cmd.py::write_sidecar()` writes `"serial": cap.serial` unredacted by default, into a file that travels with the print. Default becomes `serial: null, serial_redacted: true, device_fingerprint: "<6 hex>"`, with `--serial` to include it. `[CORRECTED — provenance R2 minor]` Keep the serial and its fingerprint mutually exclusive; carrying both makes the fingerprint pointless.

`[CORRECTED — provenance R1 major]` `device_fingerprint` = first 6 hex of `sha256("panolib-device:" + serial)`. I computed it for the working example: **a value derived from the real serial (withheld here)**, not the `7b42e1` the spec prints as "the example's real values" — a fabricated value in a document whose second rule is "never invent a value", and the obvious source for an implementer's golden test. And the privacy claim needs its limit stated: the salt is a published constant, so the fingerprint is a *commitment* to the serial, not a concealment. Anyone holding a candidate serial can confirm the match in one hash.

### 6.2 Title: the only user words, and the only role with no geometry

`[CORRECTED — typography R1 minor, typography R3 major]` The title is simultaneously a layout rule and an honesty rule — a reader can tell at a glance which marks are the file's claim and which are the author's caption. But the specs give it no geometry (`block_h` has no title term, no title baseline in the ladder), and both named styles list `title` in `fields_off` while `--title` is a documented flag — so the flag is a no-op and §3.4's title-becomes-primary behaviour is dead code. Worse, the packer's drop order puts `title` ahead of transcribed fields, so the user's caption is sacrificed first.

**Normative:**
- Title sits at the head of the caption block, above the data rows. Baselines: `b_title = gap`, then rows at `gap + title_px*1.35 + k*leading`. `band_px` gains `title_px * 1.35` when a title is present. (This is what the shipped `_draw_caption` already does; it just was not written down.)
- `--title TEXT` sets `facts.title` **and** enables the field. The style key is `fields_on_if_supplied: ["title"]`, not `fields_off`.
- `PROTECTED` includes `title`.

### 6.3 Degradation

```python
REQUIRED  = {"position", "clock"}       # ENFORCED at the top of pack(), not merely declared
PROTECTED = {"position", "alt_rel", "alt_asl", "date", "time", "honesty", "title"}
ORDER     = ["position","alt_rel","alt_asl","date","time","dd","honesty","facing",
             "title","camera","exposure","source","hash"]
```

`[CORRECTED — typography R3 blocking]` The spec declares `REQUIRED` and never references it, so a frame with no GPS silently renders a plate carrying altitude, date, time and a footnote claiming "Position, altitude and time as recorded…" — false. Three fixes, and the third is the ruling:

1. Enforce `REQUIRED` at the top of `pack()`.
2. **`[RULING]` Degrade, do not refuse, when position alone is missing.** Typography §5 says refuse on missing coords; typography R3 says refusal denies the user the print over one field and GPS is routinely absent from re-exported JPEGs. The brief scopes this to *any* image. So: render the sheet, print `No position recorded in this file.` where POSITION would be, and gate the footnote on what was printed (`PREFIX_NP + SOURCE + NOPOS + CLOSER`). **Refuse only when position *and* clock are both gone** — a plate with neither has no falsifiable claim on it at all, and that is the case where it "carries the apparatus of attestation with nothing behind it". `--require-position` restores the strict behaviour.
3. `[CORRECTED — typography R3 blocking]` The **0,0 trap**. `docs/BUILD_SPEC.md:697` already rules for the panorama path: "treat `|lat| < 0.001 and |lon| < 0.001` as no-fix (the classic 0,0 trap)". A DJI with no fix writes 0,0 — it does not omit the tags — so `lat is None or lon is None` never fires and the plate prints `0.00000° N 0.00000° E`, a point 600 km off Ghana, which is the exact string the spec promises never to print. Detection: `lat is None or lon is None or (abs(lat) < 0.001 and abs(lon) < 0.001)`. `Capture.has_position` must adopt this. The sidecar preserves the raw 0,0 with `absent_reason`, so `as_opt_float`'s absent/zero distinction earns its keep in the record rather than on the face.

`[CORRECTED — typography R3 major]` **The drop must be slot-aware.** `fits()` is global, so a footnote overflow currently deletes `facing` — a RECORDED-slot field that changes the footnote measure by zero — before deleting `dd`, which actually fixes it. Have `fits()` return the offending slots; drop only candidates in those slots; try the footnote wrap and then the stacked layout before touching another slot.

**Field-specific:** only one altitude → the survivor keeps its full phrase and the `·` disappears; never "Altitude: 207 m". No date → the whole RECORDED slot empties; a bare time is never shown. No gimbal pitch → heading omitted (§3.4).

### 6.4 Panorama composite disclosure

`[CORRECTED — typography R3 major, provenance key decision]` A 26-frame sphere is not one exposure, and the plate must say so (R3). Position, time and height are properties of an interval.

`[CORRECTED — provenance R1 major; I verified against build.py]` **But "median of 26 frames, spread 2 m" is unsupportable from a stitched output.** `build.py:187` takes `first = tiles[0]`, writes `-GPSLatitude={tile.lat}` from it, and the library records `"captured": tiles[0].datetime`, `"altitude_rel": tiles[0].rel_altitude`. `capture.py`'s own docstring states it: "A stitched panorama inherits its GPS and timestamp from the first tile." So running the plate on an equirect yields tile 1's coordinate wearing the words "median of 26 frames" — a specific false claim about how the printed number was computed, and exactly the borrowed-value failure R2 forbids. The spec's own example also prints a 2 m spread under a rule that suppresses anything below 3 m `[CORRECTED — typography R1 minor, provenance R2 minor, both]`.

**Normative:**
- `read_capture()` on an equirect returns `kind="single"` with `aggregation="first frame"`. The footnote gains `COMPOSITE` (§4.3) with the frame count from the library: `Stitched from 26 frames; position and time are the first frame's.`
- A median-and-spread annotation is reachable **only** from `read_provenance_set(meta_json_path)`, which has the per-tile data. Print the spread only above 3 m; coarsen the printed precision when the across-tile spread exceeds the printed step, so a drifting sweep cannot print 0.1″. Apply the same rule and the same 3 m threshold to altitude `[CORRECTED — provenance R2 minor: the spec aggregates position and time and leaves height bare]`.
- `ZENITH` clause required whenever `--no-fill` was not used and the printed view can show the cap.
- `COMPOSITE` and `ZENITH` are in `PROTECTED`. If space forces a choice, dropping the composite disclosure turns a candid plate into a misleading one.

### 6.5 Metadata written onto the output

`[CORRECTED — typography R3 major; I verified build.py writes these tags]` **Never a blanket EXIF copy.** `build.py:89–99` writes GPano onto every equirect: `ProjectionType=equirectangular`, `UsePanoramaViewer`, `CroppedArea*`, `FullPano*`, `PoseHeadingDegrees`. A matted sheet with a keyline and a caption is no longer an equirectangular projection, so a blanket copy makes Google Photos, Facebook and every VR viewer wrap the mat and the caption around a sphere and hand the user a broken 360. The same copy re-injects the serial that §6.1 keeps off the face "because it would appear on anything the user hangs or posts".

**Copy an explicit allowlist:** `GPSLatitude/Ref`, `GPSLongitude/Ref`, `GPSAltitude/Ref`, `DateTimeOriginal`, `OffsetTimeOriginal`, `Make`, `Model`, `FNumber`, `ExposureTime`, `ISO`, `FocalLength`, `XMP:RelativeAltitude`, `XMP:AbsoluteAltitude`, and the heading tag actually used. **Strip explicitly:** every GPano tag and `SerialNumber`. **Set `Orientation=1`.** Add `XMP-xmp:CreatorTool = "panolib print <version>"`, `XMP-dc:Source = <absolute source path>`. List the copied and stripped groups in the sidecar, so the output's metadata is auditable too. Test: a plated equirect carries no `ProjectionType`.

**Two dpi traps, both measured by printmech and both real:**

1. **JPEG:** `dpi=(d,d)` sets JFIF density, but the EXIF block must agree or exiftool and Photoshop read `XResolution: 1, ResolutionUnit: None` from DJI's own tags and the lab prints the wrong size. Set `ex[0x011A] = ex[0x011B] = IFDRational(d,1)`, `ex[0x0128] = 2`.
2. **TIFF:** never pass `exif=` — Pillow 12.3 + libtiff raises `RuntimeError: Error setting from dictionary` after rejecting `EXIFIFDOffset`. Write provenance into the TIFF with exiftool afterwards, which is already a dependency.

### 6.6 Output format

| format | when | settings | measured size (28.8 Mpx) |
|---|---|---|---|
| **TIFF** (master) | labs, archiving | `compression="tiff_adobe_deflate"`, `tiffinfo={317: 2}` | **12.3 MB** (18.1 without predictor, 23.7 LZW, 86.4 raw) |
| **JPEG** | upload limits, email | `quality=95`, `subsampling=0`, `optimize=True`, `progressive=False` | 3.6 MB |
| **PDF** | "a PDF at size" | `resolution=dpi`, `quality=95` | 3.2 MB |

4:4:4 because 4:2:0 would blur the caption's glyph edges; baseline because some lab RIPs reject progressive. **PDF is never the master**: Pillow stores `DCTDecode` and drops the ICC profile entirely.

`[CORRECTED — printmech R1 minor]` The shipped `save_print()` uses `compression="tiff_lzw"` (23.7 MB); switch to Adobe Deflate + Predictor 2 for 12.3 MB at 0.28 s extra.

**8-bit sRGB, ICC always embedded.** `[VERIFIED]` Embed `C:\Windows\System32\spool\drivers\color\sRGB Color Space Profile.icm` — exists, 3144 bytes. Fallback `ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()` is a **588-byte minimal generated profile**, not an equivalent; say so. No AdobeRGB/ProPhoto/P3 is installed (29 profiles total).

`[CORRECTED — typography R2 major, printmech §0, both]` **16-bit output is out of reach.** Verified: `Image.fromarray(np.zeros((4,4,3), np.uint16))` raises `Cannot handle this data type: (1, 1, 3), <u2`. There is no `RGB;16` mode; only `I;16` greyscale saves as a 16-bit TIFF, and a warm mat with a colour photograph is not greyscale. Offer **8-bit TIFF** instead; if 16-bit is ever genuinely wanted it requires `tifffile`, and `ImageDraw` cannot antialias text into a 16-bit colour buffer anyway.

**No brightness compensation by default.** Prints look darker than screens because an emissive display runs 100–300 cd/m² against a reflective print at 60–120 under room light — a viewing-condition difference, not an error in the file. Baking a curve in double-corrects on any properly profiled lab, is irreversible, and makes the file stop being a faithful rendering of what the tool produced, which matters more than usual here. `--print-lift G` (0.00–0.15, default 0.00) applies `out = in ** (1/(1+G))` in linear light, **image only, never the mat**, and tags the filename `-lift<G>`.

`--soft-proof <icc>` writes an **additional** proof JPEG. `[CORRECTED — printmech R1 major, printmech R2 minor, both; I verified the API]` The specified `buildTransformFromOpenProfiles(srgb, paper, ...)` is the wrong call: it converts pixels *into* the printer's device space, so writing them to a JPEG tagged sRGB displays garbage, and `SOFTPROOFING` is inert without a third profile. `ImageCms.buildProofTransform` exists in Pillow 12.3 with the needed signature, and `ImageCms.Flags.SOFTPROOFING` / `.BLACKPOINTCOMPENSATION` both exist (bare names would `NameError`):

```python
ImageCms.buildProofTransform(srgb, srgb, paper, "RGB", "RGB",
    renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
    proofRenderingIntent=ImageCms.Intent.PERCEPTUAL,
    flags=ImageCms.Flags.SOFTPROOFING | ImageCms.Flags.BLACKPOINTCOMPENSATION)
```

sRGB in, sRGB out, paper as the proof — so the result stays viewable and honestly sRGB-tagged. **15** Canon PRO-200S paper profiles are installed to proof against `[CORRECTED — printmech R1 minor: the spec says 17; I counted 15 of 29 files]`, e.g. `CN_PRO-200S_S1MkII_PhotoPaperProLuster.icc`.

### 6.7 Sharpening

```
if s < 1.0:
    radius    = clamp(0.8 * file_dpi/300, 0.5, 0.9)      # px
    percent   = clamp(rpx(80 * (1 - s)), 20, 80)
    threshold = 2
else: nothing
```

Measured: acutance ×1.10 at s = 0.674, ×1.26 at s = 0.337, **zero added clipped channels** both times. Threshold 2 keeps the operator off the JPEG noise in smooth sky. Upscales get nothing — nothing was discarded, so there is nothing to restore, and USM would turn Lanczos ringing into a visible halo while advertising absent detail.

**Resample in gamma-encoded sRGB by default.** Measured against a true linear-light resize on a real 8192×4096 equirect: mean difference 0.14 of 255 levels, p99 = 1 level, only 0.06–0.28 % of pixels differ by more than 3 (specular sun edges). Linear light costs 3–4.5× the time (3.25 s vs 0.73 s) for a difference below a print's own reproduction error. `--linear-resample` for frames with hard speculars.

**Sharpening and lift apply to the image only, before compositing. The plate's text is drawn at final resolution and is never resampled or sharpened.**

### 6.8 Digest, checks, ledger, QR, signing (phase 3)

**Digest — print it.** `sha256` of the source bytes; first 16 hex grouped 4×4 (`c3f1 a9e4 b70d 5a62`), full 64 in the sidecar. It establishes that a file someone holds is byte-identical to the one this print was made from — which resolves "which of my 470 frames is this, and has it been touched since". It does **not** establish that the file is unedited since capture (edit first, hash second), when the file came into existence, or that the printer took it; and a forger reprinting a plate simply prints their own hash. The printed digest defends against substitution of the file behind the print, not forgery of the print. Truncation at 16 hex is typographic, not a security parameter — all real checking happens against the sidecar.

**Checks — sidecar and terminal only, never a face badge (R6).** Vocabulary fixed at `agree / disagree / unknown / not_available / not_read / note`, one spelling everywhere `[CORRECTED — provenance R1 minor: §2.7 and §7.3 use different spellings]`.

| check | on the example | why it bites |
|---|---|---|
| `GPSAltitude` vs `AbsoluteAltitude` | 206.5 vs 206.58, differ by 0.08 m | most editors write EXIF and leave the DJI XMP block alone |
| `AbsoluteAltitude − RelativeAltitude` | 187.18 m | compare against terrain; edit either altitude alone and this goes somewhere silly |
| `camera_xmp_block_present` | true | a resave almost always drops the XMP block while keeping EXIF |
| `pixel_size_known_for_model` | 4000×2250 ∈ {4000×3000, 4000×2250, 8000×6000} | a substituted frame usually breaks it |
| `clock_vs_satellite_time` | `not_read` | the strongest check of the set, if the tags are present |

`[CORRECTED — provenance R2 minor]` Tolerance on the altitude pair is **0.15 m**, not 1.0 m: both figures come from the same barometer and `GPSAltitude` is quantised to 0.1 m, so the expected disagreement is at most 0.05 m. A 1 m window lets an editor move `GPSAltitude` by a full metre — enough to shift the printed figure — and still report `agree`. Report the margin, not just the verdict.

`[CORRECTED — provenance R2 major]` **Drop focal length, aperture and `Type=N` from the model check.** The Mavic Air 2 has a fixed 4.5 mm f/2.8 lens, so those are constants of every frame the camera ever wrote and discriminate nothing; `Type=N` is a shot mode, so a Type=P tile would be flagged for no reason; and 4000×2250 is one of three legal sizes, so the owner's own 4:3 and 48 MP frames would come back `disagree`. A check that fires on correct files trains the user to ignore the column.

`[CORRECTED — provenance R1 minor]` `clock_vs_satellite_time` reports **`not_read`**, not `not_available`: `exif.py::TAGS` does not request `GPSDateStamp`/`GPSTimeStamp`, so absence of a read is being reported as absence from the file. `[CORRECTED — provenance R2 minor]` And the prescription targets the wrong module — `capture.py::PHOTO_TAGS` **already requests** `GPSDateStamp` and `GPSTimeStamp`, and `read_capture` is the reader a standalone plate uses. Add `OffsetTimeOriginal` and the composite `GPSDateTime` to `PHOTO_TAGS`, not to `exif.TAGS`. `[CORRECTED — provenance R1 major]` Drop `UTCAtExposure` entirely: exiftool 13.59 defines no such tag, so it would be a permanent silent no-op — and a named-tag request exiftool does not know exits 0 with no output, so the diagnostic command the spec hands the user cannot distinguish "absent from the file" from "never looked". Use `exiftool -a -G1 -s -time:all -gps:all -XMP-drone-dji:all <file>` instead.

`[CORRECTED — provenance R2 minor]` Derived UTC offset: snap to the 15-minute grid only when the residual is under 2 minutes (India +05:30 and Nepal +05:45 both exist, so 15 not 30); otherwise report `disagree` with the residual and fall back to the zone-free wording. Record `offset_residual_s` always, so the snap is auditable.

**Solar geometry — sidecar only, print neither.** The only measure that lets a viewer check the *picture* against the *claim* rather than metadata against metadata: shadow direction comes from the scene and is independent of EXIF. `[CORRECTED — provenance R1 major, provenance R2 major, who independently recomputed and agree]` The spec's hand figures are 7.3° out while claiming one-degree accuracy. Correct values (NOAA algorithm, at the example frame's recorded position, withheld here, 2026-07-26): read as UTC−05:00 → azimuth **152.7°**, elevation **65.3°**, shadows toward **333°**, length ratio 0.46; read as UTC → azimuth **77.4°**, elevation **15.2°**, shadows toward **257°**, ratio 3.69. The qualitative conclusion survives intact — 65° and 15° are not confusable in a photograph, so the frame itself settles the time zone. Use a real solar routine and state `accuracy_deg: 0.1`.

**Ledger — `out/provenance-ledger.jsonl`, one append per plate.** `[CORRECTED — provenance R2 minor]` Describe it as what it is, in the artefact itself, with a first line: `{"schema":"panolib.ledger/1","limits":"An index of plates this tool produced. The owner can rewrite this file, so it carries no trustworthy date until a copy is anchored outside the owner's control."}` `[CORRECTED — provenance R1 minor]` And name the trusted component when recommending an anchor: a git commit's author date is set by the committer and a signature happily covers it, so what is outside the user's control is the *host's record of receipt*. Recommend an RFC 3161 timestamp or a transparency log; note that this project is not a git repo, so `git init` would be a new dependency.

**QR — no by default.** It is the one element on the plate whose contents a human cannot read, so it is the only element that must be *trusted* rather than checked — precisely inverted from the plate's purpose. It looks like the most technical thing on the print and is the least verifiable, establishes nothing about provenance, and ties a print meant to outlast a platform to a URL shape that will not. `--qr geo` emits `geo:51.47780,-0.00147;u=5` `[CORRECTED — provenance R1 minor, provenance R2 minor, both]`: RFC 5870 uses semicolon parameters (`crs`, `u`) and has no query component, so `?z=17` is an Android extension; and iOS registers no handler for `geo:` at all, so "opens in whatever map app the viewer has" is false for roughly half of viewers. `u=5` carries the uncertainty in metres, which is the one honest extra the format offers. **Hard constraint: every value inside a QR must also appear as human-readable text on the same plate**, so the code can be cross-checked and never has to be trusted — which is also what makes the iOS gap harmless.

**Signing — opt-in, off by default.** A detached signature binds the digest list to a key, so a reader holding the public key can tell the sidecar was not altered after signing. It says nothing about whether the EXIF is true, whether the image was edited before signing, or **when** it was signed. A self-signed key with no published home adds close to nothing over the bare digest. Precondition, stated in one line: *publish the public key somewhere stable and keep the private key, or do not bother.* Nothing about signing appears on the printed face — a print cannot carry a verifiable signature, and hinting at one would be the exact overclaim this spec prevents.

`[CORRECTED — provenance R1 minor, provenance R2 minor, both]` The commands as written run in neither shell this Windows-only project uses: `%USERPROFILE%` does not expand in PowerShell, `\` is not a continuation in cmd or PowerShell, and PowerShell 5.1 rejects `<` outright ("The '<' operator is reserved for future use"). Since the verify command goes verbatim into the sidecar as a reader's instructions, an unrunnable one is worse than none. Store single-line, shell-labelled commands:

```
sign:   ssh-keygen -Y sign -f $env:USERPROFILE\.ssh\id_ed25519 -n panolib-provenance DJI_0163-provenance.json
verify: cmd /c "ssh-keygen -Y verify -f allowed_signers -I me@example.com -n panolib-provenance -s DJI_0163-provenance.json.sig < DJI_0163-provenance.json"
```

`[RULING]` R1 routes verify through `cmd /c`; R2 proposes `Get-Content | ssh-keygen`. **R1.** PowerShell appends a newline when piping to a native command, which changes the signed bytes and breaks verification — R2's own fix is unsafe. Also store `signer_id` and `allowed_signers_line`, or the reader cannot build `allowed_signers`.

`--hash` prints `pixels sha256 9f2c1a…` in the survey footnote. Stated exactly: **what it does** — ties this print to one exact pixel array, so a print and a file can be checked against each other. **What it does not do** — says nothing about whether the metadata is true, nothing about where or when the picture was made, and nothing about when the hash was computed: it was computed by this tool, from this file, today, not by the camera at capture.

### 6.9 Privacy coarsening

`--round-position 100m` must be **disclosed and must coarsen the printed format**. `[CORRECTED — typography R3 minor]` Adding the disclosure while still printing 0.1″ steps leaves the plate advertising 3 m precision under a footnote saying 100 m, and the reader has to guess which to believe. Tie format to rounding: at 100 m print decimal degrees at 3 dp (111 m) and **suppress DMS entirely** (no DMS precision lands near 100 m: 1″ = 30.9 m, 5″ = 154.6 m); at 10 m print DD at 4 dp and DMS at whole arcseconds — which is the path that needs the `sec_dp=0` width fix from §3.2.

---

## 7. The user-facing explanation

### 7.1 README section — "What the plate does and does not prove"

> The plate prints what the image file records: the position, height, camera direction and clock time the camera wrote into the file when the picture was taken. It is a transcription, not a verification. Metadata of this kind can be edited with free tools, so a printed coordinate is not evidence on its own, and nothing here is certified, authenticated or verified — you will not find those words on a plate this tool produces.
>
> What the plate does do is make the claim public and checkable. Buried in EXIF, "51.47780 N, 0.00147 W, 19 m above the take-off point, facing north-east, 26 July 2026" is a claim nobody reads. Printed under the picture it is one anyone can test: against a map, against the skyline in the frame, against where the shadows fall at that hour and that latitude. That raises the cost of a false claim from editing one field to making an entire photograph agree with it.
>
> Some limits are worth knowing before you print. The coordinates come from the aircraft's own satellite receiver and are good to a few metres, so a pin that lands 4 m off the obvious subject is the receiver, not the plate. Both heights are barometric; the first is measured from wherever the aircraft took off, which is not the same as the ground beneath it, and the file does not record which vertical reference the sea-level figure uses. The clock is the camera's own and carries no time zone, so the time is printed exactly as recorded and attributed to the camera rather than converted. The camera direction is the aircraft's own compass, uncorrected for magnetic declination, which runs from about 0° in Wisconsin and India to about −25° in South Africa.
>
> If you want more than that, the useful steps are mundane. Keep the original file off the SD card untouched — the plate prints the first sixteen digits of its SHA-256 digest, and the sidecar carries the full digest, so you can show later that a file you hold is the one this print came from. The sidecar also lists every value unrounded, the exact tag each one came from, and the internal cross-checks the tool ran, so the plate's rounding and its choice of fields can be audited rather than taken on trust.

### 7.2 Footnote, `full` (back of print, `--footnote full`)

> Every value on this plate is transcribed from the metadata inside the source file DJI_0163.JPG, written by a DJI Mavic Air 2 (model FC7303). Coordinates are WGS 84 from the camera's satellite receiver, good to a few metres. Heights are barometric: the first is measured from where the aircraft took off, the second from sea level, and the file does not record which vertical reference that second figure uses. Camera direction is the aircraft's own compass, not corrected for magnetic declination. The clock is the camera's own and records no time zone. sha256 c3f1a9e4b70d5a62 — the first sixteen digits of the source file's digest; the full digest is in DJI_0163-provenance.json. Metadata of this kind can be edited with free tools. This plate reports what the file records. It is not a verification, a certificate, or proof that the picture was taken where, when or how the file says.

### 7.3 CLI epilogue (`python -m panolib print --help`)

> The plate transcribes what the file records — position, height, camera direction, clock.
> It is not a verification: this metadata is editable, and the plate never claims otherwise.
> Its value is that the claim becomes visible and checkable against a map and against the frame.

### 7.4 Terminal output after a successful plate

```
  ok    DJI_0163-a4-gallery.tif   3508x2480px   368 dpi native, 300 dpi written   12.3 MB
        51.47780 N  0.00147 W  (WGS 84, +/- a few metres)
        19 m above take-off point  |  207 m above sea level (barometric)
        camera facing 048 deg NE   (aircraft compass, declination not applied)
        2026-07-26 12:10:03  camera clock, zone not recorded
        sha256 c3f1a9e4b70d5a62...
  checks  agree         GPSAltitude 206.5 vs AbsoluteAltitude 206.58, differ by 0.08 m (tol 0.15)
          agree         camera XMP block present
          agree         4000x2250 is a known size for FC7303
          unknown       take-off elevation derives to 187.2 m; plausible only if the recorded
                        sea-level figure is orthometric -- the file does not say which
          not_read      no satellite clock tags read, so the time zone is undetermined
          note          gimbal yaw pinned at 0.00 as expected for a Type=N frame on this model;
                        camera heading taken from the airframe field
  sidecar out/prints/DJI_0163-a4-gallery.json
  ledger  appended to out/provenance-ledger.jsonl

  this source fills 13.3 x 7.5 inches at 300 dpi without enlargement
  prints in out/prints
```

---

## 8. Module layout and exact signatures

Six modules. Five exist and are extended; one is new.

### 8.1 `panolib/capture.py` — the facts

```python
PHOTO_TAGS = TAGS + (
    "FocalLengthIn35mmFormat", "ExposureCompensation", "ShutterSpeed",
    "GPSDateStamp", "GPSTimeStamp", "GPSDateTime", "OffsetTimeOriginal",   # ADDED
    "GPSHPositioningError", "GPSDOP",                                      # ADDED (§3.6 of provenance)
    "Orientation", "ImageDescription",
)

@dataclass
class Capture:                      # EXISTING, extended
    path: str; width: int; height: int
    lat: float | None = None;        lon: float | None = None
    alt_msl: float | None = None;    alt_takeoff: float | None = None   # RENAMED from alt_agl
    captured: str | None = None
    camera_heading: float | None = None
    aircraft_heading: float | None = None
    heading_source: str | None = None            # "gimbal" | "airframe"
    gimbal_pitch: float | None = None            # ADDED: as_opt_float, never as_float
    utc_offset_s: int | None = None              # ADDED
    offset_source: str | None = None             # ADDED
    make: str | None = None; model: str | None = None; serial: str | None = None
    f_number: float | None = None; exposure_time: float | None = None
    iso: float | None = None; focal_35: float | None = None
    orientation: int = 1                         # ADDED
    is_panorama: bool = False
    tile_count: int | None = None                # ADDED
    captured_span: tuple[str, str] | None = None # ADDED
    zenith_filled: bool = False                  # ADDED
    aggregation: str = "single"                  # "single" | "first frame" | "median of N"
    title: str | None = None                     # the user's words; never from the file

    @property
    def alt_agl(self) -> float | None: ...       # deprecated alias, one release
    @property
    def has_position(self) -> bool:              # CHANGED: 0,0 trap
        return (self.lat is not None and self.lon is not None
                and not (abs(self.lat) < 0.001 and abs(self.lon) < 0.001))

def read_capture(path: str, exiftool: str | None = None) -> Capture: ...   # + exif_transpose, pitch, offset
def enrich_from_library(cap: Capture, out_dir: str) -> Capture: ...        # + tile_count, span, zenith_filled

# formatting -- all return Segment lists, never strings containing spaces
Segment = tuple[str, float]
def dms_segments(deg, is_lat, *, sec_dp=1, pad_deg=False) -> list[Segment]: ...
def dd_segments(deg, *, dp=5, signed=False, is_lat=True) -> list[Segment]: ...
def position_segments(cap, *, style="dms") -> list[Segment]: ...
def altitude_segments(cap, *, units="m") -> list[Segment] | None: ...
def heading_segments(cap) -> list[Segment] | None: ...          # applies the pitch gate
def clock_segments(cap) -> list[Segment] | None: ...
def exposure_segments(cap) -> list[Segment] | None: ...
def camera_segments(cap) -> list[Segment] | None: ...            # "DJI FC7303", never a trade name
def compass_point_16(deg: float) -> str: ...
def map_url(lat: float, lon: float) -> str: ...
```

### 8.2 `panolib/paper.py` — sheet, fit, resolution

```python
DPI_LADDER = (300, 240, 200, 180)
MIN_DPI = 160 ; WARN_DPI = 240 ; GOOD_DPI = 300.0 ; MAX_UPSCALE = 1.6
CAPTURED_LAT_MAX_DEG = 41.2

def px(inches: float, dpi: int) -> int: ...           # floor(x*dpi + 0.5)
def rpx(pixels: float) -> int: ...                    # floor(n + 0.5)

@dataclass(frozen=True)
class Paper: ...                                      # EXISTING, unchanged
def parse_size(spec, dpi=300, orientation="auto", source_aspect=None) -> Paper: ...  # + \d{1,4}, \s*, bounds

@dataclass(frozen=True)
class PrintLayout:                                    # NEW in paper.py (not a new module)
    paper: Paper
    canvas_px: tuple[int, int]
    trim_px:   tuple[int, int, int, int]
    image_box_px:   tuple[int, int, int, int]
    caption_box_px: tuple[int, int, int, int]
    margin_in: float; gap_in: float; band_in: float
    bleed_in: float = 0.0; safe_in: float = 0.0

def layout_sheet(paper: Paper, *, band_px: int, bleed_mm=0.0, safe_mm=0.0) -> PrintLayout: ...
def derive_sheet(source_px, *, long_edge_in, dpi, band_measure, max_passes=6) -> Paper: ...  # §5.2 fixed point

def fit_box(source_w, source_h, box_w, box_h, mode="contain") -> tuple[int,int,tuple[int,int,int,int]]: ...
def choose_fit(source_px, box_in) -> Literal["contain","cover","fit-width"]: ...   # §2.4, r_img >= 1.9
def native_dpi(source_px, box_in, fit) -> float: ...                              # §2.3, axis-wise
def choose_file_dpi(native: float, *, forced=None, allow_soft=False) -> tuple[int|float, str]: ...
def assess_fit(source_w, source_h, draw_w, draw_h, crop, paper) -> FitReport: ...
def aspect_guard(source_px, box_in) -> str | None: ...                            # §2.4 warning
def choose_size(source_px, *, orient="auto", dpi=300, band_measure, candidates=None) -> Paper: ...

class ResolutionTooLow(ValueError):
    native_dpi: float ; max_honest_in: tuple[float, float] ; suggestions: tuple[str, ...]
```

### 8.3 `panolib/typeset.py` — type mechanics

```python
FONT_STACKS: dict[str, tuple[str, ...]]              # REORDERED: pala first in serif, serif_tab
NOTDEF_PROBE = "\U000E0100"
MARKS = set("0123456789") | {"\u00b0", "\u2032", "\u2033", "."}

def pt_to_px(points: float, dpi: int) -> int: ...
def load_font(role: str, px: int) -> FreeTypeFont: ...           # + whitelist check
def missing_glyph(font, ch: str) -> bool: ...                    # REPLACES has_glyphs
def assert_role_glyphs(role: str, px: int) -> None: ...
def cap_px(font) -> int: ...                                     # getbbox("H", anchor="ls")
def ink_bearings(font_path: str, size: int, ch: str) -> tuple[int, int]: ...   # cached
def segments_width(segments: list[Segment], font, *, track_em=0.0) -> float: ...
def draw_tracked(draw, xy, segments, font, fill, *, track_em=0.0,
                 align="l", optical=True) -> float: ...          # float accumulator, MARKS hug
def wrap_segments(segments, font, *, track_em, measure_px) -> list[list[Segment]]: ...   # NEW
def draw_small_caps(draw, xy, text, font_full, font_small, fill, tracking=0.0, anchor="ls") -> float: ...

@dataclass
class TypeScale: ...                                  # §5.4, with pt floors
```

### 8.4 `panolib/plate.py` — composition and wording

```python
@dataclass
class PlateStyle: ...                                 # EXISTING, + polarity/keyline/rule fields
STYLES: dict[str, PlateStyle]                         # gallery (default), survey, overlay, field-note
FOOTNOTE_CLAUSES: dict[str, str]                      # §4.3, CLOSER is final and non-removable

def caption_point_size(base_pt: float, paper: Paper) -> float: ...     # CHANGED: diagonal
def build_rows(cap, *, units="m", include=None) -> list[PlateRow]: ... # CHANGED: take-off first
def compose_footnote(cap, *, length="standard", printed: set[str],
                     round_position: str | None = None) -> list[Segment]: ...
def measure_band(cap, *, box_w_px: int, dpi: int, style, title=None,
                 footnote="standard", include=None) -> int: ...        # the §2.1 contract
def pick_polarity(band: np.ndarray, *, weakest_ink_Y: float,
                  target: float = 7.0) -> tuple[str, float]: ...       # §5.8
def render(cap, source, paper, *, style="gallery", title=None, fit="auto",
           units="m", include=None, footnote="standard",
           composition=None) -> tuple[Image.Image, FitReport]: ...
def save_print(image, path, paper, *, file_dpi=None, jpeg_quality=95,
               icc: bytes | None = None, source_exif=None) -> None: ...  # + allowlist, Orientation=1
def blocking_absence(cap) -> str | None: ...           # non-None -> refuse (§6.3)
```

### 8.5 `panolib/provenance.py` — NEW, phase 3

```python
PLATE_SCHEMA = "panolib.provenance/1"
CheckResult = Literal["agree","disagree","unknown","not_available","not_read","note"]

@dataclass(frozen=True)
class Check: check: str; result: CheckResult; detail: str

def source_digest(path: str, *, algo="sha256") -> str: ...
def pixel_digest(image: Image.Image) -> str: ...
def set_digest(files: Sequence[tuple[str, str]]) -> str: ...   # sha256 over "<name> <sha256>\n", sorted
def device_fingerprint(serial: str) -> str: ...                # sha256("panolib-device:"+serial)[:6]
def consistency_checks(cap: Capture) -> list[Check]: ...
def solar_candidates(lat, lon, naive_dt) -> list[dict]: ...     # NOAA, accuracy_deg 0.1
def write_sidecar(cap, out_path, print_info, *, fmt="both",
                  redact_serial=True, sign_key=None) -> list[str]: ...
def append_ledger(cap, digest, ledger_path) -> None: ...
def geo_uri(lat, lon, *, uncertainty_m: int = 5) -> str: ...     # RFC 5870, ;u= not ?z=
```

### 8.6 CLI (`panolib/_print_cmd.py`, `panolib/__main__.py`)

Existing flags kept verbatim where possible. **Added:** `--composition mat|bar|overlay`, `--coords dms|decimal`, `--footnote short|standard|full`, `--serif palatino|cambria`, `--mat warm|paper|dark`, `--dpi auto|native|300|240|200|180`, `--min-dpi N`, `--allow-soft`, `--fit auto|contain|cover|fit-width`, `--anchor`, `--offset X,Y`, `--crop-to-captured`, `--sharpen auto|none|<pct>`, `--linear-resample`, `--print-lift G`, `--soft-proof ICC`, `--bleed MM`, `--safe MM`, `--round-position`, `--serial`, `--hash`, `--sign KEY`, `--qr none|geo`, `--strict`, `--require-position`, `--no-provenance`, `--allow-huge`, `--proof/--no-proof`.

**Changed:** `--size` default becomes `auto` (was `a3`); `--fit` default becomes `auto` (was `contain`); `--sidecar` defaults **on**; `--no-footnote` errors unless `--no-provenance`; `--allow-no-position` is retained but now only needed when `--require-position` is set, since missing position degrades by default.

Exit codes: `0` ok · `1` target not found · `2` bad arguments · `3` refused on resolution.

**Filename:** `<stem>-<size><P|L>-<dpi>dpi-<style>[-<fit>][-soft][-lift<G>].<ext>`. The `-soft` and `-lift` tokens are load-bearing: a file compromised to make a size work announces it in its own name.

**Proof:** a 1600 px long-edge JPEG at quality 88, downsampled from the *finished canvas* so it cannot disagree with the master. `[CORRECTED — printmech R1 minor]` The ICC soft proof takes a distinct token, `-softproof-<profile-stem>`, or the two collide and one silently overwrites the other; `PrintResult` gains `soft_proof_path`.

### 8.7 Sidecar keys

`[CORRECTED — printmech R1 minor]` Emit both `image_box_px` (the reserved box) and `placed_px` (where the image landed) — the shipped example labels the placed rectangle as the box, so a consumer reconstructing the layout gets the wrong rectangle and cannot tell how much letterbox there was. Add `trim_px`, `achieved_bleed_in`, and state `crop_px`'s convention (PIL's `crop` takes left/upper/right/lower, not x/y/w/h).

`[CORRECTED — provenance R1 minor]` `absent_reason` describes **the file only**. Anything the tool did not look for is `"not_read"`; anything the spec has not settled belongs in prose, never in output. The spec's `"absent_reason": "not captured in the brief's verified field list"` is a statement about its own author that a reader would take as a statement about the camera.

`[CORRECTED — printmech R1 and R2 both minor]` Bleed: derive the canvas from the trim, `canvas = (trim_w + 2*px(b), trim_h + 2*px(b))` = 4870 × 7270 for a 16x24 at 3 mm, so the trim box is exactly centred; the spec's independently-derived 4871 × 7271 leaves 36 px on two sides against 35 on the others. Record the achieved bleed, which is rounded down to a whole pixel.

---

## 9. Phased plan

### Phase 1 — the smallest thing that produces a good print

Everything here is a correction to code that already runs, which is why phase 1 is small. Target: `python -m panolib print DJI_0163.JPG` with no options produces a handsome A4 at 300 dpi.

1. `capture.py`: `has_position` 0,0 trap; `exif_transpose`; `gimbal_pitch` via `as_opt_float`; `alt_takeoff` rename with alias; segment-returning formatters with the `sec_dp` and `pad_deg` width fixes; keep the seconds in `format_datetime`.
2. `typeset.py`: reorder `FONT_STACKS` (`pala.ttf` first in `serif` and `serif_tab` — removes two latent tofu paths); `missing_glyph` + `assert_role_glyphs`; `cap_px`; `ink_bearings`; `draw_tracked` with float accumulator, `MARKS` hug, optical lsb/rsb; `wrap_segments`; `TypeScale` pt floors; footnote factor 0.68.
3. `paper.py`: `px`/`rpx`; `native_dpi` axis-wise; `choose_file_dpi` with the ladder and the `native` rung; `choose_fit` at 1.9; `parse_size` regex fixes; `choose_size` on box ratio, window 0.06, aliases excluded.
4. `plate.py`: diagonal type scale; take-off altitude first with full phrases; `#5A544A`/`#4A443C` inks; rule and keyline at `max(2, rpx(0.5*dpi/72))`; `measure_band` + `band = max(measured, px(c))`; reserved 1:1.35 weighting; `ceil` descender allowance; composed footnote with the mandatory `CLOSER`; `--no-footnote` gated.
5. `_print_cmd.py`: `--size auto` and `--fit auto` defaults; sidecar on by default with the serial redacted; degrade-not-refuse on missing position; the §7.4 terminal block.

**Phase 1 ships without:** survey table layout, bar composition, overlay polarity, digests, checks, solar, ledger, signing, soft proof, bleed, QR, field-note style.

### Phase 2 — the survey plate and the overlay that works

Survey style as a real table (Consolas value column, right-aligned Segoe UI Semibold labels, reserved sign and degree cells, group separators only, drawn north arrow rotated by bearing since `ImageDraw` has no rotation and U+2191 is .notdef in Palatino); `bar` composition; automatic overlay polarity replacing the always-black scrim; intrinsic overlay type scale; `--serif cambria`; `--mat` variants.

### Phase 3 — `panolib/provenance.py`

Digests on the face and in the sidecar; consistency checks with the corrected tolerances to terminal and sidecar; the `.txt` sidecar; the ledger with its stated limits; `--sign`; solar candidates; the allowlist EXIF copy with GPano stripped and `Orientation=1`; the JPEG EXIF dpi fix.

### Phase 4 — print-shop finish

Adobe Deflate + Predictor 2 TIFF; ICC embedding with the 3144-byte Windows profile; `--soft-proof` via `buildProofTransform`; `--print-lift`; `--bleed`/`--safe`; PDF with its limits documented; the on-screen proof; `--qr geo`; field-note style; effective-ppi warning naming the re-stitch width (`GROUND_TRUTH` gives native 1519.5 px/radian = 9547 px for a full 360, so an 8192 px equirect printed 44 in wide at 186 ppi has a real remedy: re-stitch at `--width 12288`).

### Phase 5 — set-derived plates

`read_provenance_set(meta_json_path)` for median-and-spread aggregation, capture intervals, and the composite disclosure with real numbers rather than the first frame's.

---

## 10. Open questions — genuinely unresolved

**10.1 The vertical datum of the sea-level figure.** Neither `AbsoluteAltitude` nor `GPSAltitude` records whether the figure is orthometric (above the geoid, i.e. sea level) or ellipsoidal (above the WGS 84 ellipsoid, which is what a GNSS receiver natively produces). At Chicago those differ by roughly 34 m — an order of magnitude larger than every rounding decision in §3 and larger than the 5 m grid `--alt-asl coarse` offers as the honesty option. The EXIF label "Above Sea Level" is `GPSAltitudeRef=0` being rendered by exiftool, not evidence a geoid correction was applied. **This also makes the launch-elevation cross-check undecidable:** `206.58 − 19.40 = 187.18 m` is plausible for the Chicago lakefront above Lake Michigan's 176 m *if* the figure is orthometric; if it is ellipsoidal the implied elevation is about 221 m and the check fails by ~39 m. The provenance spec declared 187 m "plausible" using an argument that assumes its own conclusion. Until settled: the footnote says the file does not record the reference, the check reports `unknown` with both readings, and the take-off figure is unaffected because it is a difference and the datum cancels.

**10.2 Whether this archive carries a satellite clock.** `GPSDateStamp` and `GPSTimeStamp` are already in `PHOTO_TAGS` but never used, and nobody has looked. If present, the time zone stops being an inference and becomes a measurement from an atomic clock the photographer does not set — promoting it from Tier 2 to the strongest consistency check available, and letting the plate honestly print a UTC instant. One command answers it: `exiftool -a -G1 -s -time:all -gps:all -XMP-drone-dji:all <file>`. Both wording paths are designed (§3.5) so the answer needs no redesign. **Note that a named-tag request returning nothing proves nothing** — exiftool exits 0 on tags it does not know.

**10.3 Whether DJI applies magnetic declination before writing yaw.** `docs/BUILD_SPEC.md:1331` lists this as unresolved for the stitch; it is equally unresolved for the plate. CHK-SUN's ephemeris comparison across four countries would settle it empirically. Until then the footnote qualifier (§3.4) is the honest position, and a 25° declination means the printed compass *word* can be wrong in the South African material.

**10.4 Whether reprints should be reproducible across generations.** If 10.2 is answered yes and printed times gain a UTC instant, reprints of existing plates will differ from earlier prints of the same image. The sidecar must pin the decision (`offset_source`) so a reprint is reproducible, or the archive ends up with two inconsistent generations of plate. Not yet designed.

**10.5 No lab's submission requirements were checked.** TIFF/Adobe Deflate/Predictor 2 with an embedded sRGB profile and `ResolutionUnit=2` is standards-correct and round-trips locally, but "accepted by lab X" is unverified; some labs reject Deflate and want LZW. `--format jpeg` covers it, and the default may need changing after the first real order.

**10.6 The 7:1 contrast target is a screen model.** Printed ink on paper has a lower dynamic range and a different black point, so a pair computing at 7.07:1 may measure nearer 5:1 on matte stock. The margin is ample for the primary and secondary inks (16.8:1, 9.1:1) but the footnote sits on the threshold. No soft proof was rendered and compared against a physical print during this work.

**10.7 Rounding means the plate and the file disagree in the last digit.** Someone comparing them could read that as tampering — exactly the suspicion this feature exists to defuse. The sidecar's rounding block mitigates it, but only for a reader who has the sidecar; a printed plate alone carries no more than the footnote's "good to a few metres".

**10.8 Fonts are machine-bound.** Palatino Linotype, Segoe UI and Consolas are not redistributable, so a fresh machine fails at render time rather than install time. The whitelist refuses a face lacking U+2032/U+2033, which is correct behaviour but is the wrong place to discover it. A bundled SIL-licensed serif with lining tabular figures would fix it; none is chosen.

**10.9 raqm's absence is a property of this build, not of Pillow.** If the venv is rebuilt with libraqm present, Pillow switches to complex layout: kerning changes, advances shift, and every measured width here moves. Pin `ImageFont.Layout.BASIC` explicitly and assert `PIL.features.check('raqm') is False` in a test, so the arithmetic stays valid or fails loudly.

**10.10 Non-sphere panoramas cannot honestly print.** Measured from `library.json`: the sweep is 3851×827 (897 px/rad), the wide grid ~1111×761 (~407), the vertical strip 474×420, a single frame 415×327 — against the native 1519.5 px/rad. Honest maxima at 180 dpi are 21 in, 6 in, 2.6 in and 2.3 in. The print command will refuse the last two, correctly, but the loss is **build-side**: `classify.recommended_canvas` scales the whole 360° canvas width by span/360, lowering angular resolution everywhere instead of narrowing the canvas — the opposite of what its docstring says. Fixing that is out of scope here and belongs in a separate change.

---

## 11. Tests worth pinning (§9.4 equivalent)

1. Every rendered string passes the R4 banned-vocabulary lint, with the negation carve-out.
2. No formatter output contains any space character or any `.notdef` glyph in its assigned font.
3. `dms_segments(51.477800, True)` → `51°28′40.1″` + `N`; carries `59.96″ → 60 → +1′` and `59′59.96″ → +1°`; `sec_dp=0` → `14″` not `014″`; `len(lat) == len(lon)` under `pad_deg=True`.
4. `heading(-0.3) == "000° NE"`-form check: `000`, not `360`; `heading(47.60) == "048° NE"`.
5. `heading_source == "airframe"` for a `Type=N` frame whose `GimbalYawDegree` is 0.00.
6. `has_position` is False for `(0.0, 0.0)` and True for `(0.0, 12.3)`.
7. Every ink/ground pair in every style measures ≥ 7:1, computed.
8. Every role's printed value cap height ≥ 1.69 mm at 5x7; no rule or keyline below 2 px at any dpi.
9. `native_dpi` for a 2250×4000 portrait source on 16x20 portrait returns 312.5, not 555.6.
10. A 4000×2250 source with `Orientation=6` produces a portrait sheet whose image is 2250 wide, and the output carries `Orientation=1`.
11. `segments_width(track_em=0)` equals `sum(getlength(c))` exactly, and is within 1.0 px of `getlength(whole)` — never the reverse.
12. The realised track is within 0.002 em of the specified `track_em` at every value size in the table.
13. `band_px == max(measure_band(...), px(c))`, and the rendered block height equals the computed `band_px` with and without a title.
14. The mat is weighted 1:1.35 top:bottom on a derived sheet and on 11x14 through 24x36.
15. `pick_polarity` on a synthetic Y = 0.85 band returns `("light", 0.0)`; on Y = 0.02 returns `("dark", 0.0)`; on Y = 0.12 returns α ≈ 0.30 either way.
16. A plate with `alt_takeoff` missing never emits "Altitude" alone, and never emits "AGL" or "above ground" under any input.
17. A plate with no position prints `No position recorded in this file.` and a footnote containing neither "Position" nor a precision claim.
18. A plated equirect carries no `ProjectionType` and no `SerialNumber`.
19. `PIL.features.check('raqm') is False`, and the layout engine is pinned to `BASIC`.
20. Every data-block string is reconstructible from exiftool-read values — which is what would have caught "Mavic Air 2".