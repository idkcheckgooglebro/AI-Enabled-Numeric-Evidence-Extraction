from pathlib import Path
from datetime import datetime
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time

import pymupdf
from pypdf import PdfReader

parser = argparse.ArgumentParser()
parser.add_argument("--no-skipping", action="store_true")
args = parser.parse_args()

root = Path(__file__).resolve().parent
source = root / "input" / "marine_paper.pdf"

if not source.is_file():
    raise SystemExit(f"PDF not found: {source}")

nougat = shutil.which("nougat")
if not nougat:
    raise SystemExit("Activate nougat-test before running.")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
mode = "no_skipping" if args.no_skipping else "baseline"
run = root / "output" / f"{mode}_{stamp}"
run.mkdir(parents=True)

summary = {
    "source": str(source),
    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "model": "0.1.0-small",
    "batchsize": 1,
    "full_precision": True,
    "no_skipping": args.no_skipping,
}

def save_summary():
    (run / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

with (run / "environment.txt").open("w") as handle:
    subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        stdout=handle,
        check=True,
    )

with pymupdf.open(source) as document:
    pages = len(document)
    if pages == 0:
        raise SystemExit("The PDF has no pages.")
    preview = document[0].get_text()[:700]

summary["source_pages"] = pages
(run / "source_preview.txt").write_text(preview, encoding="utf-8")

print(f"\nSource pages: {pages}")
print(f"Source preview:\n{preview}\n", flush=True)

conversion_input = source
summary["pdf_rewritten"] = False

try:
    parsed_pages = len(PdfReader(str(source)).pages)
    if parsed_pages != pages:
        raise ValueError("PDF parsers disagree on the page count.")
except Exception as error:
    summary["original_parser_error"] = str(error)
    conversion_input = run / "marine_paper_repaired.pdf"
    print("Rewriting PDF into a separate copy.", flush=True)

    repair_start = time.perf_counter()
    with pymupdf.open(source) as document:
        document.save(conversion_input, garbage=4, deflate=True)

    summary["repair_seconds"] = round(
        time.perf_counter() - repair_start, 2
    )

    parsed_pages = len(PdfReader(str(conversion_input)).pages)
    if parsed_pages != pages:
        raise SystemExit("Repaired PDF page count changed.")

    summary["pdf_rewritten"] = True

summary["conversion_input"] = str(conversion_input)

command = [
    nougat,
    str(conversion_input),
    "--out", str(run),
    "--model", "0.1.0-small",
    "--batchsize", "1",
    "--full-precision",
    "--recompute",
]

if args.no_skipping:
    command.append("--no-skipping")

summary["command"] = command
save_summary()

print(f"Output folder: {run}", flush=True)
print(f"Progress log: {run / 'run.log'}", flush=True)

start = time.perf_counter()
with (run / "run.log").open("w") as log:
    result = subprocess.run(
        command, stdout=log, stderr=subprocess.STDOUT
    )

elapsed = time.perf_counter() - start
output = run / f"{conversion_input.stem}.mmd"

summary["exit_status"] = result.returncode
summary["conversion_seconds"] = round(elapsed, 2)
summary["seconds_per_source_page"] = round(elapsed / pages, 2)
summary["output_file"] = str(output)
summary["output_exists"] = output.is_file()

if output.is_file():
    text = output.read_text(encoding="utf-8")
    markers = re.findall(r"\[MISSING_PAGE[^\]]*\]", text)
    summary["missing_page_markers"] = markers
    summary["output_characters"] = len(text)

    print(f"\nMMD file: {output}")
    print(f"Missing-page markers: {len(markers)}")
    for marker in markers:
        print(f"  {marker}")
else:
    print("\nNo MMD generated. Check run.log.")

save_summary()

print(f"Exit status: {result.returncode}")
print(f"Conversion time: {elapsed:.2f} seconds")
print(f"Seconds per source page: {elapsed / pages:.2f}")
print(f"Summary: {run / 'summary.json'}")
print("Output existence does not prove complete extraction.")
