#!/usr/bin/env python3
"""Extract locator-preserving evidence and conservative fact candidates."""

from __future__ import annotations

import argparse
import csv
import importlib
import io
import json
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from project_facts_common import (
    json_scalar,
    normalize_space,
    normalize_token,
    read_json,
    sha256_file,
    stable_id,
    truncate,
    utc_now,
    write_json_idempotent,
)


TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1")
METRIC_PATTERN = re.compile(
    r"(?P<label>mAP\s*@\s*0\.5\s*:\s*0\.95|mAP\s*@\s*0\.5|mAP\s*50\s*[-:]\s*95|mAP\s*50|"
    r"precision|recall|accuracy|F1(?:[-\s]?score)?|FPS|FLOPs?|Params?|参数量|帧率|准确率|精确率|召回率|"
    r"性能提升|performance\s+improvement)"
    r"(?:\s*[（(][^）)]{0,80}[）)])?\s*(?:=|:|：|为|达到|is|was|提升(?:了)?)?\s*"
    r"(?P<value>[+-]?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent|percentage\s*points?|pp|ms|s|fps|gflops?|mflops?|k|m|b|mb|gb)?",
    re.IGNORECASE,
)
NAME_PATTERN = re.compile(
    r"(?P<label>项目名称|project\s+name|模型名称|model\s+name|模块名称|module\s+name)"
    r"\s*(?P<qual>[（(][^）)]{1,40}[）)])?\s*[:：=]\s*(?P<value>[^|；;，,]{1,120})",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(
    r"(?:项目时间|实验日期|experiment\s+date|project\s+date)\s*[:：=]\s*"
    r"(?P<value>\d{4}(?:[-/.年]\d{1,2})?(?:[-/.月]\d{1,2}日?)?(?:\s*(?:至|to|[-–—])\s*\d{4}[^；;,]*)?)",
    re.IGNORECASE,
)
VERSION_PATTERN = re.compile(
    r"(?:模型版本|项目版本|model\s+version|project\s+version|version)\s*[:：=]\s*(?P<value>v?\d+(?:\.\d+){0,3}(?:[-+][\w.]+)?)",
    re.IGNORECASE,
)
_MISSING = object()


def read_text_best_effort(path: Path) -> tuple[str, str, list[str]]:
    raw = path.read_bytes()
    warnings: list[str] = []
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding), encoding, warnings
        except UnicodeDecodeError:
            continue
    warnings.append("Text decoding used UTF-8 replacement characters.")
    return raw.decode("utf-8", errors="replace"), "utf-8-replace", warnings


def block(locator: str, text: Any, kind: str, structured_value: Any = _MISSING) -> dict[str, Any]:
    item = {
        "block_id": stable_id("block", locator, normalize_space(text)),
        "locator": locator,
        "text": truncate(text, 4000),
        "evidence_kind": kind,
    }
    if structured_value is not _MISSING and json_scalar(structured_value):
        item["structured_value"] = structured_value
    return item


def extract_text(path: Path, source_type: str) -> tuple[list[dict[str, Any]], list[str], Any]:
    text, encoding, warnings = read_text_best_effort(path)
    blocks: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if source_type == "code":
            kind = "source_code"
        elif source_type == "config":
            kind = "config_value"
        elif source_type == "markdown" and stripped.startswith("|") and stripped.count("|") >= 2:
            kind = "result_table"
        else:
            kind = "narrative"
        blocks.append(block(f"line {line_number}", stripped, kind))
    warnings.append(f"Decoded as {encoding}.")
    return blocks, warnings, None


def extract_csv(path: Path) -> tuple[list[dict[str, Any]], list[str], Any]:
    text, encoding, warnings = read_text_best_effort(path)
    dialect = csv.excel_tab if path.suffix.casefold() == ".tsv" else csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    blocks: list[dict[str, Any]] = []
    for row_number, row in enumerate(reader, start=2):
        for column, value in row.items():
            if column is None or value is None or not str(value).strip():
                continue
            excerpt = f"{column} = {value}"
            kind = "result_table" if re.search(r"map|metric|score|指标|结果|value", column, re.I) else "structured_data"
            blocks.append(block(f'row {row_number}, column "{column}"', excerpt, kind, value))
    warnings.append(f"Decoded as {encoding}.")
    return blocks, warnings, None


def json_path(parent: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{parent}[{key}]"
    value = str(key)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", value):
        return f"{parent}.{value}"
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"{parent}['{escaped}']"


def walk_json(value: Any, locator: str = "$") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk_json(child, json_path(locator, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_json(child, json_path(locator, index))
    elif json_scalar(value):
        key = locator.rsplit(".", 1)[-1]
        yield locator, f"{key} = {value}", value


def extract_json(path: Path) -> tuple[list[dict[str, Any]], list[str], Any]:
    data = read_json(path)
    blocks = []
    for locator, excerpt, value in walk_json(data):
        kind = "experiment_result" if re.search(r"map|metric|score|result|指标|结果", locator, re.I) else "structured_data"
        blocks.append(block(locator, excerpt, kind, value))
    return blocks, [], data


def simple_yaml_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []
    list_counters: dict[tuple[int, str], int] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stripped.startswith("- "):
            parent = "$" + "".join(f".{part}" for _, part in stack)
            counter_key = (indent, parent)
            index = list_counters.get(counter_key, 0)
            list_counters[counter_key] = index + 1
            content = stripped[2:].strip()
            locator = f"{parent}[{index}]"
            if ":" in content:
                key, value = content.split(":", 1)
                locator = json_path(locator, key.strip())
                if value.strip():
                    blocks.append(block(locator, content, "structured_data", parse_scalar(value.strip())))
                stack.append((indent, key.strip()))
            elif content:
                blocks.append(block(locator, content, "structured_data", parse_scalar(content)))
            continue
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            parent = "$" + "".join(f".{part}" for _, part in stack)
            locator = json_path(parent, key)
            if value.strip():
                blocks.append(block(locator, stripped, "structured_data", parse_scalar(value.strip())))
            else:
                stack.append((indent, key))
        else:
            blocks.append(block(f"line {line_number}", stripped, "structured_data"))
    return blocks


def parse_scalar(value: str) -> Any:
    text = value.strip().strip('"').strip("'")
    if text.casefold() in {"true", "yes"}:
        return True
    if text.casefold() in {"false", "no"}:
        return False
    if text.casefold() in {"null", "none", "~"}:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def extract_yaml(path: Path) -> tuple[list[dict[str, Any]], list[str], Any]:
    text, encoding, warnings = read_text_best_effort(path)
    data = None
    try:
        yaml_module = importlib.import_module("yaml")
        data = yaml_module.safe_load(text)
    except ModuleNotFoundError:
        warnings.append("PyYAML is not installed; used the built-in locator-preserving YAML fallback.")
    except Exception as exc:  # parsing errors must not abort extraction of other sources
        warnings.append(f"PyYAML could not parse this file; used line fallback: {exc}")
    warnings.append(f"Decoded as {encoding}.")
    return simple_yaml_blocks(text), warnings, data


def paragraph_text(element: ET.Element, namespace: dict[str, str]) -> str:
    return "".join(node.text or "" for node in element.findall(".//w:t", namespace)).strip()


def excel_column(index: int) -> str:
    result = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def extract_docx(path: Path) -> tuple[list[dict[str, Any]], list[str], Any]:
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    blocks: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        body = root.find("w:body", namespace)
        if body is None:
            return [], ["DOCX has no document body."], None
        paragraph_number = 0
        table_number = 0
        current_heading: str | None = None
        for child in list(body):
            if child.tag.endswith("}p"):
                paragraph_number += 1
                text = paragraph_text(child, namespace)
                if not text:
                    continue
                style_node = child.find("./w:pPr/w:pStyle", namespace)
                style = ""
                if style_node is not None:
                    style = style_node.attrib.get(f"{{{namespace['w']}}}val", "")
                if style.casefold().startswith("heading") or style.startswith("标题"):
                    current_heading = text
                locator = f"paragraph {paragraph_number}"
                if current_heading:
                    locator = f'heading "{truncate(current_heading, 80)}", {locator}'
                blocks.append(block(locator, text, "narrative"))
            elif child.tag.endswith("}tbl"):
                table_number += 1
                for row_number, row in enumerate(child.findall("./w:tr", namespace), start=1):
                    for column_number, cell in enumerate(row.findall("./w:tc", namespace), start=1):
                        text = " ".join(
                            value for value in (paragraph_text(p, namespace) for p in cell.findall(".//w:p", namespace)) if value
                        )
                        if text:
                            locator = f"table {table_number}, row {row_number}, cell {excel_column(column_number)}{row_number}"
                            blocks.append(block(locator, text, "result_table"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        warnings.append(f"DOCX extraction failed without modifying the source: {exc}")
    return blocks, warnings, None


def numbered_paths(names: Iterable[str], pattern: str) -> list[str]:
    expression = re.compile(pattern)
    matches: list[tuple[int, str]] = []
    for name in names:
        match = expression.fullmatch(name)
        if match:
            matches.append((int(match.group(1)), name))
    return [name for _, name in sorted(matches)]


def extract_pptx(path: Path) -> tuple[list[dict[str, Any]], list[str], Any]:
    namespace = {
        "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    }
    blocks: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            slides = numbered_paths(archive.namelist(), r"ppt/slides/slide(\d+)\.xml")
            for slide_number, slide_path in enumerate(slides, start=1):
                root = ET.fromstring(archive.read(slide_path))
                for box_number, shape in enumerate(root.findall(".//p:sp", namespace), start=1):
                    text = " ".join(
                        node.text.strip() for node in shape.findall(".//a:t", namespace) if node.text and node.text.strip()
                    )
                    if text:
                        blocks.append(block(f"slide {slide_number}, text box {box_number}", text, "presentation_claim"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        warnings.append(f"PPTX extraction failed without modifying the source: {exc}")
    return blocks, warnings, None


def shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.itertext()).strip() for node in root]


def xlsx_sheet_paths(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    main_ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
    rel_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {node.attrib["Id"]: node.attrib["Target"] for node in relationships.findall("r:Relationship", rel_ns)}
    result = []
    for sheet in workbook.findall(".//x:sheet", main_ns):
        relationship_id = sheet.attrib.get(rel_attr)
        target = targets.get(relationship_id or "")
        if not target:
            continue
        normalized = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        result.append((sheet.attrib.get("name", "Sheet"), normalized))
    return result


def extract_xlsx(path: Path) -> tuple[list[dict[str, Any]], list[str], Any]:
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    blocks: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            strings = shared_strings(archive)
            for sheet_name, sheet_path in xlsx_sheet_paths(archive):
                root = ET.fromstring(archive.read(sheet_path))
                for cell in root.findall(".//x:c", namespace):
                    reference = cell.attrib.get("r", "?")
                    cell_type = cell.attrib.get("t", "")
                    value_node = cell.find("x:v", namespace)
                    inline_node = cell.find("x:is", namespace)
                    if cell_type == "inlineStr" and inline_node is not None:
                        value: Any = "".join(inline_node.itertext()).strip()
                    elif value_node is None or value_node.text is None:
                        continue
                    elif cell_type == "s":
                        index = int(value_node.text)
                        value = strings[index] if 0 <= index < len(strings) else value_node.text
                    elif cell_type == "b":
                        value = value_node.text == "1"
                    else:
                        value = parse_scalar(value_node.text)
                    formula = cell.find("x:f", namespace)
                    excerpt = f"{reference} = {value}"
                    if formula is not None and formula.text:
                        excerpt += f" (formula: {formula.text})"
                    blocks.append(block(f'sheet "{sheet_name}", cell {reference}', excerpt, "result_table", value))
    except (OSError, KeyError, ValueError, IndexError, zipfile.BadZipFile, ET.ParseError) as exc:
        warnings.append(f"XLSX extraction failed without modifying the source: {exc}")
    return blocks, warnings, None


def extract_pdf(path: Path) -> tuple[list[dict[str, Any]], list[str], Any]:
    blocks: list[dict[str, Any]] = []
    warnings: list[str] = []
    reader_class = None
    reader_name = ""
    for module_name in ("pypdf", "PyPDF2"):
        try:
            module = importlib.import_module(module_name)
            reader_class = module.PdfReader
            reader_name = module_name
            break
        except ModuleNotFoundError:
            continue
    if reader_class is not None:
        try:
            reader = reader_class(str(path))
            for page_number, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    blocks.append(block(f"page {page_number}", text, "narrative"))
            warnings.append(f"PDF extracted with optional dependency {reader_name}.")
            return blocks, warnings, None
        except Exception as exc:
            warnings.append(f"{reader_name} could not extract this PDF: {exc}")
            return blocks, warnings, None
    try:
        pdfplumber = importlib.import_module("pdfplumber")
        with pdfplumber.open(path) as document:
            for page_number, page in enumerate(document.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    blocks.append(block(f"page {page_number}", text, "narrative"))
        warnings.append("PDF extracted with optional dependency pdfplumber.")
    except ModuleNotFoundError:
        warnings.append("No PDF text dependency found (pypdf, PyPDF2, or pdfplumber); metadata and hash were retained.")
    except Exception as exc:
        warnings.append(f"pdfplumber could not extract this PDF: {exc}")
    return blocks, warnings, None


def extract_document(path: Path, source_type: str) -> tuple[list[dict[str, Any]], list[str], Any]:
    if source_type in {"markdown", "text", "code", "config"}:
        return extract_text(path, source_type)
    if source_type == "csv":
        return extract_csv(path)
    if source_type == "json":
        return extract_json(path)
    if source_type == "yaml":
        return extract_yaml(path)
    if source_type == "docx":
        return extract_docx(path)
    if source_type == "pptx":
        return extract_pptx(path)
    if source_type == "xlsx":
        return extract_xlsx(path)
    if source_type == "pdf":
        return extract_pdf(path)
    return [], [f"No extractor is available for source type {source_type}."], None


def metric_key(label: str) -> tuple[str, str]:
    token = normalize_token(label).replace(" ", "")
    if "0.5:0.95" in token or "50-95" in token or "50:95" in token:
        return "map_0_5_0_95", "mAP@0.5:0.95"
    if token.startswith("map"):
        return "map_0_5", "mAP@0.5"
    mapping = {
        "precision": ("precision", "precision"),
        "精确率": ("precision", "precision"),
        "recall": ("recall", "recall"),
        "召回率": ("recall", "recall"),
        "accuracy": ("accuracy", "accuracy"),
        "准确率": ("accuracy", "accuracy"),
        "f1-score": ("f1_score", "F1-score"),
        "f1score": ("f1_score", "F1-score"),
        "fps": ("fps", "FPS"),
        "帧率": ("fps", "FPS"),
        "flops": ("flops", "FLOPs"),
        "flop": ("flops", "FLOPs"),
        "params": ("parameters", "parameters"),
        "参数量": ("parameters", "parameters"),
        "性能提升": ("performance_improvement", "performance improvement"),
        "performance-improvement": ("performance_improvement", "performance improvement"),
    }
    return mapping.get(token, (re.sub(r"[^a-z0-9]+", "_", token).strip("_") or "metric", label))


def infer_scope(text: str, metric_definition: str | None = None) -> dict[str, Any]:
    scope: dict[str, Any] = {}
    modality_match = re.search(r"\b(RGB|IR|EL|infrared|visible)\b|红外|可见光|电致发光", text, re.I)
    if modality_match:
        raw = modality_match.group(0)
        modality_map = {"infrared": "IR", "红外": "IR", "visible": "RGB", "可见光": "RGB", "电致发光": "EL"}
        scope["modality"] = modality_map.get(raw.casefold(), raw.upper())
    dataset_match = re.search(r"(?:dataset|数据集)\s*[:：=为]\s*([\w.\-\u4e00-\u9fff]{2,80})", text, re.I)
    if dataset_match:
        scope["dataset"] = dataset_match.group(1).rstrip("；;,，。")
    split_match = re.search(r"(?:data\s*split|数据划分|split)\s*[:：=]\s*([^；;,，。]{2,100})", text, re.I)
    if split_match:
        scope["data_split"] = split_match.group(1).strip()
    version_match = re.search(r"(?:model\s*version|模型版本|version|版本)\s*[:：=]?\s*(v?\d+(?:\.\d+){0,3})", text, re.I)
    if version_match:
        scope["model_version"] = version_match.group(1)
    iou_match = re.search(r"IoU\s*(?:threshold|阈值)?\s*[:：=@]?\s*(0?\.\d+|\d+%)", text, re.I)
    if iou_match:
        scope["iou_threshold"] = iou_match.group(1)
    confidence_match = re.search(r"(?:confidence|置信度)\s*(?:threshold|阈值)?\s*[:：=]?\s*(0?\.\d+|\d+%)", text, re.I)
    if confidence_match:
        scope["confidence_threshold"] = confidence_match.group(1)
    date_match = re.search(r"\b(20\d{2}[-/.]\d{1,2}(?:[-/.]\d{1,2})?)\b", text)
    if date_match:
        scope["experiment_date"] = date_match.group(1)
    if metric_definition:
        scope["metric_definition"] = metric_definition
    return scope


def infer_value_type(value: Any, unit: str | None = None) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "percentage" if unit and unit.casefold() in {"%", "percent", "pp"} else "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    text = normalize_space(value)
    if unit and unit.casefold() in {"%", "percent", "percentage points", "pp"}:
        return "percentage"
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?%", text):
        return "percentage"
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return "number"
    if re.fullmatch(r"v?\d+(?:\.\d+){1,3}(?:[-+][\w.]+)?", text, re.I):
        return "version"
    if re.fullmatch(r"\d{4}(?:[-/.]\d{1,2})?(?:[-/.]\d{1,2})?", text):
        return "date"
    return "string"


def category_for_key(key: str) -> str:
    if key in {"project_name", "project_positioning"}:
        return "project_identity"
    if key in {"project_date", "experiment_date", "project_version", "model_version"}:
        return "timeline_version"
    if key in {"dataset", "dataset_size"}:
        return "dataset"
    if key in {"data_split"}:
        return "split"
    if key in {"model_name", "module_name", "method_name", "baseline_name"}:
        return "model_method"
    if key in {"personal_contribution", "team_contribution"}:
        return "contribution"
    if key in {"deployment_status", "deployment_readiness"}:
        return "deployment"
    if key in {"open_source_status"}:
        return "openness"
    if key in {"real_test_status"}:
        return "testing"
    if key in {"limitation"}:
        return "limitation"
    if key in {"completion_boundary"}:
        return "completion_boundary"
    if key in {"paper_status", "patent_status", "award_status"}:
        return "outcome_status"
    if key in {"map_0_5", "map_0_5_0_95", "precision", "recall", "accuracy", "f1_score", "fps", "flops", "parameters", "performance_improvement"}:
        return "metric"
    return "other"


def evidence_for(source: dict[str, Any], item: dict[str, Any], value: Any, kind: str | None = None) -> dict[str, Any]:
    evidence_kind = kind or item["evidence_kind"]
    evidence_id = stable_id(
        "evidence", source["source_hash"], item["locator"], normalize_space(value), evidence_kind
    )
    return {
        "evidence_id": evidence_id,
        "source_path": source["source_path"],
        "source_type": source["source_type"],
        "source_hash": source["source_hash"],
        "locator": item["locator"],
        "excerpt": truncate(item["text"], 500),
        "extracted_value": value,
        "modified_at": source["modified_at"],
        "evidence_kind": evidence_kind,
    }


def make_candidate(
    source: dict[str, Any],
    item: dict[str, Any],
    *,
    category: str,
    key: str,
    value: Any,
    value_type: str | None = None,
    unit: str | None = None,
    scope: dict[str, Any] | None = None,
    aliases: list[str] | None = None,
    lifecycle: str = "unspecified",
    subtype: str | None = None,
    source_authority: str | None = None,
    requires_support: bool = False,
    evidence_kind: str | None = None,
    ownership_level: str | None = None,
) -> dict[str, Any]:
    evidence = evidence_for(source, item, value, evidence_kind)
    authority = source_authority or {
        "experiment_result": "raw",
        "config_value": "primary",
        "source_code": "primary",
        "result_table": "formal",
        "structured_data": "formal",
        "presentation_claim": "summary",
        "claim": "claim",
    }.get(evidence["evidence_kind"], "summary")
    candidate = {
        "candidate_id": stable_id(
            "candidate", category, key, value, scope or {}, source["source_hash"], item["locator"]
        ),
        "category": category,
        "key": key,
        "value": value,
        "value_type": value_type or infer_value_type(value, unit),
        "unit": unit,
        "scope": scope or {},
        "aliases": sorted(set(aliases or []), key=str.casefold),
        "lifecycle": lifecycle,
        "subtype": subtype,
        "source_authority": authority,
        "requires_support": bool(requires_support),
        "evidence": evidence,
    }
    if ownership_level:
        candidate["ownership_level"] = ownership_level
    return candidate


def explicit_candidates(data: Any, source: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("facts"), list):
        return []
    candidates = []
    for index, fact in enumerate(data["facts"]):
        if not isinstance(fact, dict) or "key" not in fact or ("value" not in fact and "canonical_value" not in fact):
            continue
        value = fact.get("value", fact.get("canonical_value"))
        locator = f"$.facts[{index}]"
        item = block(locator, json.dumps(fact, ensure_ascii=False, sort_keys=True), fact.get("evidence_kind", "structured_data"))
        key = normalize_token(fact["key"]).replace("-", "_")
        candidates.append(
            make_candidate(
                source,
                item,
                category=fact.get("category") or category_for_key(key),
                key=key,
                value=value,
                value_type=fact.get("value_type"),
                unit=fact.get("unit"),
                scope=fact.get("scope") if isinstance(fact.get("scope"), dict) else {},
                aliases=[str(value) for value in fact.get("aliases", [])],
                lifecycle=fact.get("lifecycle", "unspecified"),
                subtype=fact.get("subtype"),
                source_authority=fact.get("source_authority"),
                requires_support=bool(fact.get("requires_support", False)),
                evidence_kind=fact.get("evidence_kind"),
                ownership_level=fact.get("ownership_level"),
            )
        )
    return candidates


def candidates_from_block(source: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    text = item["text"]
    candidates: list[dict[str, Any]] = []
    for match in METRIC_PATTERN.finditer(text):
        label = match.group("label")
        key, definition = metric_key(label)
        raw_value = match.group("value")
        unit = match.group("unit")
        value: Any = float(raw_value) if "." in raw_value else int(raw_value)
        evidence_kind = item["evidence_kind"]
        requires_support = key == "performance_improvement"
        if requires_support and evidence_kind not in {"result_table", "experiment_result", "config_value"}:
            evidence_kind = "claim"
        candidates.append(
            make_candidate(
                source,
                item,
                category="metric",
                key=key,
                value=value,
                value_type="percentage" if unit and unit.casefold() in {"%", "percent", "pp"} else "number",
                unit=unit,
                scope=infer_scope(text, definition),
                requires_support=requires_support,
                evidence_kind=evidence_kind,
            )
        )

    for match in NAME_PATTERN.finditer(text):
        label = normalize_token(match.group("label"))
        value = match.group("value").strip()
        qualifier = normalize_token(match.group("qual") or "")
        if "项目" in label or label == "project-name":
            key, category = "project_name", "project_identity"
        elif "模块" in label or label == "module-name":
            key, category = "module_name", "model_method"
        else:
            key, category = "model_name", "model_method"
        lifecycle = "unspecified"
        if any(token in qualifier for token in ("旧", "old", "deprecated", "outdated")):
            lifecycle = "old"
        elif any(token in qualifier for token in ("新", "当前", "已确认", "current", "confirmed", "new")):
            lifecycle = "current"
        candidates.append(
            make_candidate(source, item, category=category, key=key, value=value, lifecycle=lifecycle, scope=infer_scope(text))
        )

    date_match = DATE_PATTERN.search(text)
    if date_match:
        candidates.append(
            make_candidate(
                source,
                item,
                category="timeline_version",
                key="project_date" if re.search(r"项目|project", date_match.group(0), re.I) else "experiment_date",
                value=date_match.group("value"),
                value_type="date",
                scope=infer_scope(text),
            )
        )
    version_match = VERSION_PATTERN.search(text)
    if version_match:
        candidates.append(
            make_candidate(
                source,
                item,
                category="timeline_version",
                key="model_version" if re.search(r"模型|model", version_match.group(0), re.I) else "project_version",
                value=version_match.group("value"),
                value_type="version",
                scope=infer_scope(text),
            )
        )

    ownership_signal = re.search(r"独立.{0,20}(?:全部|所有|全流程)|single[- ]handed|solely", text, re.I)
    partial_signal = re.search(r"负责.{0,80}(?:整理|标注|部分|写作)|contribut(?:ed|ion).{0,80}(?:partial|annotation|writing)", text, re.I)
    if ownership_signal or partial_signal:
        level = "sole" if ownership_signal else "partial"
        ownership_value = item.get("structured_value")
        if ownership_value is None:
            ownership_value = text
        candidates.append(
            make_candidate(
                source,
                item,
                category="contribution",
                key="personal_contribution",
                value=truncate(ownership_value, 300),
                subtype="OWNERSHIP",
                ownership_level=level,
                evidence_kind="contribution_statement",
            )
        )

    status_patterns = [
        (r"已部署|部署上线|\bdeployed\b", "deployment", "deployment_status"),
        (r"达到部署要求|满足部署要求|可部署|deployment[- ]ready|meets?\s+deployment\s+requirements?", "deployment", "deployment_readiness"),
        (r"已开源|\bopen[- ]source(?:d)?\b", "openness", "open_source_status"),
        (r"真实(?:场景)?测试|实地测试|real[- ]world\s+test", "testing", "real_test_status"),
    ]
    for expression, category, key in status_patterns:
        if re.search(expression, text, re.I):
            kind = item["evidence_kind"]
            proof = kind in {"experiment_result", "config_value", "source_code", "result_table"}
            candidates.append(
                make_candidate(
                    source,
                    item,
                    category=category,
                    key=key,
                    value=True,
                    value_type="boolean",
                    requires_support=True,
                    evidence_kind=kind if proof else "claim",
                )
            )

    if re.search(r"(?:局限|限制|limitation)", text, re.I):
        candidates.append(make_candidate(source, item, category="limitation", key="limitation", value=truncate(text, 300)))
    if re.search(r"(?:仅完成|尚未|当前边界|完成边界|prototype\s+only)", text, re.I):
        candidates.append(
            make_candidate(source, item, category="completion_boundary", key="completion_boundary", value=truncate(text, 300))
        )
    return candidates


def deduplicate_candidates(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for candidate in candidates:
        evidence = candidate["evidence"]
        signature = (
            candidate["category"],
            candidate["key"],
            json.dumps(candidate["value"], ensure_ascii=False, sort_keys=True),
            json.dumps(candidate["scope"], ensure_ascii=False, sort_keys=True),
            evidence["source_hash"],
            evidence["locator"],
        )
        if signature in seen:
            continue
        seen.add(signature)
        result.append(candidate)
    result.sort(
        key=lambda candidate: (
            candidate["category"],
            candidate["key"],
            candidate["evidence"]["source_path"].casefold(),
            candidate["evidence"]["locator"],
            candidate["candidate_id"],
        )
    )
    return result


def extract_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    documents = []
    candidates: list[dict[str, Any]] = []
    for source in manifest.get("sources", []):
        path = Path(source["absolute_path"])
        before = sha256_file(path)
        if before != source["source_hash"]:
            raise RuntimeError(f"Source changed after scanning: {source['source_path']}")
        blocks, warnings, structured = extract_document(path, source["source_type"])
        source_candidates = explicit_candidates(structured, source)
        has_explicit_fact_list = bool(source_candidates)
        for item in blocks:
            if has_explicit_fact_list and str(item.get("locator", "")).startswith("$.facts["):
                continue
            source_candidates.extend(candidates_from_block(source, item))
        after = sha256_file(path)
        if before != after:
            raise RuntimeError(f"Read-only integrity failure: {source['source_path']} changed during extraction")
        candidates.extend(source_candidates)
        documents.append(
            {
                "source_path": source["source_path"],
                "source_type": source["source_type"],
                "source_hash": source["source_hash"],
                "modified_at": source["modified_at"],
                "size_bytes": source["size_bytes"],
                "block_count": len(blocks),
                "candidate_count": len(deduplicate_candidates(source_candidates)),
                "warnings": warnings,
                "blocks": blocks,
            }
        )
    final_candidates = deduplicate_candidates(candidates)
    return {
        "evidence_version": "1.0",
        "evidence_id": stable_id("evidence-set", manifest.get("manifest_id"), [c["candidate_id"] for c in final_candidates]),
        "generated_at": utc_now(),
        "manifest_id": manifest.get("manifest_id"),
        "root": manifest.get("root"),
        "materials": [
            {
                key: document[key]
                for key in ("source_path", "source_type", "source_hash", "modified_at", "size_bytes", "warnings")
            }
            for document in documents
        ],
        "documents": documents,
        "candidates": final_candidates,
        "warnings": [
            f"{document['source_path']}: {warning}"
            for document in documents
            for warning in document.get("warnings", [])
            if "Decoded as" not in warning
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract precise, read-only evidence from a source manifest.")
    parser.add_argument("--manifest", required=True, help="Path created by scan_sources.py.")
    parser.add_argument("--output", required=True, help="Output evidence.json path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = read_json(Path(args.manifest))
    if len(manifest.get("sources", [])) < 2:
        print("ERROR: evidence extraction for this Skill requires at least two sources.")
        return 2
    try:
        payload = extract_manifest(manifest)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    changed, final_payload = write_json_idempotent(Path(args.output), payload)
    action = "Wrote" if changed else "Unchanged"
    print(f"{action}: {Path(args.output).resolve()} ({len(final_payload['candidates'])} candidates)")
    for warning in final_payload.get("warnings", []):
        print(f"WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
