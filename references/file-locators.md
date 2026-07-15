# File locator rules

Return a locator that another person can use without searching the whole file.

| Format | Required locator form | Example |
|---|---|---|
| Markdown, TXT, code, config | line number | `line 42` |
| PDF | page number | `page 7` |
| DOCX | paragraph/heading or table cell | `heading "Results", paragraph 31`; `table 2, row 3, cell B3` |
| PPTX | slide and text box | `slide 6, text box 3` |
| XLSX | sheet and cell | `sheet "Results", cell C12` |
| CSV | row and column | `row 8, column "mAP@0.5"` |
| JSON/YAML | key path | `$.facts[2].value` |

Keep the source path, SHA-256, modification timestamp, excerpt, extracted value, and evidence kind next to the locator. If an optional extractor is unavailable or a file is malformed, emit a warning. Do not replace a missing locator with “mentioned in the file.”

The bundled DOCX, PPTX, and XLSX readers parse OOXML with the Python standard library. PDF text extraction uses `pypdf`, `PyPDF2`, or `pdfplumber` when locally available. YAML uses `PyYAML` when available and otherwise retains indentation-derived key paths for simple mappings.
