import base64
import binascii
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import time
import zipfile
from pathlib import PurePosixPath
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.db import Base, make_session_factory
from app.models import (
    CoverageReport,
    DocumentationDraft,
    ExportTask,
    FunctionEntity,
    Project,
    SourceFile,
)
from app.schemas import (
    CoverageRead,
    DraftRead,
    ExportFormat,
    ExportRead,
    ExportRequest,
    FunctionRead,
    GenerateRequest,
    GenerateResponse,
    ParseRequest,
    ParseResponse,
    PredictionRequest,
    PredictionResponse,
    ProjectCreate,
    ProjectList,
    ProjectRead,
    ProjectTree,
    ProjectUpdate,
    TreeFile,
)
from app.services.exports import (
    documentation_markdown,
    plantuml_code,
    render_html,
    render_pdf,
    render_plantuml_image,
)
from app.services.generation import AIClient
from app.services.ml import FEATURES, model_plot_points, predict
from app.services.parsers import ParsedFile, language_for_path, parse_source

PARSE_DURATION = Histogram(
    "docgen_parse_duration_seconds", "Time spent parsing source files", ["language"]
)
FILES_PARSED = Counter("docgen_files_parsed_total", "Number of successfully parsed source files")
COVERAGE_PERCENT = Gauge("docgen_coverage_percent", "Latest documentation coverage by project", ["project_id"])
EXPORT_DURATION = Histogram(
    "docgen_export_duration_seconds", "Time spent creating exports", ["format"]
)
DOCS_GENERATED = Counter("docgen_docs_generated_total", "Number of generated documentation drafts")
HTTP_REQUESTS = Counter(
    "http_requests_total", "Number of HTTP requests", ["method", "path", "status"]
)
HTTP_DURATION = Histogram("http_request_duration_seconds", "HTTP request duration")


def create_app(database_url: str | None = None) -> FastAPI:
    app = FastAPI(
        title="DocGen",
        description="Source-code documentation generator and digital twin",
        version="1.0.0",
    )
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///./docgen.db")
    engine, session_factory = make_session_factory(url)
    app.state.engine = engine
    app.state.session_factory = session_factory

    @app.on_event("startup")
    def initialize_database() -> None:
        Base.metadata.create_all(bind=engine)

    @app.on_event("shutdown")
    def close_engine() -> None:
        engine.dispose()

    def db_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    @app.middleware("http")
    async def observe_requests(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        HTTP_DURATION.observe(time.perf_counter() - started)
        route = request.scope.get("route")
        route_path = route.path if route else request.url.path
        HTTP_REQUESTS.labels(request.method, route_path, str(response.status_code)).inc()
        return response

    def project_or_404(session: Session, project_id: str) -> Project:
        project = session.scalar(
            select(Project)
            .where(Project.id == project_id)
            .options(
                selectinload(Project.files)
                .selectinload(SourceFile.functions)
                .selectinload(FunctionEntity.drafts)
            )
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    def coverage_for(session: Session, project: Project) -> dict:
        functions = [
            function
            for source_file in project.files
            for function in source_file.functions
        ]
        uncovered = []
        documented = 0
        for function in functions:
            is_documented = bool(
                function.docblock_raw
                or (function.drafts and function.drafts[-1].generated_text.strip())
            )
            if is_documented:
                documented += 1
            else:
                uncovered.append(
                    {
                        "function_id": function.id,
                        "name": function.name,
                        "file": function.source_file.path,
                        "line": function.line_start,
                    }
                )
        percent = round(documented / len(functions) * 100, 2) if functions else 0.0
        COVERAGE_PERCENT.labels(project.id).set(percent)
        return {
            "total": len(functions),
            "documented": documented,
            "percent": percent,
            "uncovered": uncovered,
            "violations": _style_violations(functions),
        }

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home() -> str:
        return (
            "<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<title>DocGen</title><h1>DocGen</h1>"
            "<p>Source-code documentation generator</p>"
            "<p><a href='/docs'>OpenAPI</a> · <a href='/dashboard/ml'>Digital twin</a></p>"
            "</html>"
        )

    @app.post("/projects", response_model=ProjectRead, status_code=201)
    def create_project(payload: ProjectCreate, session: Session = Depends(db_session)):
        project = Project(name=payload.name)
        session.add(project)
        session.flush()
        try:
            if payload.source:
                input_files = _source_files(payload.source, int(os.getenv("MAX_UPLOAD_BYTES", "52428800")))
                if len(input_files) > 1000:
                    raise HTTPException(status_code=422, detail="A project may contain at most 1000 files")
                for path, content in input_files:
                    _upsert_source_file(session, project, path, content)
            session.commit()
        except Exception:
            session.rollback()
            raise
        return _project_read(project)

    @app.get("/projects", response_model=ProjectList)
    def list_projects(
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        session: Session = Depends(db_session),
    ):
        total = session.scalar(select(func.count()).select_from(Project)) or 0
        projects = session.scalars(
            select(Project).order_by(Project.created_at.desc()).limit(limit).offset(offset)
        ).all()
        counts = dict(
            session.execute(
                select(SourceFile.project_id, func.count(SourceFile.id))
                .group_by(SourceFile.project_id)
            ).all()
        )
        return ProjectList(
            items=[_project_read(project, counts.get(project.id, 0)) for project in projects],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get("/projects/{project_id}", response_model=ProjectRead)
    def get_project(project_id: str, session: Session = Depends(db_session)):
        project = project_or_404(session, project_id)
        return _project_read(project)

    @app.patch("/projects/{project_id}", response_model=ProjectRead)
    def update_project(
        project_id: str, payload: ProjectUpdate, session: Session = Depends(db_session)
    ):
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        project.name = payload.name
        session.commit()
        return _project_read(project)

    @app.delete("/projects/{project_id}", status_code=204)
    def delete_project(project_id: str, session: Session = Depends(db_session)):
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        session.delete(project)
        session.commit()
        return Response(status_code=204)

    @app.get("/projects/{project_id}/tree", response_model=ProjectTree)
    def project_tree(project_id: str, session: Session = Depends(db_session)):
        project = project_or_404(session, project_id)
        return ProjectTree(
            id=project.id,
            name=project.name,
            files=[
                TreeFile(
                    id=file.id,
                    path=file.path,
                    language=file.language,
                    content_hash=file.content_hash,
                    classes=file.tree.get("classes", []),
                    functions=[
                        _function_read(function)
                        for function in sorted(file.functions, key=lambda item: item.line_start)
                    ],
                )
                for file in sorted(project.files, key=lambda item: item.path)
            ],
        )

    @app.post("/projects/{project_id}/parse", response_model=ParseResponse, status_code=202)
    def parse_project(
        project_id: str,
        payload: ParseRequest | None = None,
        session: Session = Depends(db_session),
    ):
        project = project_or_404(session, project_id)
        if payload:
            for source_file in payload.files:
                _upsert_source_file(session, project, source_file.path, source_file.content)
        if len(project.files) > 1000:
            raise HTTPException(status_code=422, detail="A project may contain at most 1000 files")
        parsed_count = classes_count = 0
        complexities = []
        for source_file in project.files:
            parsed_at = time.perf_counter()
            result = _parse_one(source_file.path, source_file.content)
            PARSE_DURATION.labels(result.language).observe(time.perf_counter() - parsed_at)
            source_file.language = result.language
            source_file.tree = {"classes": result.classes}
            source_file.functions.clear()
            session.flush()
            for function in result.functions:
                source_file.functions.append(
                    FunctionEntity(
                        name=function.name,
                        class_name=function.class_name,
                        params=function.params,
                        line_start=function.line_start,
                        line_end=function.line_end,
                        complexity=function.complexity,
                        docblock_raw=function.docblock_raw,
                    )
                )
                complexities.append(function.complexity)
            parsed_count += 1
            classes_count += len(result.classes)
        languages = {file.language for file in project.files}
        project.language = next(iter(languages)) if len(languages) == 1 else ("mixed" if languages else None)
        session.commit()
        FILES_PARSED.inc(parsed_count)
        function_count = len(complexities)
        return ParseResponse(
            project_id=project.id,
            files_parsed=parsed_count,
            classes=classes_count,
            functions=function_count,
            average_complexity=round(sum(complexities) / function_count, 2) if function_count else 0.0,
        )

    @app.post("/projects/{project_id}/generate", response_model=GenerateResponse)
    async def generate_docs(
        project_id: str,
        payload: GenerateRequest | None = None,
        session: Session = Depends(db_session),
    ):
        project = project_or_404(session, project_id)
        if not any(file.functions for file in project.files):
            raise HTTPException(status_code=409, detail="Parse the project before generating documentation")
        client = AIClient()
        drafts = []
        warnings = []
        for source_file in project.files:
            for function in source_file.functions:
                code_lines = source_file.content.splitlines()
                source = "\n".join(code_lines[function.line_start - 1 : function.line_end])
                try:
                    style = payload.style if payload else "google"
                    generated_text, model_name = await client.generate(function, source, style)
                except httpx.HTTPError as exc:
                    raise HTTPException(status_code=502, detail=f"AI service request failed: {exc}") from exc
                except RuntimeError as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
                draft = DocumentationDraft(
                    function=function,
                    function_id=function.id,
                    generated_text=generated_text,
                    ai_model=model_name,
                )
                session.add(draft)
                session.flush()
                drafts.append(
                    DraftRead(
                        id=draft.id,
                        function_id=function.id,
                        generated_text=draft.generated_text,
                        model=draft.ai_model,
                    )
                )
                if model_name == "local-placeholder":
                    warnings.append("No OPENAI_API_KEY configured; deterministic local drafts were generated.")
        session.flush()
        coverage = coverage_for(session, project)
        session.add(
            CoverageReport(
                project_id=project.id,
                total=coverage["total"],
                documented=coverage["documented"],
                percent=coverage["percent"],
                uncovered=coverage["uncovered"],
            )
        )
        session.commit()
        DOCS_GENERATED.inc(len(drafts))
        return GenerateResponse(
            project_id=project.id,
            drafts=drafts,
            coverage=CoverageRead(**coverage),
            warnings=sorted(set(warnings)),
        )

    @app.get("/projects/{project_id}/docs", response_model=list[DraftRead])
    def list_docs(project_id: str, session: Session = Depends(db_session)):
        project_or_404(session, project_id)
        results = session.execute(
            select(DocumentationDraft, FunctionEntity)
            .join(FunctionEntity)
            .join(SourceFile)
            .where(SourceFile.project_id == project_id)
            .order_by(DocumentationDraft.created_at.desc())
        ).all()
        return [
            DraftRead(
                id=draft.id,
                function_id=draft.function_id,
                generated_text=draft.generated_text,
                model=draft.ai_model,
            )
            for draft, _ in results
        ]

    @app.get("/projects/{project_id}/coverage", response_model=CoverageRead)
    def get_coverage(project_id: str, session: Session = Depends(db_session)):
        project = project_or_404(session, project_id)
        return CoverageRead(**coverage_for(session, project))

    @app.post("/projects/{project_id}/export", response_model=ExportRead, status_code=201)
    async def export_project(
        project_id: str,
        payload: ExportRequest,
        session: Session = Depends(db_session),
    ):
        project = project_or_404(session, project_id)
        started = time.perf_counter()
        markdown = documentation_markdown(project, project.files)
        selected_format = payload.format
        if selected_format == ExportFormat.markdown:
            content, extension = markdown.encode("utf-8"), "md"
        elif selected_format == ExportFormat.html:
            content, extension = render_html(markdown).encode("utf-8"), "html"
        elif selected_format == ExportFormat.pdf:
            content, extension = render_pdf(markdown), "pdf"
        else:
            source = plantuml_code(project, project.files)
            image = await render_plantuml_image(source)
            content, extension = source.encode("utf-8"), "puml"
            if image is not None:
                content, extension = image, "svg"
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", project.name).strip("-") or "project"
        task = ExportTask(
            project_id=project.id,
            format=selected_format.value,
            status="completed",
            file_name=f"{name}.{extension}",
            content=content,
        )
        session.add(task)
        session.commit()
        EXPORT_DURATION.labels(selected_format.value).observe(time.perf_counter() - started)
        return _export_read(task)

    @app.get("/exports/{export_id}", response_model=ExportRead)
    def get_export(export_id: str, session: Session = Depends(db_session)):
        task = session.get(ExportTask, export_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Export not found")
        return _export_read(task)

    @app.get("/projects/{project_id}/exports", response_model=list[ExportRead])
    def list_exports(project_id: str, session: Session = Depends(db_session)):
        project_or_404(session, project_id)
        tasks = session.scalars(
            select(ExportTask)
            .where(ExportTask.project_id == project_id)
            .order_by(ExportTask.created_at.desc())
        ).all()
        return [_export_read(task) for task in tasks]

    @app.get("/exports/{export_id}/download")
    def download_export(export_id: str, session: Session = Depends(db_session)):
        task = session.get(ExportTask, export_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Export not found")
        return StreamingResponse(
            io.BytesIO(task.content),
            media_type=_content_type(task.file_name),
            headers={"Content-Disposition": f'attachment; filename="{task.file_name}"'},
        )

    @app.get("/search")
    def search_docs(
        q: str = Query(..., min_length=1, max_length=200),
        project_id: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        session: Session = Depends(db_session),
    ):
        statement = (
            select(FunctionEntity, SourceFile, DocumentationDraft)
            .join(SourceFile)
            .outerjoin(DocumentationDraft, DocumentationDraft.function_id == FunctionEntity.id)
            .where(
                (DocumentationDraft.generated_text.ilike(f"%{q}%"))
                | (FunctionEntity.docblock_raw.ilike(f"%{q}%"))
                | (FunctionEntity.name.ilike(f"%{q}%"))
                | (SourceFile.path.ilike(f"%{q}%"))
            )
        )
        if project_id:
            statement = statement.where(SourceFile.project_id == project_id)
        results = session.execute(statement.limit(limit)).all()
        return [
            {
                "draft_id": draft.id if draft else None,
                "function_id": function.id,
                "function": function.name,
                "file": source_file.path,
                "text": (
                    draft.generated_text
                    if draft and q.casefold() in draft.generated_text.casefold()
                    else function.docblock_raw or (draft.generated_text if draft else "")
                ),
            }
            for function, source_file, draft in results
        ]

    @app.post("/predict", response_model=PredictionResponse)
    def predict_time(payload: PredictionRequest):
        output = predict([getattr(payload, feature) for feature in FEATURES])
        return PredictionResponse(**output)

    @app.get("/dashboard/ml", response_class=HTMLResponse)
    def ml_dashboard():
        return HTMLResponse(_ml_dashboard_html())

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/health")
    async def health(session: Session = Depends(db_session)):
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail={"status": "fail", "db": str(exc)}) from exc
        plantuml_status = "not_configured"
        plantuml_url = os.getenv("PLANTUML_URL")
        if plantuml_url:
            try:
                async with httpx.AsyncClient(timeout=2) as client:
                    response = await client.get(f"{plantuml_url.rstrip('/')}/")
                plantuml_status = "ok" if response.is_success else "degraded"
            except httpx.HTTPError:
                plantuml_status = "degraded"
        return {"status": "ok", "db": "ok", "plantuml": plantuml_status}

    return app


def _source_files(source, max_upload_bytes: int) -> list[tuple[str, str]]:
    if source.type == "files":
        return _validated_files([(item.path, item.content) for item in source.files], max_upload_bytes)
    if source.type == "zip":
        if not source.content_base64:
            raise HTTPException(status_code=422, detail="ZIP source requires content_base64")
        try:
            data = base64.b64decode(source.content_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(status_code=422, detail="Invalid base64 ZIP payload") from exc
        if len(data) > max_upload_bytes:
            raise HTTPException(status_code=413, detail="ZIP upload exceeds the 50 MB limit")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = [item for item in archive.infolist() if not item.is_dir()]
                if len(members) > 1000 or sum(item.file_size for item in members) > max_upload_bytes:
                    raise HTTPException(status_code=413, detail="ZIP exceeds file count or expanded size limit")
                contents = []
                for item in members:
                    path = _safe_path(item.filename)
                    if language_for_path(path) is None:
                        continue
                    try:
                        contents.append((path, archive.read(item).decode("utf-8")))
                    except UnicodeDecodeError as exc:
                        raise HTTPException(status_code=422, detail=f"Source file is not UTF-8: {path}") from exc
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=422, detail="Source is not a valid ZIP archive") from exc
        return _validated_files(contents, max_upload_bytes)
    if source.url is None:
        raise HTTPException(status_code=422, detail="Git source requires a URL")
    parsed_url = urlparse(str(source.url))
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname not in {"github.com", "gitlab.com", "bitbucket.org"}
        or parsed_url.username
        or parsed_url.password
    ):
        raise HTTPException(status_code=422, detail="Git URL must use HTTPS on a supported public host")
    try:
        with tempfile.TemporaryDirectory(prefix="docgen-git-") as temp:
            destination = os.path.join(temp, "repo")
            subprocess.run(
                ["git", "clone", "--depth", "1", "--", str(source.url), destination],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            found = []
            for root, dirs, names in os.walk(destination):
                dirs[:] = [name for name in dirs if name != ".git"]
                for name in names:
                    path = os.path.join(root, name)
                    relative = os.path.relpath(path, destination).replace(os.sep, "/")
                    if language_for_path(relative):
                        with open(path, "rb") as file:
                            raw = file.read(max_upload_bytes + 1)
                        if len(raw) > max_upload_bytes:
                            raise HTTPException(status_code=413, detail="A source file exceeds the 50 MB limit")
                        found.append((relative, raw.decode("utf-8")))
            return _validated_files(found, max_upload_bytes)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=422, detail="Git clone timed out after 60 seconds") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "Git clone failed")[-1000:]
        raise HTTPException(status_code=422, detail=detail) from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Git source contains a non-UTF-8 source file") from exc


def _validated_files(files: list[tuple[str, str]], max_upload_bytes: int) -> list[tuple[str, str]]:
    if len(files) > 1000:
        raise HTTPException(status_code=422, detail="A project may contain at most 1000 files")
    total_size = 0
    normalized = []
    seen = set()
    for path, content in files:
        safe_path = _safe_path(path)
        if language_for_path(safe_path) is None:
            raise HTTPException(status_code=422, detail=f"Unsupported source file extension: {safe_path}")
        if safe_path in seen:
            raise HTTPException(status_code=422, detail=f"Duplicate source file path: {safe_path}")
        seen.add(safe_path)
        total_size += len(content.encode("utf-8"))
        if total_size > max_upload_bytes:
            raise HTTPException(status_code=413, detail="Project source exceeds the 50 MB limit")
        normalized.append((safe_path, content))
    return normalized


def _safe_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise HTTPException(status_code=422, detail=f"Unsafe source file path: {path}")
    return str(candidate)


def _upsert_source_file(session: Session, project: Project, path: str, content: str) -> SourceFile:
    _validated_files([(path, content)], int(os.getenv("MAX_UPLOAD_BYTES", "52428800")))
    source_file = session.scalar(
        select(SourceFile).where(SourceFile.project_id == project.id, SourceFile.path == path)
    )
    if source_file is None:
        source_file = SourceFile(
            path=path,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            language=language_for_path(path) or "unknown",
            content=content,
        )
        project.files.append(source_file)
    elif source_file.content != content:
        source_file.content = content
        source_file.content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        source_file.functions.clear()
        source_file.tree = {}
    return source_file


def _parse_one(path: str, content: str) -> ParsedFile:
    try:
        return parse_source(path, content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{path}: {exc}") from exc


def _project_read(project: Project, file_count: int | None = None) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        name=project.name,
        language=project.language,
        created_at=project.created_at.isoformat(),
        file_count=file_count if file_count is not None else len(project.files),
    )


def _function_read(function: FunctionEntity) -> FunctionRead:
    return FunctionRead(
        id=function.id,
        name=function.name,
        class_name=function.class_name,
        params=function.params,
        line_start=function.line_start,
        line_end=function.line_end,
        complexity=function.complexity,
        documented=bool(function.docblock_raw or (function.drafts and function.drafts[-1].generated_text)),
        docblock=function.docblock_raw,
    )


def _export_read(task: ExportTask) -> ExportRead:
    return ExportRead(
        id=task.id,
        project_id=task.project_id,
        format=ExportFormat(task.format),
        status=task.status,
        file_name=task.file_name,
        download_url=f"/exports/{task.id}/download",
    )


def _content_type(file_name: str) -> str:
    return {
        ".md": "text/markdown; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".pdf": "application/pdf",
        ".puml": "text/plain; charset=utf-8",
        ".svg": "image/svg+xml",
    }.get(os.path.splitext(file_name)[1], "application/octet-stream")


def _style_violations(functions: list[FunctionEntity]) -> list[dict]:
    violations = []
    for function in functions:
        for draft in function.drafts[-1:]:
            if any(len(line) > 100 for line in draft.generated_text.splitlines()):
                violations.append(
                    {
                        "function_id": function.id,
                        "rule": "line-length",
                        "message": "Documentation line exceeds 100 characters.",
                    }
                )
    return violations


def _ml_dashboard_html() -> str:
    page = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>DocGen digital twin</title>
<style>
body{font:16px system-ui;max-width:850px;margin:3rem auto;padding:0 1rem;color:#182230}
label{display:inline-grid;gap:.3rem;margin:.5rem}input{padding:.5rem;width:8rem}
button{padding:.7rem 1.2rem;background:#0969da;color:white;border:0;border-radius:5px}
pre{white-space:pre-wrap;background:#f6f8fa;padding:1rem}
svg{width:100%;max-width:600px;background:#f6f8fa}td,th{padding:.3rem .8rem;text-align:left}
</style>
<h1>Generation time digital twin</h1><p>Estimate documentation generation time from project metrics.</p>
<form id="prediction">
<label>Files<input name="files" type="number" value="50" min="1" max="1000" required></label>
<label>Functions<input name="funcs" type="number" value="200" min="0" required></label>
<label>Complexity<input name="complexity" type="number" value="5" min="1" max="10" step=".1" required></label>
<label>Cycles / branches<input name="cycles" type="number" value="10" min="0" required></label>
<label>Documented before (%)<input name="doc_before" type="number" value="30" min="0" max="100" step=".1" required></label>
<label>Python<input name="lang_py" type="number" value="1" min="0" max="1" required></label>
<button>Predict</button></form><pre id="result">Submit project metrics to see a prediction.</pre>
<h2>Actual vs predicted (held-out test data)</h2>
<svg viewBox="0 0 500 300" role="img" aria-label="Actual versus predicted generation time">
<line x1="45" y1="255" x2="475" y2="255" stroke="#667"/><line x1="45" y1="20" x2="45" y2="255" stroke="#667"/>
<text x="210" y="290">Actual time (seconds)</text><text transform="translate(16 190) rotate(-90)">Predicted time (seconds)</text>
<g id="dots"></g></svg>
<h2>Feature importance (model coefficients)</h2>
<table><thead><tr><th>Feature</th><th>Coefficient</th><th>Relative contribution</th></tr></thead><tbody id="importance"></tbody></table>
<h2>Model quality</h2><pre id="quality">Held-out 80/20 split. Metrics load after the first prediction.</pre>
<script>
const points=__POINTS__,dots=document.querySelector('#dots');
const max=Math.max(...points.flatMap(point=>[point.actual,point.predicted]),1);
for(const point of points){const circle=document.createElementNS('http://www.w3.org/2000/svg','circle');
 circle.setAttribute('cx',45+point.actual/max*420);circle.setAttribute('cy',255-point.predicted/max*220);
 circle.setAttribute('r','3');circle.setAttribute('fill','#0969da');circle.setAttribute('opacity','.65');dots.append(circle)}
const table=document.querySelector('#importance');
function renderImportance(values){const maxAbs=Math.max(...Object.values(values).map(Math.abs),1e-9);
 table.replaceChildren(...Object.entries(values).map(([name,value])=>{const row=document.createElement('tr');
 const feature=document.createElement('td');feature.textContent=name;const coefficient=document.createElement('td');
 coefficient.textContent=value.toFixed(4);const bar=document.createElement('td');
 const fill=document.createElement('div');fill.style.height='12px';fill.style.width=`${Math.abs(value)/maxAbs*180}px`;
 fill.style.background=value<0?'#cf222e':'#1a7f37';bar.append(fill);
 row.append(feature,coefficient,bar);return row}))}
document.querySelector('#prediction').addEventListener('submit',async event=>{
 event.preventDefault();const data=Object.fromEntries(new FormData(event.target));
 for(const key of Object.keys(data))data[key]=Number(data[key]);
 const response=await fetch('/predict',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
 const result=await response.json();document.querySelector('#result').textContent=response.ok?
 `${result.predicted_time_sec}s — ${result.recommendation}`:`Error ${response.status}: ${result.detail}`;
 if(response.ok){renderImportance(result.feature_importance);document.querySelector('#quality').textContent=
 JSON.stringify(result.model_metrics,null,2)}
});
</script></html>"""
    return page.replace("__POINTS__", json.dumps(model_plot_points()))


app = create_app()
