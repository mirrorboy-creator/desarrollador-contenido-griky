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


def _parse_raw_authors(raw: object) -> list[str]:
    """Return a list of individual author strings from various API formats."""
    if isinstance(raw, list):
        return [str(a).strip() for a in raw if str(a).strip()]
    if not raw or not str(raw).strip():
        return []
    s = str(raw).strip()
    # Split on "; " first (common separator), then " and ", then ", " only if result looks valid
    if "; " in s:
        return [a.strip() for a in s.split("; ") if a.strip()]
    if " and " in s.lower():
        return [a.strip() for a in re.split(r'\s+and\s+', s, flags=re.IGNORECASE) if a.strip()]
    return [s]


def _author_to_apa(author: str) -> tuple[str, str]:
    """
    Convert a single author string to (last_name, apa_form).
    apa_form example: "García, J. M."
    """
    author = author.strip()
    if not author:
        return ("Autor desconocido", "Autor desconocido")

    # Already in "Last, First" format
    if "," in author:
        parts = author.split(",", 1)
        last = parts[0].strip()
        first_part = parts[1].strip() if len(parts) > 1 else ""
        initials = _initials(first_part)
        apa = f"{last}, {initials}".rstrip(", ")
        return (last, apa)

    # "First [Middle] Last" format
    words = author.split()
    if len(words) == 1:
        return (words[0], words[0])
    last = words[-1]
    initials = _initials(" ".join(words[:-1]))
    apa = f"{last}, {initials}".rstrip(", ")
    return (last, apa)


def _initials(name_part: str) -> str:
    """Turn 'John Michael' or 'J. M.' into 'J. M.'"""
    tokens = name_part.strip().split()
    result = []
    for t in tokens:
        t = t.strip(".")
        if t:
            result.append(t[0].upper() + ".")
    return " ".join(result)


def _build_link(c: dict) -> str:
    """Return the best available URL/DOI string."""
    doi = c.get("doi", "")
    url = c.get("url", c.get("link", ""))
    if doi:
        doi = doi.strip()
        if doi.startswith("http"):
            return doi
        return f"https://doi.org/{doi}"
    if url:
        return url.strip()
    return ""


def format_citations_apa(citations: list[dict]) -> str:
    """
    Format a list of citation dicts as a numbered APA reference list.
    Returns the full block as a string (entries separated by blank lines).
    """
    if not citations:
        return ""
    entries = []
    for c in citations:
        _, full_ref = _format_single_citation(c)
        entries.append(full_ref)
    return "\n\n".join(entries)


def _format_single_citation(c: dict) -> tuple[str, str]:
    """
    Returns (in_text, full_reference) for one citation dict.
    in_text  → "(Apellido, Año)"
    full_ref → "Apellido, N. (Año). Título. *Revista*, *Vol*(Núm), pp–pp. https://..."
    """
    raw_authors = c.get("authors", c.get("author", ""))
    year = str(c.get("year", c.get("published_year", "s.f."))).strip()
    title = c.get("title", "Sin título").strip()
    journal = c.get("journal", c.get("venue", c.get("source", ""))).strip()
    volume = str(c.get("volume", "")).strip()
    issue = str(c.get("issue", c.get("number", ""))).strip()
    pages = str(c.get("pages", "")).strip()
    link = _build_link(c)

    author_list = _parse_raw_authors(raw_authors)
    if not author_list:
        author_list = ["Autor desconocido"]

    apa_authors_parts = []
    first_last = "Autor desconocido"
    for i, a in enumerate(author_list):
        last, apa_form = _author_to_apa(a)
        if i == 0:
            first_last = last
        apa_authors_parts.append(apa_form)

    # APA 7: up to 20 authors with ", " separator; last preceded by "& "
    if len(apa_authors_parts) > 1:
        authors_str = ", ".join(apa_authors_parts[:-1]) + ", & " + apa_authors_parts[-1]
    else:
        authors_str = apa_authors_parts[0]

    in_text = f"({first_last}, {year})"

    # Build full reference
    ref = f"{authors_str} ({year}). {title}."
    if journal:
        ref += f" *{journal}*"
        if volume:
            ref += f", *{volume}*"
            if issue:
                ref += f"({issue})"
        if pages:
            ref += f", {pages}"
        ref += "."
    if link:
        ref += f" {link}"

    return in_text, ref


def build_citations_block(citations: list[dict]) -> str:
    """
    Build the citations block passed to Claude, showing both
    the in-text key and the full APA reference for every source.
    """
    if not citations:
        return "No hay citas disponibles. Indica con [CITA REQUERIDA: tema] dónde se necesitaría una referencia."

    lines = ["Usa estas citas reales. Para cada una se muestra la clave en texto y la referencia completa:\n"]
    for i, c in enumerate(citations, 1):
        in_text, full_ref = _format_single_citation(c)
        lines.append(f"{i}. Cita en texto: {in_text}")
        lines.append(f"   Referencia completa: {full_ref}")
    return "\n".join(lines)


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
- Integra las citas en el texto usando EXACTAMENTE este formato APA: (Apellido, Año) — por ejemplo: (García, 2021) o (Smith & Jones, 2019)
- Usa SOLO los apellidos del primer autor (o primer y segundo si son dos)
- Al final de cada unidad incluye una sección "## Referencias" con TODAS las fuentes citadas
- Cada entrada de referencia debe seguir APA 7 completo: Apellido, N. (Año). Título del trabajo. *Revista*, *Vol*(Núm), pp–pp. https://doi.org/xxxxx
- Asegúrate de que CADA autor citado en el texto tenga su entrada completa en Referencias
- Usa las citas reales proporcionadas; no inventes referencias

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
    citations: list[dict],
    additional_instructions: str = "",
) -> str:
    citations_block = build_citations_block(citations)
    extra = f"\n=== INSTRUCCIONES ADICIONALES DEL AUTOR ===\n{additional_instructions.strip()}\n" if additional_instructions.strip() else ""
    return f"""Basándote en los siguientes documentos del curso, desarrolla el contenido académico completo para la unidad indicada.

{course_context}

=== UNIDAD A DESARROLLAR ===
{unit_name}

=== TEMAS Y SUBTEMAS ===
{unit_topics}

=== CITAS ACADÉMICAS DISPONIBLES ===
{citations_block}
{extra}
=== INSTRUCCIONES ESPECÍFICAS ===
1. Desarrolla TODO el contenido de esta unidad con extensión completa según el silabo
2. Sigue la estructura exacta del template/plantilla proporcionada
3. Integra las citas en el texto con formato (Apellido, Año) — usa los apellidos y años exactos de las citas proporcionadas arriba
4. Incluye [IMAGEN SUGERIDA: ...] en puntos estratégicos
5. Aplica las reglas matemáticas si el tema incluye fórmulas (solo Unicode, nunca LaTeX)
6. Al finalizar incluye una sección "## Referencias" con las entradas APA completas de TODAS las fuentes que citaste, copiando exactamente las referencias completas de la sección "CITAS ACADÉMICAS DISPONIBLES"
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
