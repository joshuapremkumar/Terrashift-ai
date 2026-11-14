"""Tavily agronomy research layer.

Tavily is used to retrieve *research context*, never to produce measurements.
Results are filtered toward authoritative agricultural institutions, cached, and
labelled as research-backed evidence with their source URLs intact.
"""
from __future__ import annotations

from datetime import date

import httpx

from ..config import settings
from . import cache

TRUSTED_DOMAINS = [
    "fao.org",
    "icar.org.in",
    "cgiar.org",
    "iari.res.in",
    "icrisat.org",
    "cimmyt.org",
    "irri.org",
    "usda.gov",
    "ars.usda.gov",
    "extension.",
    ".edu",
    "gov.in",
    "icarda.org",
    "ilri.org",
    "cgiar.org",
    "nature.com",
    "sciencedirect.com",
    "frontiersin.org",
    "mdpi.com",
    "springer.com",
    "wiley.com",
]

SUGGESTED_SOURCES = [
    {
        "name": "FAO Land & Water — crop water information",
        "domain": "fao.org",
        "use": "Seasonal crop water requirements and irrigation guidance.",
    },
    {
        "name": "ICAR (Indian Council of Agricultural Research)",
        "domain": "icar.org.in",
        "use": "Indian crop advisories, package of practices and state recommendations.",
    },
    {
        "name": "CGIAR centres (ICRISAT, CIMMYT, IRRI, IWMI)",
        "domain": "cgiar.org",
        "use": "Dryland cropping systems, rotations and climate-resilient varieties.",
    },
    {
        "name": "USDA / university extension services",
        "domain": "usda.gov",
        "use": "Temperate rotation guidance and soil nutrient management.",
    },
]


def _is_trusted(url: str) -> bool:
    lowered = url.lower()
    return any(domain in lowered for domain in TRUSTED_DOMAINS)


async def search(query: str, max_results: int = 5) -> dict:
    if not settings.tavily_enabled:
        return {
            "available": False,
            "provider": "Tavily",
            "reason": "TAVILY_API_KEY is not configured.",
            "results": [],
            "suggested_sources": SUGGESTED_SOURCES,
            "note": (
                "No research results are fabricated. Configure Tavily to retrieve live, "
                "sourced agronomic literature."
            ),
        }

    cache_key = f"tavily:{query}:{max_results}:{date.today().isoformat()}"
    cached = cache.get(cache_key, ttl_seconds=24 * 60 * 60)
    if cached is not None:
        return cached

    payload = {
        "api_key": settings.tavily_api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_domains": [
            "fao.org",
            "icar.org.in",
            "cgiar.org",
            "icrisat.org",
            "cimmyt.org",
            "irri.org",
            "usda.gov",
            "icarda.org",
        ],
        "include_answer": False,
    }
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.post(settings.tavily_base_url, json=payload)
        response.raise_for_status()
        data = response.json()

    results = []
    for item in data.get("results", []):
        url = item.get("url", "")
        results.append(
            {
                "title": item.get("title", ""),
                "url": url,
                "snippet": (item.get("content") or "")[:600],
                "relevance": item.get("score"),
                "trusted_domain": _is_trusted(url),
                "evidence_type": "research-backed",
            }
        )

    results.sort(key=lambda item: (not item["trusted_domain"], -(item["relevance"] or 0)))
    result = {
        "available": True,
        "provider": "Tavily",
        "query": query,
        "results": results[:max_results],
        "note": (
            "Search results are research context, not verified measurements for this farm. "
            "They never override the deterministic scoring engine."
        ),
    }
    cache.set(cache_key, result)
    return result


def rotation_query(crop_names: list[str], region: str | None = None) -> str:
    sequence = " then ".join(crop_names)
    location = f" in {region}" if region else ""
    return (
        f"agronomic evidence for crop rotation {sequence}{location}: "
        "soil nitrogen contribution, water requirement, pest and disease break, "
        "crop water use and recommended rotation practices"
    )
