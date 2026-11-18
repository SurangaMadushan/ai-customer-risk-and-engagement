"""Step 22 — safe local inspection of SLT historical Parquet / usage files.

Run from the SLT_Payment_Project folder:
    python 22_inspect_real_data_FIXED.py

If your files are in a different folder:
    python 22_inspect_real_data_FIXED.py --folder "C:\\path\\to\\billing-folder"

Only file metadata is read for Parquet; no customer rows are displayed.
Usage CSV/Excel inspection reads a small sample only for column names.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUPPORTED = {".parquet", ".csv", ".xlsx", ".xlsm"}


def inspect_parquet(path: Path) -> dict:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "pyarrow is not installed in the selected Python environment. "
            "Run: python -m pip install pyarrow"
        ) from exc
    pqfile = pq.ParquetFile(str(path))
    return {
        "format": "parquet",
        "rows": int(pqfile.metadata.num_rows),
        "row_groups": int(pqfile.metadata.num_row_groups),
        "columns": [
            {"name": f.name, "dtype": str(f.type)}
            for f in pqfile.schema_arrow
        ],
        "note": "Metadata only. Customer rows were not read.",
    }


def inspect_other(path: Path) -> dict:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "pandas is not installed. Run: python -m pip install pandas openpyxl"
        ) from exc
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, nrows=100, low_memory=False)
        return {"format": "csv", "sampled_rows": len(df),
                "columns": [{"name": str(x), "dtype": str(df[x].dtype)} for x in df.columns],
                "note": "Only first 100 records sampled; total record count not calculated."}
    try:
        with pd.ExcelFile(path) as book:
            sheets = []
            for name in book.sheet_names[:20]:
                df = pd.read_excel(book, sheet_name=name, nrows=100)
                sheets.append({"sheet": name, "sampled_rows": len(df),
                               "columns": [str(x) for x in df.columns]})
    except ImportError as exc:
        raise RuntimeError("Excel support missing. Run: python -m pip install openpyxl") from exc
    return {"format": "excel", "sheets": sheets,
            "note": "Only first 100 records per sheet sampled."}


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect billing Parquet metadata and usage-file schemas safely")
    parser.add_argument("--folder", type=Path, help="Optional custom folder containing historical files")
    args = parser.parse_args()

    print("\nSLT STEP 22 — HISTORICAL BILLING INSPECTION")
    print("Python:", sys.executable)
    print("Project:", ROOT)

    if args.folder:
        folders = [args.folder.expanduser().resolve()]
    else:
        folders = [ROOT / "data" / "historical", ROOT / "data" / "usage"]
        # Help if files were pasted directly into the data folder or project root.
        fallback = [ROOT / "data", ROOT]
        if not any(d.is_dir() and any(d.rglob("*.parquet")) for d in folders):
            folders.extend(fallback)

    print("Searching folders:")
    for folder in folders:
        print(" -", folder, "[found]" if folder.is_dir() else "[not found]")

    matches: dict[Path, None] = {}
    for folder in folders:
        if folder.is_dir():
            for item in folder.rglob("*"):
                if item.is_file() and item.suffix.lower() in SUPPORTED and not item.name.startswith("~$"):
                    matches[item.resolve()] = None

    files = sorted(matches)
    if not files:
        print("\nNO DATA FILES FOUND.")
        print("Create data\\historical and place DIM_BILL_2024_masked.parquet there.")
        print("Check with PowerShell: Get-ChildItem .\\data\\historical\\*.parquet")
        print("If files are elsewhere, use: python 22_inspect_real_data_FIXED.py --folder \"C:\\your\\folder\"")
        return 1

    print(f"\nFound {len(files)} supported file(s).")
    results = []
    failures = 0
    for file in files:
        print("\nFile:", file.name)
        try:
            info = inspect_parquet(file) if file.suffix.lower() == ".parquet" else inspect_other(file)
            info["filename"] = file.name
            # Do not record absolute local paths in the report.
            results.append(info)
            if "rows" in info:
                print("  Records:", f"{info['rows']:,}")
            if "columns" in info:
                print("  Columns:", ", ".join(c["name"] for c in info["columns"]))
            if "sheets" in info:
                for sheet in info["sheets"]:
                    print("  Sheet:", sheet["sheet"], "| Columns:", ", ".join(sheet["columns"]))
        except Exception as exc:
            failures += 1
            print("  ERROR:", str(exc))
            results.append({"filename": file.name, "error": str(exc)})

    target = ROOT / "outputs" / "private_audit" / "step22_dataset_schema.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "description": "Dataset schema inspection; not customer data",
        "files": results,
    }, indent=2), encoding="utf-8")
    print("\nReport saved:", target.relative_to(ROOT))
    print(f"RESULT: {len(files) - failures} inspected, {failures} errors")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
