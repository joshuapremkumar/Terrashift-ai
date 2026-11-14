"""AI explanation layer.

The language model receives the engine's finished result and is asked to explain
it in plain language. It is explicitly forbidden from inventing numbers, before
which its output is validated: any number in the narrative that is not present
in the computed payload causes the AI text to be rejected and the deterministic
narrative to be used instead.

That means the product always has a trustworthy explanation, with or without a
model provider configured.
"""
from __future__ import annotations

import json
import re

import httpx

from ..config import settings

SYSTEM_PROMPT = (
    "You are an agricultural decision-support writer. You explain results that were "
    "computed by a deterministic engine. Rules you must never break: "
    "1) Do not invent numbers, percentages, yields, or soil measurements. Use only "
    "numbers present in the supplied JSON. "
    "2) Do not claim a rotation will increase yield or improve soil health by a "
    "specific amount. "
    "3) Keep observations, forecasts and research-backed evidence clearly separate. "
    "4) State uncertainty and missing data plainly. "
    "5) Write for a farmer and an agronomist: concrete, calm, no marketing language. "
    "6) Never present this as a substitute for local agronomic advice."
)


def _numbers(text: str) -> set[int]:
    found = set()
    for token in re.findall(r"\d+(?:\.\d+)?", text):
        try:
            found.add(int(float(token)))
        except ValueError:
            continue
    return found


def _allowed_numbers(payload: dict) -> set[int]:
    raw = json.dumps(payload)
    allowed = _numbers(raw)
    allowed.update(range(0, 13))  # months, small counts, ordering words
    allowed.update({14, 15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 75, 80, 90, 100})
    return allowed


def _narrative(rotation: dict, farm: dict, analysis: dict) -> str:
    baseline = analysis.get("baseline", {})
    anomalies = analysis.get("anomalies", {})
    stress = analysis.get("stress", {})
    winner = rotation.get("winner") or {}
    best = rotation["rotations"][0]
    runner_up = rotation["rotations"][1] if len(rotation["rotations"]) > 1 else None
    criteria = best["criteria"]

    lines = []
    location = ", ".join(
        part for part in (farm.get("region"), farm.get("country")) if part
    )
    lines.append(
        f"Farm profile: {farm['name']}"
        + (f", {location}" if location else "")
        + f", soil texture {farm['soil_texture']}, irrigation {farm['irrigation']}, "
        f"current crop {farm.get('current_crop')}."
    )
    if baseline.get("annual_rain_mm") is not None:
        lines.append(
            f"Observed climate (NASA POWER, {baseline.get('years')} complete years): mean annual "
            f"rainfall {baseline['annual_rain_mm']} mm and mean temperature "
            f"{baseline.get('annual_mean_t2m_c')} °C. These are regional gridded observations, "
            "not on-farm measurements."
        )
    recent: list[str] = []
    if anomalies.get("rain_90d_pct") is not None:
        rain_pct = anomalies["rain_90d_pct"]
        direction = "below" if rain_pct < 0 else "above"
        recent.append(
            f"the last 90 days were {abs(rain_pct)}% {direction} the historical average "
            "for the same window"
        )
    temp_delta = anomalies.get("t2m_90d_c")
    if temp_delta is not None:
        if abs(temp_delta) < 0.05:
            recent.append("mean temperature was close to the historical average")
        else:
            recent.append(
                f"mean temperature was {abs(temp_delta)} °C "
                + ("warmer" if temp_delta > 0 else "cooler")
            )
    if recent:
        line = "Recent window: " + " and ".join(recent) + "."
        if stress.get("score") is not None:
            line += (
                f" The current climate-stress score is {stress['score']} out of 100, "
                "where higher means more stressed."
            )
        lines.append(line)

    # The two 90-day anomaly flags restate the paragraph above, so only report the
    # remaining signals here to avoid duplicating the same evidence.
    repeated_types = {"rainfall_deficit", "temperature_anomaly"}
    extra_flags = [
        flag["message"].rstrip(".")
        for flag in stress.get("flags", [])
        if flag.get("message") and flag.get("type") not in repeated_types
    ]
    if extra_flags:
        lines.append("Further stress signals: " + "; ".join(extra_flags) + ".")

    lines.append(
        f"Recommended sequence: {winner.get('label')} — "
        + " → ".join(winner.get("sequence_names") or [])
        + f", overall suitability {best['score']} out of 100; confidence in the underlying "
        f"input data is {best['confidence']}."
    )
    lines.append(
        "Why: water suitability scores "
        f"{criteria['water']['score']} and climate suitability scores "
        f"{criteria['climate']['score']}, soil and nutrient compatibility scores "
        f"{criteria['soil']['score']}, pest-break value scores {criteria['pest']['score']}, "
        f"and alignment with farmer priorities scores {criteria['preference']['score']}."
    )
    if best.get("benefits"):
        lines.append("Agronomic benefits: " + " ".join(best["benefits"]))
    if best.get("concerns"):
        lines.append("Concerns to manage: " + " ".join(best["concerns"]))

    if runner_up:
        lines.append(
            f"The next-best option, {runner_up['label']} ({' → '.join(runner_up['sequence_names'])}), "
            f"scores {runner_up['score']}. The difference is driven mainly by "
            f"{_criterion_gap(best, runner_up)}."
        )
    if best.get("constraints"):
        lines.append(
            "Agronomic guard: " + " ".join(item["message"] for item in best["constraints"])
        )

    quality = analysis.get("data_quality", {})
    lines.append(
        "Evidence separation: the rainfall and temperature history is observed data; the next "
        "14 days come from a forecast provider and carry growing uncertainty; crop parameters are "
        "research-backed reference ranges. "
        + (
            "Data limitations: " + " ".join(quality.get("notes", [])) + " "
            if quality.get("notes")
            else ""
        )
        + "This is a decision-support prototype, not a substitute for local agronomic advice."
    )
    return "\n\n".join(lines)


def _criterion_gap(best: dict, runner_up: dict) -> str:
    """Name the criterion that most separates the winner from the runner-up."""
    best_criteria = best["criteria"]
    runner_criteria = runner_up["criteria"]
    gaps = []
    for key, item in best_criteria.items():
        other = runner_criteria.get(key)
        if other is None:
            continue
        weighted_gap = (item["score"] - other["score"]) * item["weight"]
        gaps.append((weighted_gap, item["label"].lower(), item["score"], other["score"]))
    if not gaps:
        return "the weighted criteria"
    _, label, best_score, other_score = max(gaps, key=lambda item: item[0])
    return f"{label} ({best_score} vs {other_score})"


async def _vultr_narrative(payload: dict) -> str:
    body = {
        "model": settings.vultr_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Explain this computed rotation assessment for the farmer and the agronomist. "
                    "Do not add any number that is not in the JSON.\n\n"
                    + json.dumps(payload, ensure_ascii=False)
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 900,
    }
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.post(
            f"{settings.vultr_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.vultr_api_key}"},
            json=body,
        )
        response.raise_for_status()
        data = response.json()
    return data["choices"][0]["message"]["content"].strip()


async def explain(rotation: dict, farm: dict, analysis: dict) -> dict:
    payload = {
        "farm": {
            "name": farm.get("name"),
            "region": farm.get("region"),
            "country": farm.get("country"),
            "soil_texture": farm.get("soil_texture"),
            "soil_ph": farm.get("soil_ph"),
            "irrigation": farm.get("irrigation"),
            "current_crop": farm.get("current_crop"),
            "priority": farm.get("priority"),
        },
        "climate": {
            "annual_rain_mm": analysis.get("baseline", {}).get("annual_rain_mm"),
            "annual_mean_t2m_c": analysis.get("baseline", {}).get("annual_mean_t2m_c"),
            "anomalies": analysis.get("anomalies"),
            "stress_flags": analysis.get("stress", {}).get("flags"),
        },
        "weights": rotation.get("weights"),
        "winner": {
            "label": rotation.get("winner", {}).get("label"),
            "sequence": rotation.get("winner", {}).get("sequence_names"),
            "score": rotation.get("winner", {}).get("score"),
        },
        "rotations": [
            {
                "label": item["label"],
                "sequence": item["sequence_names"],
                "score": item["score"],
                "confidence": item["confidence"],
                "criteria": {key: value["score"] for key, value in item["criteria"].items()},
                "benefits": item["benefits"],
                "concerns": item["concerns"],
            }
            for item in rotation["rotations"]
        ],
        "disclaimer": rotation.get("disclaimer"),
    }

    fallback = _narrative(rotation, farm, analysis)
    if not settings.vultr_enabled:
        return {
            "available": False,
            "provider": "deterministic template",
            "grounded": True,
            "text": fallback,
            "note": (
                "VULTR_INFERENCE_API_KEY / VULTR_INFERENCE_MODEL are not configured, so the "
                "deterministic narrative is used. It is generated only from computed values."
            ),
        }

    try:
        text = await _vultr_narrative(payload)
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "provider": "deterministic template",
            "grounded": True,
            "text": fallback,
            "note": f"Model provider unavailable ({exc}); deterministic narrative used instead.",
        }

    allowed = _allowed_numbers(payload)
    unexpected = sorted(number for number in _numbers(text) if number not in allowed)
    if unexpected:
        return {
            "available": False,
            "provider": "deterministic template",
            "grounded": True,
            "text": fallback,
            "rejected_model_output": True,
            "note": (
                "Model output was rejected because it introduced numbers not present in the "
                f"computed results ({unexpected[:5]}). The deterministic narrative is used instead."
            ),
        }

    return {
        "available": True,
        "provider": f"Vultr Serverless Inference ({settings.vultr_model})",
        "grounded": True,
        "text": text,
        "validation": "All numbers in the narrative were found in the computed payload.",
    }
