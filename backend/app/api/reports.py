from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..models.farm import Farm
from ..services import ai_explanations, analysis_service, rotation_engine, tavily
from .deps import get_farm_or_404

router = APIRouter(prefix="/api/farms/{farm_id}", tags=["reports"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def build_report(farm: Farm) -> dict:
    farm_dict = analysis_service.farm_to_dict(farm)
    analysis = await analysis_service.build_analysis(farm_dict)
    context = rotation_engine.build_context(farm_dict, analysis)
    rotations = rotation_engine.evaluate(context)
    explanation = await ai_explanations.explain(rotations, farm_dict, analysis)

    winner = rotations["rotations"][0]
    research = await tavily.search(tavily.rotation_query(winner["sequence_names"], farm.region))

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "farm": farm_dict,
        "analysis": analysis,
        "rotation": rotations,
        "explanation": explanation,
        "research": research,
    }


@router.get("/report")
async def get_report(
    format: str = Query(default="html", pattern="^(html|json)$"),
    farm: Farm = Depends(get_farm_or_404),
):
    """Assemble the adaptation report.

    ``format=json`` returns the full evidence bundle; ``format=html`` returns a
    print-ready document (use the browser's print dialog to save as PDF).
    """
    report = await build_report(farm)

    if format == "json":
        return report

    winner = report["rotation"]["rotations"][0]
    return HTMLResponse(
        templates.get_template("report.html").render(
            report=report,
            farm=report["farm"],
            analysis=report["analysis"],
            rotation=report["rotation"],
            winner=winner,
            explanation=report["explanation"],
            research=report["research"],
            weights=report["rotation"]["weights"],
        )
    )
