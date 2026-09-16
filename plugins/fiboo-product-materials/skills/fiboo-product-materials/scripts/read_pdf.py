"""Extract page-addressable native PDF text; never imply OCR or visual review."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True)
    parser.add_argument("--output", required=True, help="New UTF-8 JSON file")
    args = parser.parse_args()
    try:
        from pypdf import PdfReader
    except ImportError:
        print(json.dumps({"status": "dependency_missing", "dependency": "pypdf",
                          "hint": "Use a Python environment with pypdf, or the host's PDF reader."}))
        return 2
    source = Path(args.file).resolve(strict=True)
    if source.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("PDF exceeds 64 MiB")
    data = source.read_bytes()
    if not data.startswith(b"%PDF-"):
        raise ValueError("The file is not a PDF")
    reader = PdfReader(source)
    pages = [{"page": i + 1, "text": page.extract_text() or ""}
             for i, page in enumerate(reader.pages)]
    sparse = [p["page"] for p in pages if len(p["text"].strip()) < 20]
    result = {"source_path": str(source), "sha256": hashlib.sha256(data).hexdigest(),
              "extraction": "native_text_only", "page_count": len(pages), "pages": pages,
              "warnings": ["Native text extraction does not verify visual layout, tables, or image text."],
              "sparse_pages": sparse}
    if sparse:
        result["warnings"].append("Sparse pages need image review/OCR before claiming complete coverage.")
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({"status": "success", "output_path": str(output),
                      "page_count": len(pages), "sparse_pages": sparse,
                      "characters": sum(len(p["text"]) for p in pages)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"status": "error", "type": type(exc).__name__,
                          "message": "PDF extraction failed; no verified text result was produced."}))
        sys.exit(1)
