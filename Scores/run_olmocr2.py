import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import pymupdf
import yaml
from huggingface_hub import snapshot_download
from mlx_vlm import load, generate
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config
from olmocr.prompts.prompts import build_no_anchoring_v4_yaml_prompt

parser = argparse.ArgumentParser()
parser.add_argument("--pages", type=int, default=0,
                    help="First N pages; 0 processes all pages.")
args = parser.parse_args()
if args.pages < 0:
    parser.error("--pages must be zero or positive")

root = Path(__file__).resolve().parent
source = root / "input/marine_paper.pdf"
if not source.is_file():
    raise SystemExit(f"PDF missing: {source}")

with pymupdf.open(source) as pdf:
    total = len(pdf)
    if not total:
        raise SystemExit("PDF has no pages.")
    preview = pdf[0].get_text()[:800]

count = min(args.pages, total) if args.pages else total
mode = "smoke" if args.pages else "full"
stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
run = root / "output" / f"{mode}_{stamp}"
run.mkdir(parents=True)

model_id = "richardyoung/olmOCR-2-7B-1025-MLX-4bit"
local = root / "models/olmocr2-4bit"

summary = {
    "source": str(source),
    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "source_pages": total,
    "requested_pages": count,
    "model": model_id,
    "backend": "MLX-VLM custom runner",
    "quantization": "4-bit",
    "image_longest_side": 1288,
    "max_tokens": 8192,
    "temperature": 0.0,
    "page_concurrency": 1,
    "repetition_penalty": None,
    "pages": [],
    "status": "starting",
    "quality_validated": False,
}

def save():
    (run / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

def parse_text(raw):
    text = raw.strip()
    text = re.sub(
        r"\A```(?:markdown|md|yaml|json)?[ \t]*\r?\n",
        "", text, count=1, flags=re.IGNORECASE
    )
    text = re.sub(r"\r?\n```[ \t]*\Z", "", text).strip()

    # YAML metadata followed by Markdown article text.
    front = re.match(
        r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)",
        text, flags=re.DOTALL
    )
    if front and "primary_language:" in front.group(1):
        body = text[front.end():].strip()
        if not body:
            raise ValueError("Metadata only; article text is empty.")
        return body, "markdown_with_metadata"

    # YAML or JSON object containing natural_text.
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        data = None

    if isinstance(data, dict):
        body = data.get("natural_text")
        if isinstance(body, str) and body.strip():
            return body, "natural_text_field"
        raise ValueError("Metadata/object has no nonempty natural_text.")

    # Preserve unexpected output for review, without silently discarding it.
    if text:
        return text, "unrecognized_format"
    raise ValueError("Empty model response.")

with (run / "environment.txt").open("w") as handle:
    subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        stdout=handle, check=True
    )

(run / "source_preview.txt").write_text(preview, encoding="utf-8")
print(f"PDF pages: {total}; processing: {count}", flush=True)
print(f"Source preview:\n{preview}", flush=True)
print(f"Output folder: {run}", flush=True)
save()

# Reuse the local model or cached snapshot where possible.
if (local / "config.json").is_file():
    model_path = str(local)
else:
    print("Resolving model; missing files may be downloaded.", flush=True)
    model_path = snapshot_download(model_id)

summary["model_path"] = str(model_path)
save()

prompt = build_no_anchoring_v4_yaml_prompt()
(run / "prompt.txt").write_text(prompt, encoding="utf-8")

print("Loading model...", flush=True)
load_start = time.perf_counter()
model, processor = load(model_path)
config = load_config(model_path)
summary["model_load_seconds"] = time.perf_counter() - load_start

formatted = apply_chat_template(
    processor, config, prompt, num_images=1
)

combined = run / "marine_paper.md"
combined.write_text("", encoding="utf-8")
summary["status"] = "running"
save()
conversion_start = time.perf_counter()

with pymupdf.open(source) as pdf:
    for index in range(count):
        number = index + 1
        folder = run / f"page_{number:03d}"
        folder.mkdir()
        record = {"source_page": number, "warnings": []}
        body = None
        page_start = time.perf_counter()

        print(f"Processing page {number}/{count}...", flush=True)
        try:
            page = pdf[index]
            scale = 1288 / max(page.rect.width, page.rect.height)
            image = folder / "source.png"
            page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale),
                colorspace=pymupdf.csRGB,
                alpha=False,
            ).save(image)

            inference_start = time.perf_counter()
            result = generate(
                model, processor, formatted,
                image=[str(image)],
                max_tokens=8192,
                temperature=0.0,
                verbose=False,
            )
            record["inference_seconds"] = (
                time.perf_counter() - inference_start
            )
            raw = result if isinstance(result, str) else result.text
            (folder / "raw_response.txt").write_text(
                raw, encoding="utf-8"
            )

            tokens = getattr(result, "generation_tokens", None)
            record["generated_tokens"] = (
                int(tokens) if tokens is not None else None
            )
            if tokens is not None and int(tokens) >= 8192:
                record["warnings"].append("Token limit reached")
            if re.search(r"(?:\.\s*){100,}", raw):
                record["warnings"].append("Excessive repeated dots")

            body, format_name = parse_text(raw)
            record["response_format"] = format_name
            if format_name == "unrecognized_format":
                record["warnings"].append("Unrecognized response format")

            (folder / "text.md").write_text(body, encoding="utf-8")
            record["status"] = (
                "review_required" if record["warnings"] else "text_saved"
            )

        except Exception as error:
            record["status"] = "failed_or_missing_text"
            record["error"] = str(error)
            (folder / "error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )

        record["text_characters"] = len(body) if body else 0
        record["page_seconds"] = time.perf_counter() - page_start

        with combined.open("a", encoding="utf-8") as handle:
            handle.write(f"<!-- Source page {number} -->\n\n")
            handle.write(
                body if body else
                "[NO_PARSED_TEXT: inspect raw_response.txt and error.txt]"
            )
            handle.write("\n\n")

        (folder / "metadata.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
        summary["pages"].append(record)
        save()
        print(
            f"Page {number}: {record['status']}; "
            f"warnings={record['warnings']}",
            flush=True,
        )

elapsed = time.perf_counter() - conversion_start
failed = [
    p["source_page"] for p in summary["pages"]
    if p["status"] == "failed_or_missing_text"
]
review = [
    p["source_page"] for p in summary["pages"]
    if p["status"] == "review_required"
]
summary.update({
    "status": "finished",
    "conversion_seconds_excluding_model_load": elapsed,
    "seconds_per_attempted_page_excluding_model_load": elapsed / count,
    "failed_or_missing_text_pages": failed,
    "review_required_pages": review,
})
save()

print(f"\nOutput: {combined}")
print(f"Summary: {run / 'summary.json'}")
print(f"Failed/missing text pages: {failed}")
print(f"Review required: {review}")
print(f"Conversion time excluding model load: {elapsed:.2f} seconds")
print("Saved text does not prove complete or accurate extraction.")
