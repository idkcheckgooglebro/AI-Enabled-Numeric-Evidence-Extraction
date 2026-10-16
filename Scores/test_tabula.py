from pathlib import Path
from datetime import datetime
from importlib.metadata import version
import csv
import hashlib
import html
import json
import subprocess
import sys
import time
import traceback

import tabula
from pypdf import PdfReader

root = Path(__file__).resolve().parent
source = root / "input/marine_paper.pdf"

if not source.is_file():
    raise SystemExit(f"PDF missing: {source}")

pages = len(PdfReader(str(source)).pages)
if pages == 0:
    raise SystemExit("PDF has no pages.")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
run = root / "output" / f"run_{stamp}"
run.mkdir(parents=True)

summary = {
    "source": str(source),
    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "source_pages": pages,
    "tabula_py_version": version("tabula-py"),
    "settings": {
        "guess": True,
        "force_subprocess": True,
        "java_options": ["-Xmx2G"],
        "page_by_page": True,
    },
    "modes": {},
    "quality_validated": False,
}

def save_summary():
    (run / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

with (run / "environment.txt").open("w") as handle:
    subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        stdout=handle, check=True
    )

java = subprocess.run(
    ["java", "-version"],
    capture_output=True, text=True, check=True
)
(run / "java_version.txt").write_text(
    java.stdout + java.stderr, encoding="utf-8"
)

print(f"Source pages: {pages}", flush=True)
print(f"Output folder: {run}", flush=True)
save_summary()

for mode in ["stream", "lattice"]:
    folder = run / mode
    folder.mkdir()

    records = []
    table_count = 0
    extraction_seconds = 0.0
    mode_start = time.perf_counter()
    sections = []

    for page in range(1, pages + 1):
        page_folder = folder / f"page_{page:03d}"
        page_folder.mkdir()
        record = {"source_page": page}
        print(f"{mode}: page {page}/{pages}", flush=True)

        started = time.perf_counter()
        try:
            tables = tabula.read_pdf(
                str(source),
                pages=page,
                output_format="json",
                guess=True,
                stream=(mode == "stream"),
                lattice=(mode == "lattice"),
                force_subprocess=True,
                java_options=["-Xmx2G"],
            )
            elapsed = time.perf_counter() - started
            extraction_seconds += elapsed

            if not isinstance(tables, list):
                raise TypeError("Expected a list of table candidates.")

            (page_folder / "raw_tables.json").write_text(
                json.dumps(tables, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )

            record.update({
                "status": "processed",
                "table_candidates": len(tables),
                "extraction_seconds": elapsed,
            })

            for number, table in enumerate(tables, start=1):
                table_count += 1
                rows = [
                    [cell.get("text", "") for cell in row]
                    for row in table.get("data", [])
                ]
                name = f"table_{number:02d}"

                with (page_folder / f"{name}.csv").open(
                    "w", newline="", encoding="utf-8"
                ) as handle:
                    csv.writer(handle).writerows(rows)

                table_html = "<table>" + "".join(
                    "<tr>" + "".join(
                        "<td>" + html.escape(str(cell)).replace(
                            "\n", "<br>"
                        ) + "</td>"
                        for cell in row
                    ) + "</tr>"
                    for row in rows
                ) + "</table>"

                sections.append(
                    f"<h2>Source page {page} — candidate {number}</h2>"
                    + table_html
                )

                metadata = {
                    "source_page": page,
                    "candidate_number": number,
                    "extraction_method": table.get("extraction_method"),
                    "top": table.get("top"),
                    "left": table.get("left"),
                    "width": table.get("width"),
                    "height": table.get("height"),
                    "rows": len(rows),
                    "maximum_columns": max(
                        (len(row) for row in rows), default=0
                    ),
                }
                (page_folder / f"{name}_metadata.json").write_text(
                    json.dumps(metadata, indent=2), encoding="utf-8"
                )

        except Exception as error:
            elapsed = time.perf_counter() - started
            record.update({
                "status": "error",
                "error": str(error),
                "page_seconds": elapsed,
            })
            (page_folder / "error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
            print(f"ERROR: {error}", flush=True)

        records.append(record)
        summary["modes"][mode] = {
            "pages": records,
            "table_candidates": table_count,
            "status": "running",
        }
        save_summary()

    duration = time.perf_counter() - mode_start
    document = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:Arial;margin:30px}"
        "table{border-collapse:collapse;margin-bottom:30px}"
        "td{border:1px solid #888;padding:5px;vertical-align:top}"
        "</style></head><body>"
        f"<h1>Tabula — {mode}</h1>"
        "<p>Automatically detected candidates; not validated tables.</p>"
        + "".join(sections)
        + "</body></html>"
    )
    (folder / "tables.html").write_text(document, encoding="utf-8")

    summary["modes"][mode] = {
        "status": "finished",
        "table_candidates": table_count,
        "extraction_seconds_successful_calls": extraction_seconds,
        "processing_and_export_seconds": duration,
        "seconds_per_source_page": duration / pages,
        "error_pages": [
            r["source_page"] for r in records if r["status"] == "error"
        ],
        "pages": records,
    }
    save_summary()
    print(
        f"{mode}: {table_count} candidates; "
        f"{duration:.2f} seconds including exports",
        flush=True
    )

print(f"\nFinished. Results: {run}", flush=True)
print("Candidate counts do not establish table coverage or accuracy.")
