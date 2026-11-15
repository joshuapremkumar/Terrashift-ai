# TerraShift AI — prototype walkthrough

**Format:** 6–7 minutes live, solo presenter. Target: NASA Space Apps judges.
**Open before you start:** <http://127.0.0.1:8000>, one browser tab, font size up, no dev tools.

---

## 0. The one-sentence pitch (15 s)

> TerraShift AI turns NASA Earth observation into a ranked, explainable crop-rotation plan
> for a specific farm — and it refuses to invent a single number it cannot trace.

---

## 1. The map — “here is a real farm, not a mock-up” (45 s)

1. The landing tab shows the four sample farms.
2. Click **Karnataka rain-fed smallholding**.
3. Point at the map: farm pin, NASA GIBS imagery layer, observed climate KPIs.

Say:

> This is a real coordinate in Karnataka. The rainfall and temperature history comes live
> from NASA POWER. The imagery is NASA GIBS. No farm data is pre-baked; the card you see
> is the engine running now.

If the network is slow, the banner states which provider answered or that a flagged
fallback was used. Never hide it.

---

## 2. Climate stress — “evidence, not prediction” (60 s)

1. Open **02 · Climate stress**.
2. Read the 90-day anomaly and the stress score.
3. Point at the vegetation card: it says **proxy — not NDVI**.

Say:

> The last 90 days are compared with the same calendar window over nine complete years.
> This is observed data, and it says the region is running about a third below its own
> historical rainfall for this window.
>
> We could have shown an NDVI map and called it vegetation health. We did not, because
> NDVI statistics need NASA Earthdata credentials we have not configured. The card says so
> and shows a clearly labelled proxy instead.

This is the trust moment. Do not rush it.

---

## 3. Rotation simulator — “the engine decides” (90 s)

1. Open **04 · Rotation simulator**.
2. Point at the winner, then at the score breakdown.
3. Scroll to **How the score is calculated**.
4. Point at a penalised strategy (for example continuous maize).

Say:

> Five criteria — water, climate, soil, pest break and farmer priority — are weighted,
> then agronomic guards apply a reported penalty. Here the winner is a legume break:
> maize, pigeon pea, maize. Continuous maize is still listed, but the guard knocks it down
> and we show the penalty separately. The arithmetic is on screen.
>
> For Machakos, the same engine refuses to recommend a maize-heavy rotation under a severe
> water gap. It is not optimising for a nice-looking answer; it is enforcing feasibility.

Optional: switch priority to **Water conservation** and re-run to show the ranking move.

---

## 4. What-if planner — “decisions, not charts” (45 s)

1. Open **05 · What-if planner**.
2. Set rainfall to **−20 %**, irrigation to **full**, priority to **water**.
3. Click **Run scenario**.

Say:

> Only the declared inputs change. The scoring rules do not. You can see which sequences
> gain and which lose, and the constraint messages update with them.

---

## 5. Adaptation report — “grounded language” (60 s)

1. Open **06 · Adaptation report**, click **Build report**.
2. Read two sentences of the narrative.
3. Point at the provider badge: **deterministic template** (or Vultr when configured).
4. Open **Printable report**.

Say:

> The narrative is generated from the computed payload. When Vultr inference is enabled,
> the model's text is accepted only if every number in it appears in the engine output;
> otherwise it is rejected and the deterministic narrative is served. The model explains,
> it never decides.
>
> This is the downloadable artefact a extension officer could print and take to the farm.

---

## 6. Close — “why this is different” (45 s)

Three differentiators, one line each:

1. **Climate Memory.** We do not score against a generic climate average; we compare each
   crop's own growing window with multi-year monthly climatology for this location.
2. **Farmer Priority Mode.** Water, soil, profit or resilience changes the weights — and
   the recommendation changes with them, transparently.
3. **Explainable by construction.** Every score has criteria, weights, penalties and an
   evidence type. Missing data is shown as missing, not smoothed away.

Final line:

> TerraShift AI is a working prototype built today: live NASA data, a deterministic
> agronomic engine, honest uncertainty, and a report a farmer can actually use. The next
> step is local calibration with extension services — not more model confidence.

---

## Judge Q&A — short answers

**“Is this just an LLM wrapper?”**
No. The LLM is optional and downstream. Remove the key and every score, ranking and
penalty is unchanged; only the prose falls back to a template. The engine is NumPy/Pandas.

**“Where does the crop data come from?”**
A 36-entry indicative reference table compiled from published ranges. It is explicitly
not calibrated to local varieties, and the UI says so.

**“How do you prevent hallucinated yields?”**
We never produce yields. We rank rotations and report indicative water balance and
nitrogen-fixation credits from the reference table. Model output containing any number not
in the computed payload is rejected automatically.

**“What happens when NASA POWER is down?”**
A synthetic fallback is used only if the live call fails, and the response is flagged
`synthetic: true` with a warning in the UI and report. It is never presented as observed.

**“Why Vultr?”**
Vultr hosts the deterministic engine and API, stores farm profiles in PostgreSQL, and
provides Serverless Inference for grounded narration. It is the compute and deployment
backbone, not a logo on a slide.

**“What is not modelled?”**
Stored soil moisture, groundwater, runoff, irrigation efficiency, pests and disease
pressure dynamics, prices, labour and machinery. These are listed in the report's
limitations.

---

## Fallback plan

- If NASA POWER is slow: the cache usually answers instantly after the first run. Warm the
  four sample farms before presenting.
- If the network is unavailable: the flagged synthetic fallback keeps the walkthrough running; say
  clearly that it is synthetic.
- If the browser fails: the print-ready report at `/api/farms/{id}/report` is a complete
  fallback artefact.
- Keep a second browser tab on `/health` to show provider status honestly.
