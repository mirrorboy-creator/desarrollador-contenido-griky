"""
Desarrollador de Contenido Griky - FastAPI Backend
Generates complete academic content using Claude claude-opus-4-6.
"""
import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Optional

import anthropic
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from file_processor import process_uploaded_file, build_course_context
from content_generator import extract_units_from_context, generate_content_stream
from docx_builder import build_docx

load_dotenv()

app = FastAPI(title="Desarrollador de Contenido Griky", version="1.0.0")

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
GENERATED_DIR = BASE_DIR / "generated"
STATIC_DIR = BASE_DIR / "static"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(GENERATED_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

os.makedirs(BASE_DIR / "static", exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB per file


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Desarrollador de Contenido Griky"}


@app.post("/generate-stream")
async def generate_content_endpoint(
    request: Request,
    files: list[UploadFile] = File(...),
    unit_filter: str = Form(""),
    additional_instructions: str = Form(""),
):
    """
    Main endpoint: accepts uploaded files, streams content generation progress.
    Returns an SSE stream with progress updates and finally the download URL.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY no está configurada en el servidor.")

    # Process uploaded files
    if not files:
        raise HTTPException(status_code=400, detail="Debes subir al menos un archivo.")

    processed_files = []
    errors = []

    for uploaded_file in files:
        filename = uploaded_file.filename or "unknown"
        ext = Path(filename).suffix.lower()

        if ext not in ALLOWED_EXTENSIONS:
            errors.append(f"Formato no permitido: {filename} ({ext})")
            continue

        file_bytes = await uploaded_file.read()

        if len(file_bytes) > MAX_FILE_SIZE:
            errors.append(f"Archivo demasiado grande: {filename} (máx 20MB)")
            continue

        try:
            processed = process_uploaded_file(filename, file_bytes)
            processed_files.append(processed)
        except Exception as e:
            errors.append(f"Error procesando {filename}: {str(e)}")

    if not processed_files:
        detail = "No se pudo procesar ningún archivo."
        if errors:
            detail += " Errores: " + "; ".join(errors)
        raise HTTPException(status_code=400, detail=detail)

    # Build session ID for this generation
    session_id = str(uuid.uuid4())

    async def event_stream():
        try:
            # Step 1: Notify files processed
            yield f"data: {json.dumps({'type': 'status', 'message': f'Procesados {len(processed_files)} archivos correctamente.', 'progress': 5})}\n\n"

            if errors:
                yield f"data: {json.dumps({'type': 'warning', 'message': 'Advertencias: ' + '; '.join(errors)})}\n\n"

            await asyncio.sleep(0.1)

            # Step 2: Build course context
            yield f"data: {json.dumps({'type': 'status', 'message': 'Analizando estructura del curso...', 'progress': 10})}\n\n"
            course_context = build_course_context(processed_files)

            # Step 3: Extract units using Claude
            yield f"data: {json.dumps({'type': 'status', 'message': 'Identificando unidades del curso con IA...', 'progress': 15})}\n\n"

            try:
                units, subject_area = await extract_units_from_context(course_context, api_key)
            except anthropic.AuthenticationError:
                yield f"data: {json.dumps({'type': 'error', 'message': 'API key de Anthropic inválida o sin permisos.'})}\n\n"
                return
            except anthropic.RateLimitError:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Límite de rate de Anthropic alcanzado. Espera un momento.'})}\n\n"
                return
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Error al analizar el curso: {str(e)}'})}\n\n"
                return

            # Filter to a single unit/week if the user requested one
            unit_filter_clean = unit_filter.strip()
            if unit_filter_clean:
                filtered = [u for u in units if unit_filter_clean.lower() in u.get("name", "").lower()]
                if filtered:
                    units = filtered
                    yield f"data: {json.dumps({'type': 'status', 'message': f'Filtro aplicado: generando solo «{unit_filter_clean}» ({len(units)} unidad/es).', 'progress': 20})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'warning', 'message': f'No se encontró «{unit_filter_clean}» entre las unidades. Se generarán todas.'})}\n\n"

            yield f"data: {json.dumps({'type': 'status', 'message': f'Curso identificado: {subject_area} | {len(units)} unidades a generar.', 'progress': 20})}\n\n"

            # Step 4: Generate content unit by unit
            units_content = []
            total_units = len(units)

            for unit_idx, unit in enumerate(units, 1):
                unit_name = unit.get("name", f"Unidad {unit_idx}")
                unit_topics = unit.get("topics", "")

                # Fetch citations
                yield f"data: {json.dumps({'type': 'status', 'message': f'[{unit_idx}/{total_units}] Obteniendo referencias para: {unit_name}...', 'progress': 20 + (unit_idx - 1) / total_units * 60})}\n\n"

                from content_generator import fetch_citations, format_citations_apa, build_content_prompt, build_system_prompt
                citations = await fetch_citations(unit_topics[:200], subject_area)
                citations_text = format_citations_apa(citations)

                yield f"data: {json.dumps({'type': 'status', 'message': f'[{unit_idx}/{total_units}] Generando contenido: {unit_name}...', 'progress': 20 + (unit_idx - 0.5) / total_units * 60})}\n\n"

                # Generate content with Claude
                prompt = build_content_prompt(
                    course_context=course_context,
                    unit_name=unit_name,
                    unit_topics=unit_topics,
                    subject_area=subject_area,
                    citations=citations,
                    additional_instructions=additional_instructions,
                )

                try:
                    anthropic_client = anthropic.Anthropic(api_key=api_key)
                    unit_content_parts = []

                    with anthropic_client.messages.stream(
                        model="claude-opus-4-6",
                        max_tokens=8000,
                        system=build_system_prompt(),
                        messages=[{"role": "user", "content": prompt}],
                    ) as stream:
                        chunk_count = 0
                        for text_chunk in stream.text_stream:
                            unit_content_parts.append(text_chunk)
                            chunk_count += 1
                            # Send preview every 50 chunks to avoid flooding
                            if chunk_count % 50 == 0:
                                preview = "".join(unit_content_parts[-200:])
                                yield f"data: {json.dumps({'type': 'content_preview', 'unit': unit_idx, 'preview': preview})}\n\n"

                    unit_content = "".join(unit_content_parts)
                    units_content.append({
                        "unit_name": unit_name,
                        "content": unit_content,
                        "citations": citations,
                        "citations_text": citations_text,
                    })

                    progress = 20 + unit_idx / total_units * 60
                    yield f"data: {json.dumps({'type': 'unit_complete', 'unit': unit_idx, 'unit_name': unit_name, 'progress': progress})}\n\n"

                except anthropic.AuthenticationError:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'API key de Anthropic inválida.'})}\n\n"
                    return
                except anthropic.RateLimitError:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Límite de rate alcanzado. Intenta de nuevo en unos minutos.'})}\n\n"
                    return
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Error generando unidad {unit_idx}: {str(e)}'})}\n\n"
                    return

            # Step 5: Build DOCX
            yield f"data: {json.dumps({'type': 'status', 'message': 'Construyendo documento Word...', 'progress': 85})}\n\n"

            # Extract course name from context or use first unit
            course_name = subject_area
            for f in processed_files:
                if "silabo" in f["filename"].lower() or "syllabus" in f["filename"].lower():
                    # Try to extract course name from content
                    lines = f["content"].split('\n')
                    for line in lines[:10]:
                        if line.strip() and len(line.strip()) > 5:
                            course_name = line.strip()[:80]
                            break
                    break

            try:
                docx_bytes = build_docx(units_content, course_name, subject_area)
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Error construyendo el documento: {str(e)}'})}\n\n"
                return

            # Save DOCX
            output_filename = f"contenido_academico_{session_id}.docx"
            output_path = GENERATED_DIR / output_filename
            with open(output_path, "wb") as fout:
                fout.write(docx_bytes)

            yield f"data: {json.dumps({'type': 'status', 'message': 'Documento generado exitosamente.', 'progress': 100})}\n\n"
            yield f"data: {json.dumps({'type': 'complete', 'download_url': f'/download/{output_filename}', 'filename': output_filename, 'progress': 100})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Error inesperado: {str(e)}'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/download/{filename}")
async def download_file(filename: str):
    """Download a generated DOCX file."""
    # Security: only allow files from generated directory, no path traversal
    safe_filename = Path(filename).name
    if not safe_filename.endswith(".docx") or ".." in filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido.")

    file_path = GENERATED_DIR / safe_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado. Puede haber expirado.")

    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=safe_filename,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
