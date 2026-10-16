# Numerical evidence extraction from scientific documents: tool inventory and empirical evaluation

Data and scripts accompanying:

> Rajendran, A.A., Kotta, J., Yousef, T., Tapaninen, U.P. (2026). *AI-enabled evidence infrastructure for ocean and coastal management: extracting numbers from scientific documents.* Submitted to Environmental Modelling & Software.

The study (1) proposes a framework for context-aware numerical evidence extraction, (2) maps 61 published extraction tools against it, and (3) evaluates five of these tools (Docling, Nougat, PP-StructureV2, olmOCR 2, Tabula) on five full marine and coastal research articles against manually verified references.

## Repository contents

| Folder | Contents | Paper |
|---|---|---|
| `inventory/` | Structured inventory of the 61 reviewed tools (CSV and XLSX) and classification codes | Supplementary Material S1 |
| `protocol/` | Database search strings as executed and study selection criteria | Supplementary Material S2 |
| `evaluation/articles.csv` | The five test articles with full citations and DOIs | Section 4.7 |
| `evaluation/reference_annotations/` | Manually verified reference values used for scoring, one CSV per article | Section 4.7 |
| `evaluation/tool_outputs/` | Extracted table outputs that were scored, per tool and article | Section 5.6 |
| `evaluation/scores/` | Per-article counts, pooled results (Table 6) and assessment notes (XLSX and CSV) | Supplementary Material S3, Table 6 |
| `scripts/` | Scripts and commands used to run and score the tools | Section 4.7 |
| `environment/` | Tool versions, run mode, settings and hardware | Table S3.3 |

## Test articles and copyright

The evaluated articles are listed with DOIs in `evaluation/articles.csv`. The PDFs and the full text produced by the tools are **not redistributed** because the articles are protected by copyright. Only the table values that were scored are included. Full outputs can be reproduced by running the commands in `scripts/commands.md` on the original PDFs.

## How scores were calculated

Each measure is expressed as **n / N**: n = items the tool got right, N = items checked in the source article. Items missing from an output count as failures, and counts are pooled across articles. "NS" = not supported by tool design; "not scored" = not assessed; "not run" = tool not run on that article. Full definitions are given in Section 4.7 of the paper and in `evaluation/scores/S3_empirical_evaluation.xlsx` (README sheet).

## Reusing the scorer

`scripts/score_extraction.py` compares any tool's extracted values with a reference CSV:

```
python scripts/score_extraction.py --gt evaluation/reference_annotations/P1_Bergstrom2013.csv --results <folder with evidence_candidates.jsonl>
```

## Licence

- Code (`scripts/`): MIT – see `LICENSE`
- Data (all other files): CC BY 4.0 – see `LICENSE-data`

## Citation

Please cite the paper and this dataset (see `CITATION.cff`; DOI from Zenodo).

## Contact

Athira Asalatha Rajendran, Estonian Maritime Academy, Tallinn University of Technology – athira@taltech.ee
