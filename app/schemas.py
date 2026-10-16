from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints


class SourceFileInput(BaseModel):
    path: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    content: str


class SourceInput(BaseModel):
    type: Literal["zip", "git", "files"]
    content_base64: str | None = None
    url: HttpUrl | None = None
    files: list[SourceFileInput] = Field(default_factory=list, max_length=1000)


class ProjectCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    source: SourceInput | None = None


class ProjectUpdate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    language: str | None
    created_at: str
    file_count: int


class ProjectList(BaseModel):
    items: list[ProjectRead]
    total: int
    limit: int
    offset: int


class ParseRequest(BaseModel):
    files: list[SourceFileInput] = Field(default_factory=list, max_length=1000)


class ParseResponse(BaseModel):
    project_id: str
    files_parsed: int
    classes: int
    functions: int
    average_complexity: float
    status: Literal["parsed"] = "parsed"


class FunctionRead(BaseModel):
    id: str
    name: str
    class_name: str | None
    params: list
    line_start: int
    line_end: int
    complexity: int
    documented: bool
    docblock: str | None = None


class TreeFile(BaseModel):
    id: str
    path: str
    language: str
    content_hash: str
    classes: list[dict]
    functions: list[FunctionRead]


class ProjectTree(BaseModel):
    id: str
    name: str
    files: list[TreeFile]


class GenerateRequest(BaseModel):
    style: Literal["google", "numpy", "jsdoc"] = "google"


class DraftRead(BaseModel):
    id: str
    function_id: str
    generated_text: str
    model: str


class CoverageRead(BaseModel):
    total: int
    documented: int
    percent: float
    uncovered: list[dict]
    violations: list[dict] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    project_id: str
    drafts: list[DraftRead]
    coverage: CoverageRead
    warnings: list[str] = Field(default_factory=list)


class ExportFormat(StrEnum):
    markdown = "markdown"
    html = "html"
    pdf = "pdf"
    plantuml = "plantuml"


class ExportRequest(BaseModel):
    format: ExportFormat


class ExportRead(BaseModel):
    id: str
    project_id: str
    format: ExportFormat
    status: str
    file_name: str
    download_url: str


class PredictionRequest(BaseModel):
    files: Annotated[int, Field(gt=0, le=1000)]
    funcs: Annotated[int, Field(ge=0, le=100_000)]
    complexity: Annotated[float, Field(ge=1, le=10)]
    cycles: Annotated[int, Field(ge=0, le=100_000)]
    doc_before: Annotated[float, Field(ge=0, le=100)]
    lang_py: Annotated[int, Field(ge=0, le=1)]


class PredictionResponse(BaseModel):
    predicted_time_sec: float
    recommendation: str
    confidence: float
    model_metrics: dict[str, float]
    feature_importance: dict[str, float]
