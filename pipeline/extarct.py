import logging
from ingestion.models import PipelineState
from ingestion.pdf_parser import parse_pdf
from ingestion.web_scrapper import scrape_sanfoundry
from ingestion.schema_mapper import to_schema_dict, batch

log = logging.getLogger(__name__)

SANFOUNDRY_HOST = "sanfoundry.com"

def is_sanfoundry(source: str) -> bool:
    return SANFOUNDRY_HOST in source

def extract_and_batch(state: PipelineState) -> PipelineState:
    """
    Router node — dispatches to the right extractor based on source,
    then maps to schema and batches. Output shape is always identical
    so enrich_batch never needs to care where data came from.
    """
    source     = state["source"]
    batch_size = state.get("batch_size", 50)

    # ── Route ───────────────────────────────────────────
    if is_sanfoundry(source):
        log.info(f"Source identified as Sanfoundry — using web scraper")
        raw_questions = scrape_sanfoundry(source)
    else:
        log.info(f"Source identified as PDF — using file parser")
        raw_questions = parse_pdf(source)

    # ── Map + batch (identical regardless of source) ────
    if raw_questions is None:
        raw_questions = []

    mapped  = [to_schema_dict(q) for q in raw_questions]
    batches = batch(mapped, size=batch_size)
    resume_idx = max(0, int(state.get("resume_batch_idx", 0)))
    current_batch_idx = min(resume_idx, len(batches))

    log.info(
        f"Extracted {len(mapped)} questions from '{source}' "
        f"→ {len(batches)} batches of {batch_size}"
    )

    return {
        **state,
        "batches":           batches,
        "current_batch_idx": current_batch_idx,
        "enriched":          [],
        "failed":            [],
        "retry_count":       0,
    }