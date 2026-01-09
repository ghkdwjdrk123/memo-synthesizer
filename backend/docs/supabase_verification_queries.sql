-- ============================================================================
-- Supabase Verification Queries for Step 2
-- ============================================================================
-- Copy and paste these queries into Supabase SQL Editor to verify Step 2 results
-- https://zqrbrddmwrpogabizton.supabase.co

-- ----------------------------------------------------------------------------
-- 1. Total thought_units count
-- ----------------------------------------------------------------------------
SELECT
    COUNT(*) as total_thought_units,
    COUNT(embedding) as embeddings_present,
    COUNT(*) - COUNT(embedding) as null_embeddings
FROM thought_units;

-- Expected: 10 total, 10 embeddings, 0 null


-- ----------------------------------------------------------------------------
-- 2. Thought extraction statistics per raw_note
-- ----------------------------------------------------------------------------
SELECT
    rn.title as raw_note_title,
    COUNT(tu.id) as thought_count,
    MIN(tu.extracted_at) as first_extracted,
    MAX(tu.extracted_at) as last_extracted
FROM raw_notes rn
LEFT JOIN thought_units tu ON rn.id = tu.raw_note_id
GROUP BY rn.id, rn.title
ORDER BY thought_count DESC;

-- Expected: 5 notes with 1-3 thoughts each


-- ----------------------------------------------------------------------------
-- 3. Sample thought_units with full details
-- ----------------------------------------------------------------------------
SELECT
    tu.id,
    tu.claim,
    tu.context,
    tu.embedding_model,
    tu.extracted_at,
    rn.title as source_note_title,
    rn.notion_url as source_url
FROM thought_units tu
JOIN raw_notes rn ON tu.raw_note_id = rn.id
ORDER BY tu.id
LIMIT 5;

-- Expected: 5 records with claims, contexts, and source information


-- ----------------------------------------------------------------------------
-- 4. Embedding dimension check
-- ----------------------------------------------------------------------------
-- Note: This requires parsing the string embedding as array
-- Embeddings are stored as text representation of array
SELECT
    id,
    LEFT(embedding::text, 50) as embedding_preview,
    LENGTH(embedding::text) as embedding_string_length,
    embedding_model
FROM thought_units
LIMIT 1;

-- Expected: String length ~19,000 chars, model = 'text-embedding-3-small'


-- ----------------------------------------------------------------------------
-- 5. Data quality checks
-- ----------------------------------------------------------------------------
-- Check for invalid or missing data
SELECT
    'Total records' as check_type,
    COUNT(*)::text as result
FROM thought_units
UNION ALL
SELECT
    'Records with null claim' as check_type,
    COUNT(*)::text as result
FROM thought_units
WHERE claim IS NULL
UNION ALL
SELECT
    'Records with null embedding' as check_type,
    COUNT(*)::text as result
FROM thought_units
WHERE embedding IS NULL
UNION ALL
SELECT
    'Records with invalid raw_note_id' as check_type,
    COUNT(*)::text as result
FROM thought_units tu
LEFT JOIN raw_notes rn ON tu.raw_note_id = rn.id
WHERE rn.id IS NULL
UNION ALL
SELECT
    'Average claim length' as check_type,
    ROUND(AVG(LENGTH(claim)))::text as result
FROM thought_units;

-- Expected: All checks should pass (0 null/invalid records)


-- ----------------------------------------------------------------------------
-- 6. Preview of potential pairs for Step 3
-- ----------------------------------------------------------------------------
-- Show which thoughts will be compared in Step 3
SELECT
    a.id as thought_a_id,
    LEFT(a.claim, 60) || '...' as claim_a,
    b.id as thought_b_id,
    LEFT(b.claim, 60) || '...' as claim_b,
    1 - (a.embedding <=> b.embedding) as cosine_similarity
FROM thought_units a
CROSS JOIN thought_units b
WHERE a.id < b.id
ORDER BY cosine_similarity DESC
LIMIT 10;

-- Expected: 10 pairs with similarity scores
-- Note: This will show top 10 most similar pairs


-- ----------------------------------------------------------------------------
-- 7. Similarity distribution for Step 3
-- ----------------------------------------------------------------------------
-- Count pairs in different similarity ranges
WITH pair_similarities AS (
    SELECT
        1 - (a.embedding <=> b.embedding) as similarity
    FROM thought_units a
    CROSS JOIN thought_units b
    WHERE a.id < b.id
)
SELECT
    'High similarity (>= 0.7)' as range_type,
    COUNT(*) as pair_count
FROM pair_similarities
WHERE similarity >= 0.7
UNION ALL
SELECT
    'Weak connections (0.3-0.7)' as range_type,
    COUNT(*) as pair_count
FROM pair_similarities
WHERE similarity >= 0.3 AND similarity < 0.7
UNION ALL
SELECT
    'Low similarity (< 0.3)' as range_type,
    COUNT(*) as pair_count
FROM pair_similarities
WHERE similarity < 0.3
UNION ALL
SELECT
    'Total pairs' as range_type,
    COUNT(*) as pair_count
FROM pair_similarities;

-- Expected: Total = 45 pairs, distribution varies


-- ----------------------------------------------------------------------------
-- 8. Raw notes with their thought counts
-- ----------------------------------------------------------------------------
SELECT
    rn.id,
    rn.title,
    rn.notion_page_id,
    rn.imported_at,
    COUNT(tu.id) as extracted_thoughts,
    ARRAY_AGG(tu.id ORDER BY tu.id) as thought_ids
FROM raw_notes rn
LEFT JOIN thought_units tu ON rn.id = tu.raw_note_id
GROUP BY rn.id, rn.title, rn.notion_page_id, rn.imported_at
ORDER BY COUNT(tu.id) DESC;

-- Expected: 5 raw notes, total 10 thoughts


-- ----------------------------------------------------------------------------
-- 9. Most recent extractions
-- ----------------------------------------------------------------------------
SELECT
    tu.id,
    LEFT(tu.claim, 80) || '...' as claim_preview,
    tu.extracted_at,
    rn.title as source_note
FROM thought_units tu
JOIN raw_notes rn ON tu.raw_note_id = rn.id
ORDER BY tu.extracted_at DESC
LIMIT 5;

-- Expected: Most recent 5 thoughts extracted on 2026-01-08


-- ----------------------------------------------------------------------------
-- 10. Verification summary
-- ----------------------------------------------------------------------------
SELECT
    'Step 2 Verification' as summary_type,
    json_build_object(
        'total_thoughts', (SELECT COUNT(*) FROM thought_units),
        'total_raw_notes', (SELECT COUNT(DISTINCT raw_note_id) FROM thought_units),
        'null_embeddings', (SELECT COUNT(*) FROM thought_units WHERE embedding IS NULL),
        'avg_thoughts_per_note', (SELECT ROUND(COUNT(*)::numeric / COUNT(DISTINCT raw_note_id), 2) FROM thought_units),
        'oldest_extraction', (SELECT MIN(extracted_at) FROM thought_units),
        'newest_extraction', (SELECT MAX(extracted_at) FROM thought_units),
        'status', CASE
            WHEN (SELECT COUNT(*) FROM thought_units) = 10
             AND (SELECT COUNT(*) FROM thought_units WHERE embedding IS NULL) = 0
            THEN 'PASSED ✓'
            ELSE 'FAILED ✗'
        END
    ) as verification_data;

-- Expected: Status = 'PASSED ✓'


-- ============================================================================
-- Notes:
-- ============================================================================
-- 1. The <=> operator is pgvector's cosine distance operator
-- 2. Cosine similarity = 1 - cosine distance
-- 3. Similarity range for weak connections: 0.3 to 0.7
-- 4. Total possible pairs: C(10,2) = 45 pairs
-- 5. Embeddings stored as text (JSON array) in vector(1536) column
-- ============================================================================
