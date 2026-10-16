from pathlib import Path
from datetime import datetime
from importlib.metadata import version
import argparse
import hashlib
import html
import json
import subprocess
import sys
import time
import traceback

import cv2
import numpy as np
import pymupdf
from paddleocr import PPStructure

parser = argparse.ArgumentParser()
parser.add_argument("--dpi", type=int, default=200)
parser.add_argument("--pages", type=int, default=None,
                    help="Process only the first N pages; omit for full PDF.")
args = parser.parse_args()

if args.dpi <= 0 or (args.pages is not None and args.pages <= 0):
    parser.error("DPI and page limit must be positive.")

root = Path(__file__).resolve().parent
source = root / "input" / "marine_paper.pdf"
if not source.is_file():
    raise SystemExit(f"PDF not found: {source}")

run = root / "output" / datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
run.mkdir(parents=True)
pages_dir = run / "pages"
pages_dir.mkdir()

def serializable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): serializable(v) for k, v in value.items()
                if k != "img"}
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value

summary = {
    "source": str(source),
    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "dpi": args.dpi,
    "device": "CPU",
    "language": "en",
    "layout": True,
    "table": True,
    "ocr": True,
    "image_orientation": False,
    "page_limit": args.pages,
    "output_folder": str(run),
    "pages": [],
}

def save_summary():
    (run / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

for package in ["paddleocr", "paddlepaddle", "pymupdf", "numpy"]:
    try:
        summary[package + "_version"] = version(package)
    except Exception:
        summary[package + "_version"] = "unavailable"

with (run / "environment.txt").open("w") as handle:
    subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        stdout=handle, check=True,
    )

document = pymupdf.open(source)
source_pages = len(document)
if source_pages == 0:
    raise SystemExit("PDF contains no pages.")
if document.needs_pass:
    raise SystemExit("PDF requires a password.")

count = min(args.pages, source_pages) if args.pages else source_pages
summary["source_pages"] = source_pages
summary["requested_pages"] = count

preview = document[0].get_text()[:1000]
(run / "source_preview.txt").write_text(preview, encoding="utf-8")

print(f"Source: {source}", flush=True)
print(f"Source pages: {source_pages}", flush=True)
print(f"Pages to process: {count}", flush=True)
print(f"Output folder: {run}", flush=True)
print(f"Source preview:\n{preview}\n", flush=True)
save_summary()

total_start = time.perf_counter()
init_start = time.perf_counter()

try:
    engine = PPStructure(
        lang="en",
        use_gpu=False,
        layout=True,
        table=True,
        ocr=True,
        image_orientation=False,
        show_log=True,
    )
except Exception:
    summary["initialization_error"] = traceback.format_exc()
    save_summary()
    raise

summary["model_initialization_seconds"] = round(
    time.perf_counter() - init_start, 3
)

html_pages = []
text_pages = []
failed = []

for index in range(count):
    number = index + 1
    page_dir = pages_dir / f"page_{number:03d}"
    page_dir.mkdir()
    record = {"source_page": number}
    page_start = time.perf_counter()

    print(f"Processing page {number}/{count}", flush=True)

    try:
        page = document[index]
        record["source_page_size_points"] = [
            page.rect.width, page.rect.height
        ]

        render_start = time.perf_counter()
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(args.dpi / 72, args.dpi / 72),
            colorspace=pymupdf.csRGB,
            alpha=False,
        )
        pix.save(page_dir / "source.png")

        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, 3
        )
        image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        record["rendered_size_pixels"] = [pix.width, pix.height]
        record["render_seconds"] = round(
            time.perf_counter() - render_start, 3
        )

        inference_start = time.perf_counter()
        result = engine(image)
        record["inference_seconds"] = round(
            time.perf_counter() - inference_start, 3
        )

        # Preserve the order returned by PPStructure.
        # No additional reading-order algorithm is applied.
        clean = serializable(result)
        (page_dir / "structure.json").write_text(
            json.dumps(clean, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        page_html = [f"<h2>Source page {number}</h2>"]
        page_text = [f"<!-- Source page {number} -->"]
        blocks = []
        table_count = 0

        for block_index, block in enumerate(result, start=1):
            block_type = str(block.get("type", "unknown"))
            bbox = block.get("bbox", [])
            res = block.get("res")
            block_name = f"block_{block_index:03d}"

            entry = {
                "block": block_index,
                "type": block_type,
                "bbox_pixels": serializable(bbox),
            }

            if len(bbox) == 4:
                entry["bbox_pdf_points"] = [
                    float(bbox[0]) * page.rect.width / pix.width,
                    float(bbox[1]) * page.rect.height / pix.height,
                    float(bbox[2]) * page.rect.width / pix.width,
                    float(bbox[3]) * page.rect.height / pix.height,
                ]

            if block_type.lower() == "table":
                table_count += 1
                table_html = (
                    res.get("html", "") if isinstance(res, dict) else ""
                )
                entry["table_html_exists"] = bool(table_html)

                if table_html:
                    table_file = page_dir / f"{block_name}_table.html"
                    table_file.write_text(table_html, encoding="utf-8")
                    page_html.append(table_html)
                    page_text.append(table_html)
                else:
                    page_html.append("<p>[Table HTML missing]</p>")
                    page_text.append("[Table HTML missing]")

            else:
                texts = []
                if isinstance(res, list):
                    texts = [
                        str(item.get("text", ""))
                        for item in res
                        if isinstance(item, dict) and item.get("text")
                    ]
                elif isinstance(res, dict) and res.get("text"):
                    texts = [str(res["text"])]

                if texts:
                    page_html.append(
                        "<p>" + "<br>".join(html.escape(t) for t in texts)
                        + "</p>"
                    )
                    page_text.extend(texts)

            # Save detected figure/image-region crops.
            # These are crops from the source, not recovered plotted values.
            if block_type.lower() in {"figure", "image"} and len(bbox) == 4:
                x0, y0, x1, y1 = [int(v) for v in bbox]
                x0, y0 = max(0, x0), max(0, y0)
                x1, y1 = min(pix.width, x1), min(pix.height, y1)
                crop = image[y0:y1, x0:x1]
                if crop.size:
                    filename = f"{block_name}_figure.png"
                    if not cv2.imwrite(str(page_dir / filename), crop):
                        raise RuntimeError("Could not save figure crop.")
                    relative = f"pages/page_{number:03d}/{filename}"
                    page_html.append(
                        f'<img src="{relative}" alt="Detected figure crop">'
                    )
                    entry["figure_crop"] = relative

            blocks.append(entry)

        record["status"] = "processed"
        record["detected_blocks"] = len(result)
        record["detected_table_regions"] = table_count

        (page_dir / "provenance.json").write_text(
            json.dumps(blocks, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        html_pages.append("\n".join(page_html))
        text_pages.append("\n".join(page_text))

    except Exception:
        failed.append(number)
        record["status"] = "failed"
        record["error"] = traceback.format_exc()
        (page_dir / "error.txt").write_text(
            record["error"], encoding="utf-8"
        )
        html_pages.append(
            f"<h2>Source page {number}</h2><p>[Processing failed]</p>"
        )
        text_pages.append(
            f"<!-- Source page {number} -->\n[Processing failed]"
        )
        print(record["error"], flush=True)

    record["page_processing_seconds"] = round(
        time.perf_counter() - page_start, 3
    )
    summary["pages"].append(record)
    save_summary()

document.close()

(run / "document.html").write_text(
    '<!doctype html><html><head><meta charset="utf-8">'
    '<title>PP-StructureV2 extraction</title>'
    '<style>body{max-width:1100px;margin:30px auto;font-family:Arial}'
    'table{border-collapse:collapse;margin:20px 0}'
    'td,th{border:1px solid #aaa;padding:5px}'
    'img{max-width:100%;height:auto}</style></head><body>'
    + "\n<hr>\n".join(html_pages) + "</body></html>",
    encoding="utf-8",
)

(run / "document.txt").write_text(
    "\n\n".join(text_pages), encoding="utf-8"
)

summary["failed_pages"] = failed
summary["processed_pages"] = count - len(failed)
summary["conversion_seconds_including_initialization"] = round(
    time.perf_counter() - total_start, 3
)
summary["mean_seconds_per_attempted_page"] = round(
    summary["conversion_seconds_including_initialization"] / count, 3
)
summary["all_source_pages_attempted"] = count == source_pages
summary["exit_status"] = 1 if failed else 0
save_summary()

print(f"\nProcessed pages: {count - len(failed)}/{count}", flush=True)
print(f"Failed pages: {failed}", flush=True)
print(f"HTML: {run / 'document.html'}", flush=True)
print(f"Text: {run / 'document.txt'}", flush=True)
print(f"Summary: {run / 'summary.json'}", flush=True)
print("Processed pages do not prove complete or accurate extraction.")

raise SystemExit(summary["exit_status"])
