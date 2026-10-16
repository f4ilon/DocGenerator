import html
import os
import zlib
from io import BytesIO
from pathlib import Path

import httpx
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.models import Project, SourceFile


def documentation_markdown(project: Project, files: list[SourceFile]) -> str:
    lines = [f"# {project.name}", "", f"Language: {project.language or 'mixed'}", ""]
    for source_file in files:
        lines.extend([f"## `{source_file.path}`", ""])
        if source_file.tree.get("classes"):
            lines.append("### Classes")
            lines.extend(f"- `{item['name']}`" for item in source_file.tree["classes"])
            lines.append("")
        for function in source_file.functions:
            signature = ", ".join(function.params)
            heading = f"{function.class_name + '.' if function.class_name else ''}{function.name}"
            lines.extend([f"### `{heading}({signature})`", ""])
            latest_draft = function.drafts[-1].generated_text if function.drafts else None
            lines.append(function.docblock_raw or latest_draft or "_No documentation yet._")
            lines.append("")
    return "\n".join(lines)


def render_html(markdown: str) -> str:
    escaped = html.escape(markdown)
    return (
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
        "<title>DocGen export</title><body><pre>"
        + escaped
        + "</pre></body></html>"
    )


def render_pdf(markdown: str) -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    font_name = _pdf_font()
    text = pdf.beginText(48, 790)
    text.setFont(font_name, 9)
    for line in markdown.splitlines():
        for part in [line[index : index + 105] for index in range(0, len(line), 105)] or [""]:
            if text.getY() < 48:
                pdf.drawText(text)
                pdf.showPage()
                text = pdf.beginText(48, 790)
                text.setFont(font_name, 9)
            text.textLine(part)
    pdf.drawText(text)
    pdf.save()
    return output.getvalue()


def _pdf_font() -> str:
    candidates = (
        Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for font_path in candidates:
        if font_path.is_file():
            if "DocGenUnicode" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("DocGenUnicode", str(font_path)))
            return "DocGenUnicode"
    return "Helvetica"


def plantuml_code(project: Project, files: list[SourceFile]) -> str:
    lines = ["@startuml", f"title {project.name}"]
    for source_file in files:
        for item in source_file.tree.get("classes", []):
            name = item["name"]
            lines.append(f"class {name} {{")
            for method in item.get("methods", []):
                lines.append(f"  +{method}()")
            lines.append("}")
        for function in source_file.functions:
            if function.class_name:
                lines.append(f"{function.class_name} : +{function.name}()")
    lines.extend(["@enduml", ""])
    return "\n".join(lines)


_PLANTUML_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"


def _encode_plantuml(source: str) -> str:
    data = zlib.compress(source.encode("utf-8"))[2:-4]

    def encode_group(first: int, second: int, third: int) -> str:
        return "".join(
            _PLANTUML_ALPHABET[value]
            for value in (
                first >> 2,
                ((first & 0x3) << 4) | (second >> 4),
                ((second & 0xF) << 2) | (third >> 6),
                third & 0x3F,
            )
        )

    encoded = []
    for index in range(0, len(data), 3):
        block = data[index : index + 3]
        padded = block + b"\0" * (3 - len(block))
        encoded.append(encode_group(*padded))
    return "".join(encoded)


async def render_plantuml_image(source: str) -> bytes | None:
    plantuml_url = os.getenv("PLANTUML_URL")
    if not plantuml_url:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{plantuml_url.rstrip('/')}/svg/{_encode_plantuml(source)}")
        response.raise_for_status()
        if "svg" not in response.headers.get("content-type", ""):
            raise RuntimeError("PlantUML server returned a non-SVG response")
        return response.content
