# Backup Directory

This directory stores backup exports and comparison reports for thought extraction experiments.

## Files

- `thought_units_haiku_YYYYMMDD_HHMMSS.json` - Export of Haiku extraction results
- `thought_units_sonnet_YYYYMMDD_HHMMSS.json` - Export of Sonnet 4.5 extraction results (optional)
- `comparison_haiku_vs_sonnet_YYYYMMDD_HHMMSS.json` - Comparison analysis report

## JSON Structure

### Thought Export Format

```json
{
  "metadata": {
    "exported_at": "2026-01-08T12:34:56",
    "model": "claude-3-haiku-20240307",
    "source_table": "thought_units",
    "statistics": {
      "total_count": 10,
      "with_context_count": 10,
      "without_context_count": 0,
      "avg_claim_length": 58.0,
      "avg_context_length": 45.3,
      "context_usage_rate": 1.0,
      "unique_raw_notes": 5
    }
  },
  "thoughts": [
    {
      "id": 1,
      "raw_note_id": "uuid-here",
      "raw_note_title": "Note title",
      "raw_note_url": "https://notion.so/...",
      "claim": "Thought claim text",
      "context": "Additional context",
      "claim_length": 58,
      "context_length": 45,
      "has_embedding": true,
      "embedding_model": "text-embedding-3-small",
      "extracted_at": "2026-01-08T12:00:00"
    }
  ]
}
```

### Comparison Report Format

```json
{
  "haiku": {
    "model": "Haiku",
    "total_count": 10,
    "with_context_count": 10,
    "context_usage_rate": 1.0,
    "avg_claim_length": 58.0,
    "avg_context_length": 45.3,
    "unique_raw_notes": 5,
    "thoughts_per_note": 2.0
  },
  "sonnet": {
    "model": "Sonnet 4.5",
    "total_count": 15,
    "with_context_count": 14,
    "context_usage_rate": 0.93,
    "avg_claim_length": 72.5,
    "avg_context_length": 58.1,
    "unique_raw_notes": 5,
    "thoughts_per_note": 3.0
  },
  "differences": {
    "thought_count_diff": 5,
    "context_usage_diff": -0.07,
    "avg_claim_length_diff": 14.5,
    "thoughts_per_note_diff": 1.0
  }
}
```

## Usage

See `/Users/hwangjeongtae/Desktop/develop_project/notion_idea_synthesizer/memo-synthesizer/backend/BACKUP_COMPARISON_GUIDE.md` for complete workflow.

## Retention Policy

- Keep backup files for at least 30 days
- Archive old comparisons after selecting winning model
- Delete only after confirming new extraction is stable
