from pathlib import Path
from importlib.metadata import version
import hashlib
import json
import logging
import time

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    TableFormerMode,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import ImageRefMode

logging.basicConfig(level=logging.INFO)

# Paths are relative to this script, not your Terminal location.
ROOT = Path(__file__).resolve().parent
PDF = ROOT / "input" / "marine_paper.pdf"
OUT = ROOT / "output" / "baseline"

if not PDF.exists():
    raise FileNotFoundError(f"PDF not found: {PDF}")

for folder in ["tables", "figures", "pages"]:
    (OUT / folder).mkdir(parents=True, exist_ok=True)

# Configure the standard PDF pipeline.
options = PdfPipelineOptions()
options.do_ocr = True
options.do_table_structure = True
options.table_structure_options.mode = TableFormerMode.ACCURATE
options.table_structure_options.do_cell_matching = True
options.generate_page_images = True
options.generate_picture_images = True
options.images_scale = 2.0

converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=options)
    }
)

started = time.perf_counter()
result = converter.convert(PDF)
conversion_seconds = time.perf_counter() - started
doc = result.document

# Full structured document, including available provenance.
doc.save_as_json(OUT / "document.json")

# Readable exports with figures embedded.
doc.save_as_markdown(
    OUT / "document.md",
    image_mode=ImageRefMode.EMBEDDED,
)
doc.save_as_html(
    OUT / "document.html",
    image_mode=ImageRefMode.EMBEDDED,
)

(OUT / "document.txt").write_text(
    doc.export_to_text(),
    encoding="utf-8",
)

# Export each detected table.
table_metadata = []

for i, table in enumerate(doc.tables, start=1):
    name = f"table_{i:03d}"

    dataframe = table.export_to_dataframe(doc=doc)
    dataframe.to_csv(OUT / "tables" / f"{name}.csv", index=False)

    (OUT / "tables" / f"{name}.html").write_text(
        table.export_to_html(doc=doc),
        encoding="utf-8",
    )

    image = table.get_image(doc)
    if image is not None:
        image.save(OUT / "tables" / f"{name}.png")

    table_metadata.append({
        "id": name,
        "self_ref": table.self_ref,
        "caption": table.caption_text(doc),
        "provenance": [
            p.model_dump(mode="json") for p in table.prov
        ],
    })

# Export each detected picture region.
figure_metadata = []

for i, picture in enumerate(doc.pictures, start=1):
    name = f"figure_{i:03d}"

    image = picture.get_image(doc)
    if image is not None:
        image.save(OUT / "figures" / f"{name}.png")

    figure_metadata.append({
        "id": name,
        "self_ref": picture.self_ref,
        "caption": picture.caption_text(doc),
        "provenance": [
            p.model_dump(mode="json") for p in picture.prov
        ],
    })

# Export rendered pages for visual comparison.
for page_number, page in doc.pages.items():
    if page.image is not None:
        page.image.pil_image.save(
            OUT / "pages" / f"page_{page_number:03d}.png"
        )

# Save item locations and extracted text in reading order.
items = []

for item, level in doc.iterate_items():
    items.append({
        "self_ref": item.self_ref,
        "label": str(getattr(item, "label", "")),
        "level": level,
        "text": getattr(item, "text", None),
        "provenance": [
            p.model_dump(mode="json")
            for p in getattr(item, "prov", [])
        ],
    })

def save_json(filename, data):
    (OUT / filename).write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

save_json("table_metadata.json", table_metadata)
save_json("figure_metadata.json", figure_metadata)
save_json("items_with_provenance.json", items)

summary = {
    "input_file": PDF.name,
    "input_sha256": hashlib.sha256(PDF.read_bytes()).hexdigest(),
    "docling_version": version("docling"),
    "docling_core_version": version("docling-core"),
    "status": str(result.status),
    "conversion_seconds": round(conversion_seconds, 2),
    "pages_in_output": len(doc.pages),
    "detected_tables": len(doc.tables),
    "detected_picture_regions": len(doc.pictures),
    "settings": {
        "ocr": True,
        "table_mode": "accurate",
        "cell_matching": True,
        "image_scale": 2.0,
    },
    "errors": [
        error.model_dump(mode="json") for error in result.errors
    ],
}

save_json("run_summary.json", summary)

print(json.dumps(summary, indent=2))
print(f"\nOutputs saved to: {OUT}")