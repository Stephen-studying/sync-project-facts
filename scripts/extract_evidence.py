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
    r"precision|recall|accuracy|F1(?:[-\s]?score)?|FPS|FLOPs?|Params?|å‚æ•°é‡|å¸§çŽ‡|å‡†ç¡®çŽ‡|ç²¾ç¡®çŽ‡|å¬å›žçŽ‡|"
    r"æ€§èƒ½æå‡|performance\s+improvement)"
    r"(?:\s*[ï¼ˆ(][^ï¼‰)]{0,80}[ï¼‰)])?\s*(?:=|:|ï¼š|ä¸º|è¾¾åˆ°|is|was|æå‡(?:äº†)?)?\s*"
    r"(?P<value>[+-]?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent|percentage\s*points?|pp|ms|s|fps|gflops?|mflops?|k|m|b|mb|gb)?",
    re.IGNORECASE,
)
NAME_PATTERN = re.compile(
    r"(?P<label>é¡¹ç›®åç§°|project\s+name|æ¨¡åž‹åç§°|model\s+name|æ¨¡å—åç§°|module\s+name)"
    r"\s*(?P<qual>[ï¼ˆ(][^ï¼‰)]{1,40}[ï¼‰)])?\s*[:ï¼š=]\s*(?P<value>[^|ï¼›;ï¼Œ,]{1,120})",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(
    r"(?:é¡¹ç›®æ—¶é—´|å®žéªŒæ—¥æœŸ|experiment\s+date|project\s+date)\s*[:ï¼š=]\s*"
    r"(?P<value>\d{4}(?:[-/.å¹´]\d{1,2})?(?:[-/.æœˆ]\d{1,2}æ—¥?)?(?:\s*(?:è‡³|to|[-â€“â€”])\s*\d{4}[^ï¼›;,]*)?)",
    re.IGNORECASE,
)
VERSION_PATTERN = re.compile(
    r"(?:æ¨¡åž‹ç‰ˆæœ¬|é¡¹ç›®ç‰ˆæœ¬|model\s+version|project\s+version|version)\s*[:ï¼š=]\s*(?P<value>v?\d+(?:\.\d+){0,3}(?:[-+][\w.]+)?)",
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
            kind = "result_table" if re.search(r"map|metric|score|æŒ‡æ ‡|ç»“æžœ|value", column, re.I) else "structured_data"
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
        kind = "experiment_result" if re.search(r"map|metric|score|result|æŒ‡æ ‡|ç»“æžœ", locator, re.I) else "structured_data"
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
                if style.casefold().startswith("heading") or style.startswith("æ ‡é¢˜"):
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
                    elif value_node is None orÛž:¶‰žËkºwµçH(€€€¥˜­•ä¥¸ì‰‘…Ñ…}ÍÁ±¥Ð‰ôè(€€€€€€€É•ÑÕÉ¸€‰ÍÁ±¥Ðˆ(€€€¥˜­•ä¥¸ì‰µ½‘•±}¹…µ”ˆ°€‰µ½‘Õ±•}¹…µ”ˆ°€‰µ•Ñ¡½‘}¹…µ”ˆ°€‰‰…Í•±¥¹•}¹…µ”‰ôè(€€€€€€€É•ÑÕÉ¸€‰µ½‘•±}µ•Ñ¡½ˆ(€€€¥˜­•ä¥¸ì‰Á•ÉÍ½¹…±}½¹ÑÉ¥‰ÕÑ¥½¸ˆ°€‰Ñ•…µ}½¹ÑÉ¥‰ÕÑ¥½¸‰ôè(€€€€€€€É•ÑÕÉ¸€‰½¹ÑÉ¥‰ÕÑ¥½¸ˆ(€€€¥˜­•ä¥¸ì‰‘•Á±½åµ•¹Ñ}ÍÑ…ÑÕÌˆ°€‰‘•Á±½åµ•¹Ñ}É•…‘¥¹•ÍÌ‰ôè(€€€€€€€É•ÑÕÉ¸€‰‘•Á±½åµ•¹Ðˆ(€€€¥˜­•ä¥¸ì‰½Á•¹}Í½ÕÉ•}ÍÑ…ÑÕÌ‰ôè(€€€€€€€É•ÑÕÉ¸€‰½Á•¹¹•ÍÌˆ(€€€¥˜­•ä¥¸ì‰É•…±}Ñ•ÍÑ}ÍÑ…ÑÕÌ‰ôè(€€€€€€€É•ÑÕÉ¸€‰Ñ•ÍÑ¥¹œˆ(€€€¥˜­•ä¥¸ì‰±¥µ¥Ñ…Ñ¥½¸‰ôè(€€€€€€€É•ÑÕÉ¸€‰±¥µ¥Ñ…Ñ¥½¸ˆ(€€€¥˜­•ä¥¸ì‰½µÁ±•Ñ¥½¹}‰½Õ¹‘…Éä‰ôè(€€€€€€€É•ÑÕÉ¸€‰½µÁ±•Ñ¥½¹}‰½Õ¹‘…Éäˆ(€€€¥˜­•ä¥¸ì‰Á…Á•É}ÍÑ…ÑÕÌˆ°€‰Á…Ñ•¹Ñ}ÍÑ…ÑÕÌˆ°€‰…Ý…É‘}ÍÑ…ÑÕÌ‰ôè(€€€€€€€É•ÑÕÉ¸€‰½ÕÑ½µ•}ÍÑ…ÑÕÌˆ(€€€¥˜­•ä¥¸ì‰µ…Á|Á|Ôˆ°€‰µ…Á|Á|Õ|Á|äÔˆ°€‰ÁÉ•¥Í¥½¸ˆ°€‰É•…±°ˆ°€‰…ÕÉ…äˆ°€‰˜Å}Í½É”ˆ°€‰™ÁÌˆ°€‰™±½ÁÌˆ°€‰Á…É…µ•Ñ•ÉÌˆ°€‰Á•É™½Éµ…¹•}¥µÁÉ½Ù•µ•¹Ð‰ôè(€€€€€€€É•ÑÕÉ¸€‰µ•ÑÉ¥Œˆ(€€€É•ÑÕÉ¸€‰½Ñ¡•Èˆ(()‘•˜•Ù¥‘•¹•}™½È¡Í½ÕÉ”è‘¥ÑmÍÑÈ°¹åt°¥Ñ•´è‘¥ÑmÍÑÈ°¹åt°Ù…±Õ”è¹ä°­¥¹èÍÑÈð9½¹”€ô9½¹”¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€•Ù¥‘•¹•}­¥¹€ô­¥¹½È¥Ñ•µl‰•Ù¥‘•¹•}­¥¹‰t(€€€•Ù¥‘•¹•}¥€ôÍÑ…‰±•}¥ (€€€€€€€€‰•Ù¥‘•¹”ˆ°Í½ÕÉ•l‰Í½ÕÉ•}¡…Í ‰t°¥Ñ•µl‰±½…Ñ½È‰t°¹½Éµ…±¥é•}ÍÁ…”¡Ù…±Õ”¤°•Ù¥‘•¹•}­¥¹(€€€€¤(€€€É•ÑÕÉ¸ì(€€€€€€€€‰•Ù¥‘•¹•}¥ˆè•Ù¥‘•¹•}¥°(€€€€€€€€‰Í½ÕÉ•}Á…Ñ ˆèÍ½ÕÉ•l‰Í½ÕÉ•}Á…Ñ ‰t°(€€€€€€€€‰Í½ÕÉ•}ÑåÁ”ˆèÍ½ÕÉ•l‰Í½ÕÉ•}ÑåÁ”‰t°(€€€€€€€€‰Í½ÕÉ•}¡…Í ˆèÍ½ÕÉ•l‰Í½ÕÉ•}¡…Í ‰t°(€€€€€€€€‰±½…Ñ½Èˆè¥Ñ•µl‰±½…Ñ½È‰t°(€€€€€€€€‰•á•ÉÁÐˆèÑÉÕ¹…Ñ”¡¥Ñ•µl‰Ñ•áÐ‰t°€ÔÀÀ¤°(€€€€€€€€‰•áÑÉ…Ñ•‘}Ù…±Õ”ˆèÙ…±Õ”°(€€€€€€€€‰µ½‘¥™¥•‘}…ÐˆèÍ½ÕÉ•l‰µ½‘¥™¥•‘}…Ð‰t°(€€€€€€€€‰•Ù¥‘•¹•}­¥¹ˆè•Ù¥‘•¹•}­¥¹°(€€€ô(()‘•˜µ…­•}…¹‘¥‘…Ñ” (€€€Í½ÕÉ”è‘¥ÑmÍÑÈ°¹åt°(€€€¥Ñ•´è‘¥ÑmÍÑÈ°¹åt°(€€€€¨°(€€€…Ñ•½ÉäèÍÑÈ°(€€€­•äèÍÑÈ°(€€€Ù…±Õ”è¹ä°(€€€Ù…±Õ•}ÑåÁ”èÍÑÈð9½¹”€ô9½¹”°(€€€Õ¹¥ÐèÍÑÈð9½¹”€ô9½¹”°(€€€Í½Á”è‘¥ÑmÍÑÈ°¹åtð9½¹”€ô9½¹”°(€€€…±¥…Í•Ìè±¥ÍÑmÍÑÉtð9½¹”€ô9½¹”°(€€€±¥™•å±”èÍÑÈ€ô€‰Õ¹ÍÁ•¥™¥•ˆ°(€€€ÍÕ‰ÑåÁ”èÍÑÈð9½¹”€ô9½¹”°(€€€Í½ÕÉ•}…ÕÑ¡½É¥ÑäèÍÑÈð9½¹”€ô9½¹”°(€€€É•ÅÕ¥É•Í}ÍÕÁÁ½ÉÐè‰½½°€ô…±Í”°(€€€•Ù¥‘•¹•}­¥¹èÍÑÈð9½¹”€ô9½¹”°(€€€½Ý¹•ÉÍ¡¥Á}±•Ù•°èÍÑÈð9½¹”€ô9½¹”°(¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€•Ù¥‘•¹”€ô•Ù¥‘•¹•}™½È¡Í½ÕÉ”°¥Ñ•´°Ù…±Õ”°•Ù¥‘•¹•}­¥¹¤(€€€…ÕÑ¡½É¥Ñä€ôÍ½ÕÉ•}…ÕÑ¡½É¥Ñä½Èì(€€€€€€€€‰•áÁ•É¥µ•¹Ñ}É•ÍÕ±Ðˆè€‰É…Üˆ°(€€€€€€€€‰½¹™¥}Ù…±Õ”ˆè€‰ÁÉ¥µ…Éäˆ°(€€€€€€€€‰Í½ÕÉ•}½‘”ˆè€‰ÁÉ¥µ…Éäˆ°(€€€€€€€€‰É•ÍÕ±Ñ}Ñ…‰±”ˆè€‰™½Éµ…°ˆ°(€€€€€€€€‰ÍÑÉÕÑÕÉ•‘}‘…Ñ„ˆè€‰™½Éµ…°ˆ°(€€€€€€€€‰ÁÉ•Í•¹Ñ…Ñ¥½¹}±…¥´ˆè€‰ÍÕµµ…Éäˆ°(€€€€€€€€‰±…¥´ˆè€‰±…¥´ˆ°(€€€ô¹•Ð¡•Ù¥‘•¹•l‰•Ù¥‘•¹•}­¥¹‰t°€‰ÍÕµµ…Éäˆ¤(€€€…¹‘¥‘…Ñ”€ôì(€€€€€€€€‰…¹‘¥‘…Ñ•}¥ˆèÍÑ…‰±•}¥ (€€€€€€€€€€€€‰…¹‘¥‘…Ñ”ˆ°…Ñ•½Éä°­•ä°Ù…±Õ”°Í½Á”½Èíô°Í½ÕÉ•l‰Í½ÕÉ•}¡…Í ‰t°¥Ñ•µl‰±½…Ñ½È‰t(€€€€€€€€¤°(€€€€€€€€‰…Ñ•½Éäˆè…Ñ•½Éä°(€€€€€€€€‰­•äˆè­•ä°(€€€€€€€€‰Ù…±Õ”ˆèÙ…±Õ”°(€€€€€€€€‰Ù…±Õ•}ÑåÁ”ˆèÙ…±Õ•}ÑåÁ”½È¥¹™•É}Ù…±Õ•}ÑåÁ”¡Ù…±Õ”°Õ¹¥Ð¤°(€€€€€€€€‰Õ¹¥ÐˆèÕ¹¥Ð°(€€€€€€€€‰Í½Á”ˆèÍ½Á”½Èíô°(€€€€€€€€‰…±¥…Í•ÌˆèÍ½ÉÑ•¡Í•Ð¡…±¥…Í•Ì½Èmt¤°­•äõÍÑÈ¹…Í•™½±¤°(€€€€€€€€‰±¥™•å±”ˆè±¥™•å±”°(€€€€€€€€‰ÍÕ‰ÑåÁ”ˆèÍÕ‰ÑåÁ”°(€€€€€€€€‰Í½ÕÉ•}…ÕÑ¡½É¥Ñäˆè…ÕÑ¡½É¥Ñä°(€€€€€€€€‰É•ÅÕ¥É•Í}ÍÕÁÁ½ÉÐˆè‰½½°¡É•ÅÕ¥É•Í}ÍÕÁÁ½ÉÐ¤°(€€€€€€€€‰•Ù¥‘•¹”ˆè•Ù¥‘•¹”°(€€€ô(€€€¥˜½Ý¹•ÉÍ¡¥Á}±•Ù•°è(€€€€€€€…¹‘¥‘…Ñ•l‰½Ý¹•ÉÍ¡¥Á}±•Ù•°‰t€ô½Ý¹•ÉÍ¡¥Á}±•Ù•°(€€€É•ÑÕÉ¸…¹‘¥‘…Ñ”(()‘•˜•áÁ±¥¥Ñ}…¹‘¥‘…Ñ•Ì¡‘…Ñ„è¹ä°Í½ÕÉ”è‘¥ÑmÍÑÈ°¹åt¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°¹åutè(€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡‘…Ñ„°‘¥Ð¤½È¹½Ð¥Í¥¹ÍÑ…¹”¡‘…Ñ„¹•Ð ‰™…ÑÌˆ¤°±¥ÍÐ¤è(€€€€€€€É•ÑÕÉ¸mt(€€€…¹‘¥‘…Ñ•Ì€ômt(€€€™½È¥¹‘•à°™…Ð¥¸•¹Õµ•É…Ñ”¡‘…Ñ…l‰™…ÑÌ‰t¤è(€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡™…Ð°‘¥Ð¤½È€‰­•äˆ¹½Ð¥¸™…Ð½È€ ‰Ù…±Õ”ˆ¹½Ð¥¸™…Ð…¹€‰…¹½¹¥…±}Ù…±Õ”ˆ¹½Ð¥¸™…Ð¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€Ù…±Õ”€ô™…Ð¹•Ð ‰Ù…±Õ”ˆ°™…Ð¹•Ð ‰…¹½¹¥…±}Ù…±Õ”ˆ¤¤(€€€€€€€±½…Ñ½È€ô˜ˆ¹™…ÑÍmí¥¹‘•áõtˆ(€€€€€€€¥Ñ•´€ô‰±½¬¡±½…Ñ½È°©Í½¸¹‘ÕµÁÌ¡™…Ð°•¹ÍÕÉ•}…Í¥¤õ…±Í”°Í½ÉÑ}­•åÌõQÉÕ”¤°™…Ð¹•Ð ‰•Ù¥‘•¹•}­¥¹ˆ°€‰ÍÑÉÕÑÕÉ•‘}‘…Ñ„ˆ¤¤(€€€€€€€­•ä€ô¹½Éµ…±¥é•}Ñ½­•¸¡™…Ñl‰­•ä‰t¤¹É•Á±…” ˆ´ˆ°€‰|ˆ¤(€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹ (€€€€€€€€€€€µ…­•}…¹‘¥‘…Ñ” (€€€€€€€€€€€€€€€Í½ÕÉ”°(€€€€€€€€€€€€€€€¥Ñ•´°(€€€€€€€€€€€€€€€…Ñ•½Éäõ™…Ð¹•Ð ‰…Ñ•½Éäˆ¤½È…Ñ•½Éå}™½É}­•ä¡­•ä¤°(€€€€€€€€€€€€€€€­•äõ­•ä°(€€€€€€€€€€€€€€€Ù…±Õ”õÙ…±Õ”°(€€€€€€€€€€€€€€€Ù…±Õ•}ÑåÁ”õ™…Ð¹•Ð ‰Ù…±Õ•}ÑåÁ”ˆ¤°(€€€€€€€€€€€€€€€Õ¹¥Ðõ™…Ð¹•Ð ‰Õ¹¥Ðˆ¤°(€€€€€€€€€€€€€€€Í½Á”õ™…Ð¹•Ð ‰Í½Á”ˆ¤¥˜¥Í¥¹ÍÑ…¹”¡™…Ð¹•Ð ‰Í½Á”ˆ¤°‘¥Ð¤•±Í”íô°(€€€€€€€€€€€€€€€…±¥…Í•ÌõmÍÑÈ¡Ù…±Õ”¤™½ÈÙ…±Õ”¥¸™…Ð¹•Ð ‰…±¥…Í•Ìˆ°mt¥t°(€€€€€€€€€€€€€€€±¥™•å±”õ™…Ð¹•Ð ‰±¥™•å±”ˆ°€‰Õ¹ÍÁ•¥™¥•ˆ¤°(€€€€€€€€€€€€€€€ÍÕ‰ÑåÁ”õ™…Ð¹•Ð ‰ÍÕ‰ÑåÁ”ˆ¤°(€€€€€€€€€€€€€€€Í½ÕÉ•}…ÕÑ¡½É¥Ñäõ™…Ð¹•Ð ‰Í½ÕÉ•}…ÕÑ¡½É¥Ñäˆ¤°(€€€€€€€€€€€€€€€É•ÅÕ¥É•Í}ÍÕÁÁ½ÉÐõ‰½½°¡™…Ð¹•Ð ‰É•ÅÕ¥É•Í}ÍÕÁÁ½ÉÐˆ°…±Í”¤¤°(€€€€€€€€€€€€€€€•Ù¥‘•¹•}­¥¹õ™…Ð¹•Ð ‰•Ù¥‘•¹•}­¥¹ˆ¤°(€€€€€€€€€€€€€€€½Ý¹•ÉÍ¡¥Á}±•Ù•°õ™…Ð¹•Ð ‰½Ý¹•ÉÍ¡¥Á}±•Ù•°ˆ¤°(€€€€€€€€€€€€¤(€€€€€€€€¤(€€€É•ÑÕÉ¸…¹‘¥‘…Ñ•Ì(()‘•˜…¹‘¥‘…Ñ•Í}™É½µ}‰±½¬¡Í½ÕÉ”è‘¥ÑmÍÑÈ°¹åt°¥Ñ•´è‘¥ÑmÍÑÈ°¹åt¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°¹åutè(€€€Ñ•áÐ€ô¥Ñ•µl‰Ñ•áÐ‰t(€€€…¹‘¥‘…Ñ•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€™½Èµ…Ñ ¥¸5QI%}AQQI8¹™¥¹‘¥Ñ•È¡Ñ•áÐ¤è(€€€€€€€±…‰•°€ôµ…Ñ ¹É½ÕÀ ‰±…‰•°ˆ¤(€€€€€€€­•ä°‘•™¥¹¥Ñ¥½¸€ôµ•ÑÉ¥}­•ä¡±…‰•°¤(€€€€€€€É…Ý}Ù…±Õ”€ôµ…Ñ ¹É½ÕÀ ‰Ù…±Õ”ˆ¤(€€€€€€€Õ¹¥Ð€ôµ…Ñ ¹É½ÕÀ ‰Õ¹¥Ðˆ¤(€€€€€€€Ù…±Õ”è¹ä€ô™±½…Ð¡É…Ý}Ù…±Õ”¤¥˜€ˆ¸ˆ¥¸É…Ý}Ù…±Õ”•±Í”¥¹Ð¡É…Ý}Ù…±Õ”¤(€€€€€€€•Ù¥‘•¹•}­¥¹€ô¥Ñ•µl‰•Ù¥‘•¹•}­¥¹‰t(€€€€€€€É•ÅÕ¥É•Í}ÍÕÁÁ½ÉÐ€ô­•ä€ôô€‰Á•É™½Éµ…¹•}¥µÁÉ½Ù•µ•¹Ðˆ(€€€€€€€¥˜É•ÅÕ¥É•Í}ÍÕÁÁ½ÉÐ…¹•Ù¥‘•¹•}­¥¹¹½Ð¥¸ì‰É•ÍÕ±Ñ}Ñ…‰±”ˆ°€‰•áÁ•É¥µ•¹Ñ}É•ÍÕ±Ðˆ°€‰½¹™¥}Ù…±Õ”‰ôè(€€€€€€€€€€€•Ù¥‘•¹•}­¥¹€ô€‰±…¥´ˆ(€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹ (€€€€€€€€€€€µ…­•}…¹‘¥‘…Ñ” (€€€€€€€€€€€€€€€Í½ÕÉ”°(€€€€€€€€€€€€€€€¥Ñ•´°(€€€€€€€€€€€€€€€…Ñ•½Éäô‰µ•ÑÉ¥Œˆ°(€€€€€€€€€€€€€€€­•äõ­•ä°(€€€€€€€€€€€€€€€Ù…±Õ”õÙ…±Õ”°(€€€€€€€€€€€€€€€Ù…±Õ•}ÑåÁ”ô‰Á•É•¹Ñ…”ˆ¥˜Õ¹¥Ð…¹Õ¹¥Ð¹…Í•™½± ¤¥¸ìˆ”ˆ°€‰Á•É•¹Ðˆ°€‰ÁÀ‰ô•±Í”€‰¹Õµ‰•Èˆ°(€€€€€€€€€€€€€€€Õ¹¥ÐõÕ¹¥Ð°(€€€€€€€€€€€€€€€Í½Á”õ¥¹™•É}Í½Á”¡Ñ•áÐ°‘•™¥¹¥Ñ¥½¸¤°(€€€€€€€€€€€€€€€É•ÅÕ¥É•Í}ÍÕÁÁ½ÉÐõÉ•ÅÕ¥É•Í}ÍÕÁÁ½ÉÐ°(€€€€€€€€€€€€€€€•Ù¥‘•¹•}­¥¹õ•Ù¥‘•¹•}­¥¹°(€€€€€€€€€€€€¤(€€€€€€€€¤((€€€™½Èµ…Ñ ¥¸95}AQQI8¹™¥¹‘¥Ñ•È¡Ñ•áÐ¤è(€€€€€€€±…‰•°€ô¹½Éµ…±¥é•}Ñ½­•¸¡µ…Ñ ¹É½ÕÀ ‰±…‰•°ˆ¤¤(€€€€€€€Ù…±Õ”€ôµ…Ñ ¹É½ÕÀ ‰Ù…±Õ”ˆ¤¹ÍÑÉ¥À ¤(€€€€€€€ÅÕ…±¥™¥•È€ô¹½Éµ…±¥é•}Ñ½­•¸¡µ…Ñ ¹É½ÕÀ ‰ÅÕ…°ˆ¤½È€ˆˆ¤(€€€€€€€¥˜€‹¦†çžn¸ˆ¥¸±…‰•°½È±…‰•°€ôô€‰ÁÉ½©•Ðµ¹…µ”ˆè(€€€€€€€€€€€­•ä°…Ñ•½Éä€ô€‰ÁÉ½©•Ñ}¹…µ”ˆ°€‰ÁÉ½©•Ñ}¥‘•¹Ñ¥Ñäˆ(€€€€€€€•±¥˜€‹š¢‡–v\ˆ¥¸±…‰•°½È±…‰•°€ôô€‰µ½‘Õ±”µ¹…µ”ˆè(€€€€€€€€€€€­•ä°…Ñ•½Éä€ô€‰µ½‘Õ±•}¹…µ”ˆ°€‰µ½‘•±}µ•Ñ¡½ˆ(€€€€€€€•±Í”è(€€€€€€€€€€€­•ä°…Ñ•½Éä€ô€‰µ½‘•±}¹…µ”ˆ°€‰µ½‘•±}µ•Ñ¡½ˆ(€€€€€€€±¥™•å±”€ô€‰Õ¹ÍÁ•¥™¥•ˆ(€€€€€€€¥˜…¹ä¡Ñ½­•¸¥¸ÅÕ…±¥™¥•È™½ÈÑ½­•¸¥¸€ ‹š^œˆ°€‰½±ˆ°€‰‘•ÁÉ•…Ñ•ˆ°€‰½ÕÑ‘…Ñ•ˆ¤¤è(€€€€€€€€€€€±¥™•å±”€ô€‰½±ˆ(€€€€€€€•±¥˜…¹ä¡Ñ½­•¸¥¸ÅÕ…±¥™¥•È™½ÈÑ½­•¸¥¸€ ‹šZÀˆ°€‹–öO–&4ˆ°€‹–ÞËž†»¢ºˆ°€‰ÕÉÉ•¹Ðˆ°€‰½¹™¥Éµ•ˆ°€‰¹•Üˆ¤¤è(€€€€€€€€€€€±¥™•å±”€ô€‰ÕÉÉ•¹Ðˆ(€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹ (€€€€€€€€€€€µ…­•}…¹‘¥‘…Ñ”¡Í½ÕÉ”°¥Ñ•´°…Ñ•½Éäõ…Ñ•½Éä°­•äõ­•ä°Ù…±Õ”õÙ…±Õ”°±¥™•å±”õ±¥™•å±”°Í½Á”õ¥¹™•É}Í½Á”¡Ñ•áÐ¤¤(€€€€€€€€¤((€€€‘…Ñ•}µ…Ñ €ôQ}AQQI8¹Í•…É ¡Ñ•áÐ¤(€€€¥˜‘…Ñ•}µ…Ñ è(€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹ (€€€€€€€€€€€µ…­•}…¹‘¥‘…Ñ” (€€€€€€€€€€€€€€€Í½ÕÉ”°(€€€€€€€€€€€€€€€¥Ñ•´°(€€€€€€€€€€€€€€€…Ñ•½Éäô‰Ñ¥µ•±¥¹•}Ù•ÉÍ¥½¸ˆ°(€€€€€€€€€€€€€€€­•äô‰ÁÉ½©•Ñ}‘…Ñ”ˆ¥˜É”¹Í•…É ¡È‹¦†çžn¹ñÁÉ½©•Ðˆ°‘…Ñ•}µ…Ñ ¹É½ÕÀ À¤°É”¹$¤•±Í”€‰•áÁ•É¥µ•¹Ñ}‘…Ñ”ˆ°(€€€€€€€€€€€€€€€Ù…±Õ”õ‘…Ñ•}µ…Ñ ¹É½ÕÀ ‰Ù…±Õ”ˆ¤°(€€€€€€€€€€€€€€€Ù…±Õ•}ÑåÁ”ô‰‘…Ñ”ˆ°(€€€€€€€€€€€€€€€Í½Á”õ¥¹™•É}Í½Á”¡Ñ•áÐ¤°(€€€€€€€€€€€€¤(€€€€€€€€¤(€€€Ù•ÉÍ¥½¹}µ…Ñ €ôYIM%=9}AQQI8¹Í•…É ¡Ñ•áÐ¤(€€€¥˜Ù•ÉÍ¥½¹}µ…Ñ è(€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹ (€€€€€€€€€€€µ…­•}…¹‘¥‘…Ñ” (€€€€€€€€€€€€€€€Í½ÕÉ”°(€€€€€€€€€€€€€€€¥Ñ•´°(€€€€€€€€€€€€€€€…Ñ•½Éäô‰Ñ¥µ•±¥¹•}Ù•ÉÍ¥½¸ˆ°(€€€€€€€€€€€€€€€­•äô‰µ½‘•±}Ù•ÉÍ¥½¸ˆ¥˜É”¹Í•…É ¡È‹š¢‡–z-ñµ½‘•°ˆ°Ù•ÉÍ¥½¹}µ…Ñ ¹É½ÕÀ À¤°É”¹$¤•±Í”€‰ÁÉ½©•Ñ}Ù•ÉÍ¥½¸ˆ°(€€€€€€€€€€€€€€€Ù…±Õ”õÙ•ÉÍ¥½¹}µ…Ñ ¹É½ÕÀ ‰Ù…±Õ”ˆ¤°(€€€€€€€€€€€€€€€Ù…±Õ•}ÑåÁ”ô‰Ù•ÉÍ¥½¸ˆ°(€€€€€€€€€€€€€€€Í½Á”õ¥¹™•É}Í½Á”¡Ñ•áÐ¤°(€€€€€€€€€€€€¤(€€€€€€€€¤((€€€½Ý¹•ÉÍ¡¥Á}Í¥¹…°€ôÉ”¹Í•…É ¡È‹ž.³ž®,¹ìÀ°ÈÁô üë–£¦¡óš&šr%ó–£šÖž¢,¥ñÍ¥¹±•l´u¡…¹‘•‘ñÍ½±•±äˆ°Ñ•áÐ°É”¹$¤(€€€Á…ÉÑ¥…±}Í¥¹…°€ôÉ”¹Í•…É ¡È‹¢Ò¢ÒŒ¹ìÀ°àÁô üëšVÓžBóš‚šÎ¡ó¦£–"ó–g’öp¥ñ½¹ÑÉ¥‰ÕÐ üé•‘ñ¥½¸¤¹ìÀ°àÁô üéÁ…ÉÑ¥…±ñ…¹¹½Ñ…Ñ¥½¹ñÝÉ¥Ñ¥¹œ¤ˆ°Ñ•áÐ°É”¹$¤(€€€¥˜½Ý¹•ÉÍ¡¥Á}Í¥¹…°½ÈÁ…ÉÑ¥…±}Í¥¹…°è(€€€€€€€±•Ù•°€ô€‰Í½±”ˆ¥˜½Ý¹•ÉÍ¡¥Á}Í¥¹…°•±Í”€‰Á…ÉÑ¥…°ˆ(€€€€€€€½Ý¹•ÉÍ¡¥Á}Ù…±Õ”€ô¥Ñ•´¹•Ð ‰ÍÑÉÕÑÕÉ•‘}Ù…±Õ”ˆ¤(€€€€€€€¥˜½Ý¹•ÉÍ¡¥Á}Ù…±Õ”¥Ì9½¹”è(€€€€€€€€€€€½Ý¹•ÉÍ¡¥Á}Ù…±Õ”€ôÑ•áÐ(€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹ (€€€€€€€€€€€µ…­•}…¹‘¥‘…Ñ” (€€€€€€€€€€€€€€€Í½ÕÉ”°(€€€€€€€€€€€€€€€¥Ñ•´°(€€€€€€€€€€€€€€€…Ñ•½Éäô‰½¹ÑÉ¥‰ÕÑ¥½¸ˆ°(€€€€€€€€€€€€€€€­•äô‰Á•ÉÍ½¹…±}½¹ÑÉ¥‰ÕÑ¥½¸ˆ°(€€€€€€€€€€€€€€€Ù…±Õ”õÑÉÕ¹…Ñ”¡½Ý¹•ÉÍ¡¥Á}Ù…±Õ”°€ÌÀÀ¤°(€€€€€€€€€€€€€€€ÍÕ‰ÑåÁ”ô‰=]9IM!%@ˆ°(€€€€€€€€€€€€€€€½Ý¹•ÉÍ¡¥Á}±•Ù•°õ±•Ù•°°(€€€€€€€€€€€€€€€•Ù¥‘•¹•}­¥¹ô‰½¹ÑÉ¥‰ÕÑ¥½¹}ÍÑ…Ñ•µ•¹Ðˆ°(€€€€€€€€€€€€¤(€€€€€€€€¤((€€€ÍÑ…ÑÕÍ}Á…ÑÑ•É¹Ì€ôl(€€€€€€€€¡È‹–ÞË¦£žöÉó¦£žöË’â+žêýñq‰‘•Á±½å•‘qˆˆ°€‰‘•Á±½åµ•¹Ðˆ°€‰‘•Á±½åµ•¹Ñ}ÍÑ…ÑÕÌˆ¤°(€€€€€€€€¡È‹¢úû–"Ã¦£žöË¢ššÆ	óšî‡¢ÚÏ¦£žöË¢ššÆ	ó–>¿¦£žöÉñ‘•Á±½åµ•¹Ñl´uÉ•…‘åñµ••ÑÌýqÌ­‘•Á±½åµ•¹ÑqÌ­É•ÅÕ¥É•µ•¹ÑÌüˆ°€‰‘•Á±½åµ•¹Ðˆ°€‰‘•Á±½åµ•¹Ñ}É•…‘¥¹•ÍÌˆ¤°(€€€€€€€€¡È‹–ÞË–òšêAñq‰½Á•¹l´uÍ½ÕÉ” üé¤ýqˆˆ°€‰½Á•¹¹•ÍÌˆ°€‰½Á•¹}Í½ÕÉ•}ÍÑ…ÑÕÌˆ¤°(€€€€€€€€¡È‹žr–ºx üë–rëšf¼¤ÿšÖ/¢¾Uó–º{–rÃšÖ/¢¾UñÉ•…±l´uÝ½É±‘qÌ­Ñ•ÍÐˆ°€‰Ñ•ÍÑ¥¹œˆ°€‰É•…±}Ñ•ÍÑ}ÍÑ…ÑÕÌˆ¤°(€€€t(€€€™½È•áÁÉ•ÍÍ¥½¸°…Ñ•½Éä°­•ä¥¸ÍÑ…ÑÕÍ}Á…ÑÑ•É¹Ìè(€€€€€€€¥˜É”¹Í•…É ¡•áÁÉ•ÍÍ¥½¸°Ñ•áÐ°É”¹$¤è(€€€€€€€€€€€­¥¹€ô¥Ñ•µl‰•Ù¥‘•¹•}­¥¹‰t(€€€€€€€€€€€ÁÉ½½˜€ô­¥¹¥¸ì‰•áÁ•É¥µ•¹Ñ}É•ÍÕ±Ðˆ°€‰½¹™¥}Ù…±Õ”ˆ°€‰Í½ÕÉ•}½‘”ˆ°€‰É•ÍÕ±Ñ}Ñ…‰±”‰ô(€€€€€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹ (€€€€€€€€€€€€€€€µ…­•}…¹‘¥‘…Ñ” (€€€€€€€€€€€€€€€€€€€Í½ÕÉ”°(€€€€€€€€€€€€€€€€€€€¥Ñ•´°(€€€€€€€€€€€€€€€€€€€…Ñ•½Éäõ…Ñ•½Éä°(€€€€€€€€€€€€€€€€€€€­•äõ­•ä°(€€€€€€€€€€€€€€€€€€€Ù…±Õ”õQÉÕ”°(€€€€€€€€€€€€€€€€€€€Ù…±Õ•}ÑåÁ”ô‰‰½½±•…¸ˆ°(€€€€€€€€€€€€€€€€€€€É•ÅÕ¥É•Í}ÍÕÁÁ½ÉÐõQÉÕ”°(€€€€€€€€€€€€€€€€€€€•Ù¥‘•¹•}­¥¹õ­¥¹¥˜ÁÉ½½˜•±Í”€‰±…¥´ˆ°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€¤((€€€¥˜É”¹Í•…É ¡Èˆ üë–Æ¦fAó¦fC–"Ùñ±¥µ¥Ñ…Ñ¥½¸¤ˆ°Ñ•áÐ°É”¹$¤è(€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹¡µ…­•}…¹‘¥‘…Ñ”¡Í½ÕÉ”°¥Ñ•´°…Ñ•½Éäô‰±¥µ¥Ñ…Ñ¥½¸ˆ°­•äô‰±¥µ¥Ñ…Ñ¥½¸ˆ°Ù…±Õ”õÑÉÕ¹…Ñ”¡Ñ•áÐ°€ÌÀÀ¤¤¤(€€€¥˜É”¹Í•…É ¡Èˆ üë’î–º3š"Aó–Âkšr©ó–öO–&7¢úçžV1ó–º3š"C¢úçžV1ñÁÉ½Ñ½ÑåÁ•qÌ­½¹±ä¤ˆ°Ñ•áÐ°É”¹$¤è(€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹ (€€€€€€€€€€€µ…­•}…¹‘¥‘…Ñ”¡Í½ÕÉ”°¥Ñ•´°…Ñ•½Éäô‰½µÁ±•Ñ¥½¹}‰½Õ¹‘…Éäˆ°­•äô‰½µÁ±•Ñ¥½¹}‰½Õ¹‘…Éäˆ°Ù…±Õ”õÑÉÕ¹…Ñ”¡Ñ•áÐ°€ÌÀÀ¤¤(€€€€€€€€¤(€€€É•ÑÕÉ¸…¹‘¥‘…Ñ•Ì(()‘•˜‘•‘ÕÁ±¥…Ñ•}…¹‘¥‘…Ñ•Ì¡…¹‘¥‘…Ñ•Ìè%Ñ•É…‰±•m‘¥ÑmÍÑÈ°¹åut¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°¹åutè(€€€É•ÍÕ±Ðè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€Í••¸èÍ•ÑmÑÕÁ±•m¹ä°€¸¸¹ut€ôÍ•Ð ¤(€€€™½È…¹‘¥‘…Ñ”¥¸…¹‘¥‘…Ñ•Ìè(€€€€€€€•Ù¥‘•¹”€ô…¹‘¥‘…Ñ•l‰•Ù¥‘•¹”‰t(€€€€€€€Í¥¹…ÑÕÉ”€ô€ (€€€€€€€€€€€…¹‘¥‘…Ñ•l‰…Ñ•½Éä‰t°(€€€€€€€€€€€…¹‘¥‘…Ñ•l‰­•ä‰t°(€€€€€€€€€€€©Í½¸¹‘ÕµÁÌ¡…¹‘¥‘…Ñ•l‰Ù…±Õ”‰t°•¹ÍÕÉ•}…Í¥¤õ…±Í”°Í½ÉÑ}­•åÌõQÉÕ”¤°(€€€€€€€€€€€©Í½¸¹‘ÕµÁÌ¡…¹‘¥‘…Ñ•l‰Í½Á”‰t°•¹ÍÕÉ•}…Í¥¤õ…±Í”°Í½ÉÑ}­•åÌõQÉÕ”¤°(€€€€€€€€€€€•Ù¥‘•¹•l‰Í½ÕÉ•}¡…Í ‰t°(€€€€€€€€€€€•Ù¥‘•¹•l‰±½…Ñ½È‰t°(€€€€€€€€¤(€€€€€€€¥˜Í¥¹…ÑÕÉ”¥¸Í••¸è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€Í••¸¹…‘¡Í¥¹…ÑÕÉ”¤(€€€€€€€É•ÍÕ±Ð¹…ÁÁ•¹¡…¹‘¥‘…Ñ”¤(€€€É•ÍÕ±Ð¹Í½ÉÐ (€€€€€€€­•äõ±…µ‰‘„…¹‘¥‘…Ñ”è€ (€€€€€€€€€€€…¹‘¥‘…Ñ•l‰…Ñ•½Éä‰t°(€€€€€€€€€€€…¹‘¥‘…Ñ•l‰­•ä‰t°(€€€€€€€€€€€…¹‘¥‘…Ñ•l‰•Ù¥‘•¹”‰ul‰Í½ÕÉ•}Á…Ñ ‰t¹…Í•™½± ¤°(€€€€€€€€€€€…¹‘¥‘…Ñ•l‰•Ù¥‘•¹”‰ul‰±½…Ñ½È‰t°(€€€€€€€€€€€…¹‘¥‘…Ñ•l‰…¹‘¥‘…Ñ•}¥‰t°(€€€€€€€€¤(€€€€¤(€€€É•ÑÕÉ¸É•ÍÕ±Ð(()‘•˜•áÑÉ…Ñ}µ…¹¥™•ÍÐ¡µ…¹¥™•ÍÐè‘¥ÑmÍÑÈ°¹åt¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€‘½Õµ•¹ÑÌ€ômt(€€€…¹‘¥‘…Ñ•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€™½ÈÍ½ÕÉ”¥¸µ…¹¥™•ÍÐ¹•Ð ‰Í½ÕÉ•Ìˆ°mt¤è(€€€€€€€Á…Ñ €ôA…Ñ ¡Í½ÕÉ•l‰…‰Í½±ÕÑ•}Á…Ñ ‰t¤(€€€€€€€‰•™½É”€ôÍ¡„ÈÔÙ}™¥±”¡Á…Ñ ¤(€€€€€€€¥˜‰•™½É”€„ôÍ½ÕÉ•l‰Í½ÕÉ•}¡…Í ‰tè(€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È¡˜‰M½ÕÉ”¡…¹•…™Ñ•ÈÍ…¹¹¥¹œèíÍ½ÕÉ•lÍ½ÕÉ•}Á…Ñ uôˆ¤(€€€€€€€‰±½­Ì°Ý…É¹¥¹Ì°ÍÑÉÕÑÕÉ•€ô•áÑÉ…Ñ}‘½Õµ•¹Ð¡Á…Ñ °Í½ÕÉ•l‰Í½ÕÉ•}ÑåÁ”‰t¤(€€€€€€€Í½ÕÉ•}…¹‘¥‘…Ñ•Ì€ô•áÁ±¥¥Ñ}…¹‘¥‘…Ñ•Ì¡ÍÑÉÕÑÕÉ•°Í½ÕÉ”¤(€€€€€€€¡…Í}•áÁ±¥¥Ñ}™…Ñ}±¥ÍÐ€ô‰½½°¡Í½ÕÉ•}…¹‘¥‘…Ñ•Ì¤(€€€€€€€™½È¥Ñ•´¥¸‰±½­Ìè(€€€€€€€€€€€¥˜¡…Í}•áÁ±¥¥Ñ}™…Ñ}±¥ÍÐ…¹ÍÑÈ¡¥Ñ•´¹•Ð ‰±½…Ñ½Èˆ°€ˆˆ¤¤¹ÍÑ…ÉÑÍÝ¥Ñ  ˆ¹™…ÑÍlˆ¤è(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€Í½ÕÉ•}…¹‘¥‘…Ñ•Ì¹•áÑ•¹¡…¹‘¥‘…Ñ•Í}™É½µ}‰±½¬¡Í½ÕÉ”°¥Ñ•´¤¤(€€€€€€€…™Ñ•È€ôÍ¡„ÈÔÙ}™¥±”¡Á…Ñ ¤(€€€€€€€¥˜‰•™½É”€„ô…™Ñ•Èè(€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È¡˜‰I•…µ½¹±ä¥¹Ñ•É¥Ñä™…¥±ÕÉ”èíÍ½ÕÉ•lÍ½ÕÉ•}Á…Ñ uô¡…¹•‘ÕÉ¥¹œ•áÑÉ…Ñ¥½¸ˆ¤(€€€€€€€…¹‘¥‘…Ñ•Ì¹•áÑ•¹¡Í½ÕÉ•}…¹‘¥‘…Ñ•Ì¤(€€€€€€€‘½Õµ•¹ÑÌ¹…ÁÁ•¹ (€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}Á…Ñ ˆèÍ½ÕÉ•l‰Í½ÕÉ•}Á…Ñ ‰t°(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}ÑåÁ”ˆèÍ½ÕÉ•l‰Í½ÕÉ•}ÑåÁ”‰t°(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}¡…Í ˆèÍ½ÕÉ•l‰Í½ÕÉ•}¡…Í ‰t°(€€€€€€€€€€€€€€€€‰µ½‘¥™¥•‘}…ÐˆèÍ½ÕÉ•l‰µ½‘¥™¥•‘}…Ð‰t°(€€€€€€€€€€€€€€€€‰Í¥é•}‰åÑ•ÌˆèÍ½ÕÉ•l‰Í¥é•}‰åÑ•Ì‰t°(€€€€€€€€€€€€€€€€‰‰±½­}½Õ¹Ðˆè±•¸¡‰±½­Ì¤°(€€€€€€€€€€€€€€€€‰…¹‘¥‘…Ñ•}½Õ¹Ðˆè±•¸¡‘•‘ÕÁ±¥…Ñ•}…¹‘¥‘…Ñ•Ì¡Í½ÕÉ•}…¹‘¥‘…Ñ•Ì¤¤°(€€€€€€€€€€€€€€€€‰Ý…É¹¥¹ÌˆèÝ…É¹¥¹Ì°(€€€€€€€€€€€€€€€€‰‰±½­Ìˆè‰±½­Ì°(€€€€€€€€€€€ô(€€€€€€€€¤(€€€™¥¹…±}…¹‘¥‘…Ñ•Ì€ô‘•‘ÕÁ±¥…Ñ•}…¹‘¥‘…Ñ•Ì¡…¹‘¥‘…Ñ•Ì¤(€€€É•ÑÕÉ¸ì(€€€€€€€€‰•Ù¥‘•¹•}Ù•ÉÍ¥½¸ˆè€ˆÄ¸Àˆ°(€€€€€€€€‰•Ù¥‘•¹•}¥ˆèÍÑ…‰±•}¥ ‰•Ù¥‘•¹”µÍ•Ðˆ°µ…¹¥™•ÍÐ¹•Ð ‰µ…¹¥™•ÍÑ}¥ˆ¤°ml‰…¹‘¥‘…Ñ•}¥‰t™½ÈŒ¥¸™¥¹…±}…¹‘¥‘…Ñ•Ít¤°(€€€€€€€€‰•¹•É…Ñ•‘}…ÐˆèÕÑ}¹½Ü ¤°(€€€€€€€€‰µ…¹¥™•ÍÑ}¥ˆèµ…¹¥™•ÍÐ¹•Ð ‰µ…¹¥™•ÍÑ}¥ˆ¤°(€€€€€€€€‰É½½Ðˆèµ…¹¥™•ÍÐ¹•Ð ‰É½½Ðˆ¤°(€€€€€€€€‰µ…Ñ•É¥…±Ìˆèl(€€€€€€€€€€€ì(€€€€€€€€€€€€€€€­•äè‘½Õµ•¹Ñm­•åt(€€€€€€€€€€€€€€€™½È­•ä¥¸€ ‰Í½ÕÉ•}Á…Ñ ˆ°€‰Í½ÕÉ•}ÑåÁ”ˆ°€‰Í½ÕÉ•}¡…Í ˆ°€‰µ½‘¥™¥•‘}…Ðˆ°€‰Í¥é•}‰åÑ•Ìˆ°€‰Ý…É¹¥¹Ìˆ¤(€€€€€€€€€€€ô(€€€€€€€€€€€™½È‘½Õµ•¹Ð¥¸‘½Õµ•¹ÑÌ(€€€€€€€t°(€€€€€€€€‰‘½Õµ•¹ÑÌˆè‘½Õµ•¹ÑÌ°(€€€€€€€€‰…¹‘¥‘…Ñ•Ìˆè™¥¹…±}…¹‘¥‘…Ñ•Ì°(€€€€€€€€‰Ý…É¹¥¹Ìˆèl(€€€€€€€€€€€˜‰í‘½Õµ•¹ÑlÍ½ÕÉ•}Á…Ñ uôèíÝ…É¹¥¹ôˆ(€€€€€€€€€€€™½È‘½Õµ•¹Ð¥¸‘½Õµ•¹ÑÌ(€€€€€€€€€€€™½ÈÝ…É¹¥¹œ¥¸‘½Õµ•¹Ð¹•Ð ‰Ý…É¹¥¹Ìˆ°mt¤(€€€€€€€€€€€¥˜€‰•½‘•…Ìˆ¹½Ð¥¸Ý…É¹¥¹œ(€€€€€€€t°(€€€ô(()‘•˜Á…ÉÍ•}…ÉÌ ¤€´ø…ÉÁ…ÉÍ”¹9…µ•ÍÁ…”è(€€€Á…ÉÍ•È€ô…ÉÁ…ÉÍ”¹ÉÕµ•¹ÑA…ÉÍ•È¡‘•ÍÉ¥ÁÑ¥½¸ô‰áÑÉ…ÐÁÉ•¥Í”°É•…µ½¹±ä•Ù¥‘•¹”™É½´„Í½ÕÉ”µ…¹¥™•ÍÐ¸ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µµ…¹¥™•ÍÐˆ°É•ÅÕ¥É•õQÉÕ”°¡•±Àô‰A…Ñ É•…Ñ•‰äÍ…¹}Í½ÕÉ•Ì¹Áä¸ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ½ÕÑÁÕÐˆ°É•ÅÕ¥É•õQÉÕ”°¡•±Àô‰=ÕÑÁÕÐ•Ù¥‘•¹”¹©Í½¸Á…Ñ ¸ˆ¤(€€€É•ÑÕÉ¸Á…ÉÍ•È¹Á…ÉÍ•}…ÉÌ ¤(()‘•˜µ…¥¸ ¤€´ø¥¹Ðè(€€€…ÉÌ€ôÁ…ÉÍ•}…ÉÌ ¤(€€€µ…¹¥™•ÍÐ€ôÉ•…‘}©Í½¸¡A…Ñ ¡…ÉÌ¹µ…¹¥™•ÍÐ¤¤(€€€¥˜±•¸¡µ…¹¥™•ÍÐ¹•Ð ‰Í½ÕÉ•Ìˆ°mt¤¤€ð€Èè(€€€€€€€ÁÉ¥¹Ð ‰II=Hè•Ù¥‘•¹”•áÑÉ…Ñ¥½¸™½ÈÑ¡¥ÌM­¥±°É•ÅÕ¥É•Ì…Ð±•…ÍÐÑÝ¼Í½ÕÉ•Ì¸ˆ¤(€€€€€€€É•ÑÕÉ¸€È(€€€ÑÉäè(€€€€€€€Á…å±½…€ô•áÑÉ…Ñ}µ…¹¥™•ÍÐ¡µ…¹¥™•ÍÐ¤(€€€•á•ÁÐ€¡=MÉÉ½È°Y…±Õ•ÉÉ½È°IÕ¹Ñ¥µ•ÉÉ½È¤…Ì•áŒè(€€€€€€€ÁÉ¥¹Ð¡˜‰II=Hèí•áôˆ¤(€€€€€€€É•ÑÕÉ¸€Ä(€€€¡…¹•°™¥¹…±}Á…å±½…€ôÝÉ¥Ñ•}©Í½¹}¥‘•µÁ½Ñ•¹Ð¡A…Ñ ¡…ÉÌ¹½ÕÑÁÕÐ¤°Á…å±½…¤(€€€…Ñ¥½¸€ô€‰]É½Ñ”ˆ¥˜¡…¹••±Í”€‰U¹¡…¹•ˆ(€€€ÁÉ¥¹Ð¡˜‰í…Ñ¥½¹ôèíA…Ñ ¡…ÉÌ¹½ÕÑÁÕÐ¤¹É•Í½±Ù” ¥ô€¡í±•¸¡™¥¹…±}Á…å±½…‘l…¹‘¥‘…Ñ•Ìt¥ô…¹‘¥‘…Ñ•Ì¤ˆ¤(€€€™½ÈÝ…É¹¥¹œ¥¸™¥¹…±}Á…å±½…¹•Ð ‰Ý…É¹¥¹Ìˆ°mt¤è(€€€€€€€ÁÉ¥¹Ð¡˜‰]I9%9èíÝ…É¹¥¹ôˆ¤(€€€É•ÑÕÉ¸€À(()¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè(€€€É…¥Í”MåÍÑ•µá¥Ð¡µ…¥¸ ¤¤(