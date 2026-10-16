from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    files: Mapped[list["SourceFile"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class SourceFile(Base):
    __tablename__ = "source_files"
    __table_args__ = (Index("ix_source_files_project_path", "project_id", "path", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tree: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    project: Mapped[Project] = relationship(back_populates="files")
    functions: Mapped[list["FunctionEntity"]] = relationship(
        back_populates="source_file", cascade="all, delete-orphan"
    )


class FunctionEntity(Base):
    __tablename__ = "functions"
    __table_args__ = (Index("ix_functions_file_name", "file_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), index=True
    )
    class_name: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    params: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    line_start: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    line_end: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    complexity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    docblock_raw: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[SourceFile] = relationship(back_populates="functions")
    drafts: Mapped[list["DocumentationDraft"]] = relationship(
        back_populates="function", cascade="all, delete-orphan"
    )


class DocumentationDraft(Base):
    __tablename__ = "documentation_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    function_id: Mapped[str] = mapped_column(
        ForeignKey("functions.id", ondelete="CASCADE"), index=True
    )
    generated_text: Mapped[str] = mapped_column(Text, nullable=False)
    ai_model: Mapped[str] = mapped_column(String(100), nullable=False, default="local-placeholder")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    function: Mapped[FunctionEntity] = relationship(back_populates="drafts")


class CoverageReport(Base):
    __tablename__ = "coverage_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    documented: Mapped[int] = mapped_column(Integer, nullable=False)
    percent: Mapped[float] = mapped_column(Float, nullable=False)
    uncovered: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExportTask(Base):
    __tablename__ = "export_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[bytes] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
