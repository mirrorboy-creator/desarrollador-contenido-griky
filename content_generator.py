"""
Content generator module for Desarrollador de Contenido Griky.
Uses Claude claude-opus-4-6 to generate complete academic content.
"""
import asyncio
import json
import os
import re
from typing import AsyncGenerator, Optional

import httpx
import anthropic


CITATIONS_API_URL = "https://citationhunter.onrender.com/citations"
CLAUDE_MODEL = "claude-opus-4-6"


async def fetch_citations(topic: str, area: str) -> list[dict]:
    """
    Call the external citations API to get real open access academic references.
    Returns list of citation dicts, or empty list on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                CITATIONS_API_URL,
                json={"q": topic, "area": area},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "results" in data:
                    return data["results"]
                if isinstance(data, dict) and "citations" in data:
                    return data["citations"]
            return []
    except Exception:
        return []


def format_citations_apa(citations: list[dict]) -> str:
    """Format citations list as APA references."""
    if not citations:
        return ""
    lines = []
    for c in citations:
        # Try to build APA from common fields
        authors = c.get("authors", c.get("author", "Autor desconocido"))
        year = c.get("year", c.get("published_year", "s.f."))
        title = c.get("title", "Sin título")
        journal = c.get("journal", c.get("venue", c.get("source", "")))
        doi = c.get("doi", c.get("url", ""))
        volume = c.get("volume", "")
        issue = c.get("issue", c.get("number", ""))
        pages = c.get("pages", "")

        apa = f"{authors} ({year}). {title}."
        if journal:
            apa += f" *{journal}*"
        if volume:
            apa += f", *{volume}*"
        if issue:
            apa += f"({issue})"
        if pages:
            apa += f", {pages}"
        apa += "."
        if doi:
            apa += f" {doi}"
        lines.append(apa)
    return "\n\n".join(lines)


def build_system_prompt() -> str:
    return """Eres un experto desarrollador de contenido académico universitario.
Tu rol es crear contenido académico completo, riguroso y de alta calidad en español,
siguiendo exactamente los parámetros, estructura, extensión y condiciones especificadas
en los documentos del curso proporcionados.

REGLAS MATEMÁTICAS (aplica siempre que haya fórmulas):
- USA SIEMPRE caracteres Unicode: ∫, ∑, √, π, Δ, ±, ², ³, ℝ, ∈, ⊆, →, ∞
- NUNCA uses LaTeX ni delimitadores como $, $$, \\[, \\]
- Variables en cursiva, funciones estándar en romano: sin, cos, log, exp, lim
- Espaciado correcto alrededor de operadores: a + b, x · y, f(x) = 2x + 1

SUGERENCIAS DE IMÁGENES:
- Incluye marcadores en el texto así: [IMAGEN SUGERIDA: descripción detallada de qué imagen va aquí y por qué es relevante pedagógicamente]
- Colócalos en lugares estratégicos donde una imagen mejoraría la comprensión

CITAS ACADÉMICAS:
- Integra las citas naturalmente en el texto usando formato APA: (Autor, año)
- Incluye la lista completa de referencias en APA al final de cada unidad
- Usa las citas reales proporcionadas, no inventes referencias

ESTRUCTURA:
- Sigue EXACTAMENTE la estructura de unidades, temas y subtemas del silabo
- Respeta la extensión y número de páginas especificados
- Desarrolla cada tema con profundidad académica apropiada para el nivel universitario
- Incluye: introducción al tema, desarrollo conceptual, ejemplos, aplicaciones, reflexión

IDIOMA Y TONO:
- Español académico formal
- Segunda persona plural cuando te diriges al estudiante (ustedes/usted)
- Lenguaje inclusivo y culturalmente apropiado para el contexto latinoamericano"""


def build_content_prompt(
    course_context: str,
    unit_name: str,
    unit_topics: str,
    subject_area: str,
    citations_text: str,
) -> str:
    return f"""Basándote en los siguientes documentos del curso, desarrolla el contenido académico completo para la unidad indicada.

{course_context}

=== UNIDAD A DESARROLLAR ===
{unit_name}

=== TEMAS Y SUBTEMAS ===
{unit_topics}

=== CITAS ACADÉMICAS DISPONIBLES ===
{citations_text if citations_text else "No hay citas disponibles. Indica dónde irían referencias relevantes."}

=== INSTRUCCIONES ESPECÍFICAS ===
1. Desarrolla TODO el contenido de esta unidad con extensión completa según el silabo
2. Sigue la estructura exacta del template/plantilla proporcionada
3. Integra las citas académicas de forma natural en el texto
4. Incluye [IMAGEN SUGERIDA: ...] en puntos estratégicos
5. Aplica las reglas matemáticas si el tema incluye fórmulas (solo Unicode, nunca LaTeX)
6. Termina con una sección "Referencias" en formato APA completo
7. El contenido debe ser académicamente riguroso y apropiado para nivel universitario

Desarrolla el contenido completo ahora:"""


async def generate_content_stream(
    course_context: str,
    units: list[dict],
    subject_area: str,
    api_key: str,
    progress_callback=None,
) -> AsyncGenerator[str, None]:
    """
    Generate complete academic content for all units using Claude claude-opus-4-6.
    Yields progress messages and content chunks.
    """
    client = anthropic.Anthropic(api_key=api_key)

    all_content = []
    total_units = len(units)

    for unit_idx, unit in enumerate(units, 1):
        unit_name = unit.get("name", f"Unidad {unit_idx}")
        unit_topics = unit.get("topics", "")

        if progress_callback:
            await progress_callback(
                f"Obteniendo referencias académicas para: {unit_name}...",
                (unit_idx - 1) / total_units * 100,
            )

        # Fetch citations for this unit
        citations = await fetch_citations(unit_topics[:200], subject_area)
        citations_text = format_citations_apa(citations)

        if progress_callback:
            await progress_callback(
                f"Generando contenido para: {unit_name}...",
                ((unit_idx - 1) / total_units * 100) + (1 / total_units * 30),
            )

        prompt = build_content_prompt(
            course_context=course_context,
            unit_name=unit_name,
            unit_topics=unit_topics,
            subject_area=subject_area,
            citations_text=citations_text,
        )

        # Call Claude with streaming
        unit_content_parts = []

        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=8000,
            system=build_system_prompt(),
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text_chunk in stream.text_stream:
                unit_content_parts.append(text_chunk)
                yield json.dumps({"type": "content_chunk", "text": text_chunk, "unit": unit_idx}) + "\n"

        unit_content = "".join(unit_content_parts)
        all_content.append({
            "unit_name": unit_name,
            "content": unit_content,
            "citations": citations,
            "citations_text": citations_text,
        })

        if progress_callback:
            await progress_callback(
                f"Unidad {unit_idx}/{total_units} completada: {unit_name}",
                (unit_idx / total_units) * 100,
            )

        yield json.dumps({
            "type": "unit_complete",
            "unit": unit_idx,
            "unit_name": unit_name,
            "progress": (unit_idx / total_units) * 100,
        }) + "\n"

    yield json.dumps({"type": "all_complete", "units": len(all_content)}) + "\n"


async def extract_units_from_context(course_context: str, api_key: str) -> tuple[list[dict], str]:
    """
    Use Claude to extract units and subject area from the course context.
    Returns (units_list, subject_area).
    """
    client = anthropic.Anthropic(api_key=api_key)

    extraction_prompt = f"""Analiza los siguientes documentos del curso y extrae:
1. La lista de unidades/módulos del curso con sus temas principales
2. El área temática/disciplina del curso

{course_context[:12000]}

Responde ÚNICAMENTE en JSON válido con este formato exacto:
{{
  "subject_area": "nombre del área o disciplina",
  "course_name": "nombre del curso",
  "units": [
    {{
      "name": "Unidad 1: Nombre",
      "topics": "Lista de temas y subtemas de esta unidad"
    }}
  ]
}}"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": extraction_prompt}],
    )

    raw = response.content[0].text.strip()

    # Extract JSON from response
    json_match = re.search(r'\{[\s\S]*\}', raw)
    if json_match:
        try:
            data = json.loads(json_match.group())
            units = data.get("units", [])
            subject_area = data.get("subject_area", "Ciencias")
            if not units:
                units = [{"name": "Contenido del Curso", "topics": "Todos los temas del curso"}]
            return units, subject_area
        except json.JSONDecodeError:
            pass

    # Fallback: single unit
    return [{"name": "Contenido del Curso", "topics": "Todos los temas del curso"}], "Educación"
