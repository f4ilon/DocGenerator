import ast
import re
from dataclasses import dataclass

SUPPORTED = {".py": "python", ".js": "javascript", ".java": "java"}


@dataclass
class ParsedFunction:
    name: str
    class_name: str | None
    params: list[str]
    line_start: int
    line_end: int
    complexity: int
    docblock_raw: str | None


@dataclass
class ParsedFile:
    language: str
    classes: list[dict]
    functions: list[ParsedFunction]
    average_complexity: float


def language_for_path(path: str) -> str | None:
    suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return SUPPORTED.get(suffix)


def parse_source(path: str, content: str) -> ParsedFile:
    language = language_for_path(path)
    if language is None:
        raise ValueError(f"Unsupported source file extension: {path}")
    if language == "python":
        return _parse_python(content)
    if language == "javascript":
        return _parse_javascript(content)
    return _parse_java(content)


def _complexity(source: str) -> int:
    return 1 + len(re.findall(r"\b(?:if|for|while|case|catch|except|elif)\b|&&|\|\|", source))


def _parse_python(source: str) -> ParsedFile:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"Python syntax error at line {exc.lineno}: {exc.msg}") from exc

    classes: list[dict] = []
    functions: list[ParsedFunction] = []

    def visit(body: list[ast.stmt], class_name: str | None = None) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                class_info = {
                    "name": node.name,
                    "line_start": node.lineno,
                    "line_end": getattr(node, "end_lineno", node.lineno),
                    "methods": [
                        child.name
                        for child in node.body
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    ],
                }
                classes.append(class_info)
                visit(node.body, node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_source = ast.get_source_segment(source, node) or ""
                docstring = ast.get_docstring(node, clean=False)
                args = [
                    arg.arg
                    for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                ]
                if node.args.vararg:
                    args.append("*" + node.args.vararg.arg)
                if node.args.kwarg:
                    args.append("**" + node.args.kwarg.arg)
                functions.append(
                    ParsedFunction(
                        node.name,
                        class_name,
                        args,
                        node.lineno,
                        getattr(node, "end_lineno", node.lineno),
                        _complexity(function_source),
                        docstring,
                    )
                )
                visit(node.body, class_name)

    visit(tree.body)
    average = sum(item.complexity for item in functions) / len(functions) if functions else 0.0
    return ParsedFile("python", classes, functions, average)


def _parse_javascript(source: str) -> ParsedFile:
    classes, class_spans = _parse_class_spans(source)
    functions = []
    patterns = (
        r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)",
        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>",
        r"^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*\{",
    )
    found: set[tuple[int, str]] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, source, re.MULTILINE):
            name, raw_params = match.group(1), match.group(2)
            line = source.count("\n", 0, match.start()) + 1
            if (line, name) in found:
                continue
            found.add((line, name))
            params = [
                value.strip().split("=")[0].strip()
                for value in raw_params.split(",")
                if value.strip()
            ]
            class_name = next(
                (item["name"] for item, start, end in class_spans if start < match.start() < end),
                None,
            )
            if class_name:
                next(item for item in classes if item["name"] == class_name)["methods"].append(name)
            functions.append(
                ParsedFunction(
                    name, class_name, params, line, line, _complexity(source[match.start() :]), None
                )
            )
    average = sum(item.complexity for item in functions) / len(functions) if functions else 0.0
    return ParsedFile("javascript", classes, functions, average)


def _parse_java(source: str) -> ParsedFile:
    classes, class_spans = _parse_class_spans(source)
    pattern = re.compile(
        r"(?:public|protected|private|static|final|synchronized|abstract|\s)+"
        r"[\w<>\[\],.?]+\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*\{",
        re.MULTILINE,
    )
    functions = []
    for match in pattern.finditer(source):
        line = source.count("\n", 0, match.start()) + 1
        params = [part.strip().split()[-1] for part in match.group(2).split(",") if part.strip()]
        class_name = next(
            (item["name"] for item, start, end in class_spans if start < match.start() < end),
            None,
        )
        if class_name:
            next(item for item in classes if item["name"] == class_name)["methods"].append(
                match.group(1)
            )
        functions.append(
            ParsedFunction(
                match.group(1),
                class_name,
                params,
                line,
                line,
                _complexity(source[match.start() :]),
                None,
            )
        )
    average = sum(item.complexity for item in functions) / len(functions) if functions else 0.0
    return ParsedFile("java", classes, functions, average)


def _parse_class_spans(source: str) -> tuple[list[dict], list[tuple[dict, int, int]]]:
    classes = []
    spans = []
    for match in re.finditer(r"\bclass\s+([A-Za-z_$][\w$]*)", source):
        opening = source.find("{", match.end())
        if opening < 0:
            continue
        depth = 0
        closing = len(source)
        for offset in range(opening, len(source)):
            if source[offset] == "{":
                depth += 1
            elif source[offset] == "}":
                depth -= 1
                if depth == 0:
                    closing = offset
                    break
        class_info = {
            "name": match.group(1),
            "line_start": source.count("\n", 0, match.start()) + 1,
            "line_end": source.count("\n", 0, closing) + 1,
            "methods": [],
        }
        classes.append(class_info)
        spans.append((class_info, opening, closing))
    return classes, spans
