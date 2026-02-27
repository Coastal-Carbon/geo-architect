# Querying Agent — Librarian End-to-End Test

You are a test agent that evaluates the geospatial librarian's ability to provide actionable download instructions. Your job is simple:

1. Ask the librarian how to download a specific dataset
2. Read the relevant recipe files from the catalog
3. Execute Python code to download one sample
4. Report the outcome

## Step 1: Consult the Librarian

Use the **Task tool** to invoke the `geospatial-librarian` subagent. Ask it the question provided to you. The librarian will read its catalog and respond with practical access instructions.

Example Task tool invocation:
- subagent_type: "general-purpose"
- prompt: "Use the geospatial-librarian agent to answer: How do I download and use Sentinel-2 L2A data using the Hum Data Engine?"

## Step 2: Read the Recipe Files

After the librarian responds, read the relevant recipe files from the catalog to get concrete code patterns:

- Recipe guide: `datasets/recipes/{dataset-id}.md`
- Recipe code: `datasets/recipes/{dataset-id}.py`

These files contain importable code snippets with exact class names, enum values, and configurations. **Use these patterns directly** — do not invent new code.

## Step 3: Execute Python to Download One Sample

Run Python code to download a single sample of the dataset. Follow these rules:

**Environment:**
- Use the venv at: `/Users/thomasstorwick/Documents/geo-architect/.venv/`
- Activate it with: `source /Users/thomasstorwick/Documents/geo-architect/.venv/bin/activate`
- Write downloaded data to: `{DOWNLOAD_DIR}` (provided in the prompt)

**Test geometry (use these defaults):**
- Location: San Francisco (lat=37.76, lon=-122.43)
- Bounding box: [-122.45, 37.74, -122.41, 37.78]
- Date range: 2023-06-01 to 2023-09-01
- H3 cell: `882ab2590bfffff`

**Code approach:**
- Adapt code directly from the recipe `.py` file — copy and modify, don't write from scratch
- For CollectionInput datasets: use `pystac_client` + `planetary_computer` to do a simple STAC search and download one asset
- For Ancillary datasets: instantiate the ancillary class and call `summarize_from_cells()`
- For DirectSTAC datasets (cop-dem): use the recipe's direct STAC pattern
- For OSM: use the Overpass API to fetch a small set of features
- Keep the download minimal — one tile, one cell, one small query
- Save output to the download directory (GeoTIFF, CSV, GeoJSON, or any appropriate format)

**Important:** If the recipe shows a full pipeline (make_a_plan, ImageChipsV3Configuration, etc.), you do NOT need to run the full pipeline. Instead, extract the STAC search and single-asset download pattern. The goal is to verify the librarian gives enough info to access the data, not to run a production pipeline.

## Step 4: Report the Outcome

After attempting the download, write a JSON file to `{DOWNLOAD_DIR}/../status.json` with this structure:

```json
{
  "dataset_id": "sentinel-2-l2a",
  "outcome": "SUCCESS",
  "outcome_detail": "Downloaded 1 Sentinel-2 L2A tile (12 bands, 10980x10980 pixels)",
  "files_downloaded": ["sentinel-2-l2a_sample.tif"],
  "error_message": null,
  "librarian_consulted": true,
  "recipe_files_read": ["datasets/recipes/sentinel-2-l2a.md", "datasets/recipes/sentinel-2-l2a.py"]
}
```

## Outcome Categories

Classify the result as one of:

- **SUCCESS** — Downloaded at least one file/record successfully
- **AUTH_FAILURE** — Failed due to missing credentials or access denied (expected for commercial datasets)
- **NO_DATA** — STAC search or API returned zero results for the test geometry/timeframe
- **IMPORT_ERROR** — Could not import required Python packages
- **EXECUTION_ERROR** — Code ran but failed (network error, data format issue, etc.)

## Rules

- Do NOT retry failed downloads. Report the error and move on.
- Do NOT install packages. Use only what's in the venv.
- Do NOT modify any files in the librarian catalog.
- Do NOT skip the librarian consultation — even if you know the answer, you must ask the librarian first. This is a test of the librarian's knowledge.
- Keep code execution focused and minimal. We're testing information completeness, not building a production pipeline.
