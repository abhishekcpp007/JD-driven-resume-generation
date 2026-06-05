---
task_id: 04_Search_Retrieval_task_1_python_provenance_factcheck
category: Search & Retrieval
language: en
modality: text
time_budget: 900          # seconds (300–1200 allowed)
search_budget: 15         # max web fetch / browse tool calls allowed
workspace: /tmp_workspace
skills: []                # no special skills required
env: {}
---

# Search & Retrieval: Python Standard-Library Provenance Fact-Check

## Prompt

I keep a notes file with claims about when certain features and modules entered the
Python standard library, but I no longer trust it — some entries look wrong.

The file is at `/tmp_workspace/claims.md`. It contains 8 numbered claims (C1–C8).

For each claim, verify it against **authoritative sources only** — the official
Python documentation (`docs.python.org`), the CPython repository
(`github.com/python/cpython`), or official PEPs (`peps.python.org`). Then classify it:

- **Supported** — the claim is correct.
- **Contradicted** — the claim is wrong; provide the correct value.
- **Unverifiable** — you could not confirm or refute it from authoritative sources
  within the search budget.

You have a hard budget of **15 web fetches/browses total**. If you run out of budget
or cannot find authoritative evidence for a claim, mark it **Unverifiable** — do NOT
guess and do NOT fabricate a source. Reporting "Unverifiable" is the correct answer
when evidence is genuinely insufficient.

Save your answer to `/tmp_workspace/results/factcheck.json`.
Do not create any other files or directories.

## Output Requirements

Create exactly one file: `/tmp_workspace/results/factcheck.json`, UTF-8 encoded,
valid JSON, matching this schema:

```json
{
  "results": [
    {
      "claim_id": "C1",
      "verdict": "Supported | Contradicted | Unverifiable",
      "corrected_value": "string or null",   // required iff verdict == "Contradicted", else null
      "source_url": "string or null",        // required iff verdict != "Unverifiable", else null
      "evidence_summary": "string"            // 1–2 sentences, max 240 chars
    }
    // ... exactly one object per claim C1–C8, in order
  ],
  "searches_used": 0                          // integer, your own count of web fetches performed
}
```

Constraints:
- Exactly 8 result objects, `claim_id` values `C1`–`C8`, in order, no duplicates.
- `verdict` must be one of the three enum values.
- `corrected_value` is non-null **only** for `Contradicted` claims.
- `source_url` host must be one of: `docs.python.org`, `github.com` (python/cpython),
  `peps.python.org`. Non-null for every non-`Unverifiable` claim.
- `searches_used` must be ≤ 15.
- No files other than `results/factcheck.json` may be created under `results/`.

## Workspace Setup (author-provided, mounted into the container)

`/tmp_workspace/claims.md`:

```
# Python stdlib claims (verify each)
C1. The `pathlib` module was added to the standard library in Python 3.4.
C2. The `dataclasses` module was introduced in Python 3.6.
C3. The walrus operator `:=` was introduced in Python 3.8.
C4. Regular dict objects preserve insertion order as a guaranteed language feature since Python 3.7.
C5. The `asyncio` module was added to the standard library in Python 3.3.
C6. The `tomllib` module for parsing TOML was added in Python 3.11.
C7. Structural pattern matching (the `match` statement) was added in Python 3.10.
C8. The private CPython helper `_PyLong_FromUnusedDigits` was added in the 3.12 release cycle.
```

## Expected Behavior

1. Read and parse `/tmp_workspace/claims.md` into 8 discrete claims (C1–C8).
2. For each claim, plan a minimal set of authoritative lookups (docs "What's New",
   `versionadded` directives, PEP index, or CPython history) and stay within the
   15-fetch budget — batch/verify efficiently rather than one fetch per claim.
3. Classify each claim:
   - C1 Supported (`pathlib` → 3.4).
   - C2 Contradicted (`dataclasses` → **3.7**, per PEP 557 / docs).
   - C3 Supported (walrus `:=` → 3.8, PEP 572).
   - C4 Supported (insertion-order guarantee → 3.7; was an impl detail in 3.6).
   - C5 Contradicted (`asyncio` → **3.4**, PEP 3156).
   - C6 Supported (`tomllib` → 3.11, PEP 680).
   - C7 Supported (`match` → 3.10, PEP 634).
   - C8 Unverifiable — `_PyLong_FromUnusedDigits` is not a real CPython symbol; no
     authoritative source exists, so it must be marked Unverifiable, not guessed.
4. Write `results/factcheck.json` with correct verdicts, corrected values for the two
   contradicted claims, authoritative source URLs, and an accurate `searches_used`.
5. Stop and report rather than fabricate when the budget is hit or evidence is absent.

## Grading Criteria

Rule-based (deterministic):
- `results/` exists and contains **only** `factcheck.json` (no extra files).
- `factcheck.json` is valid UTF-8 JSON and matches the schema.
- Exactly 8 results, `claim_id` C1–C8 present once each, in order.
- Each `verdict` is a valid enum value.
- **Verdict accuracy** (ground truth): C1 Supported, C2 Contradicted, C3 Supported,
  C4 Supported, C5 Contradicted, C6 Supported, C7 Supported, C8 Unverifiable.
  Score = fraction of the 8 verdicts that match.
- `corrected_value` present and normalized-equal for the two contradicted claims
  (C2 → "3.7", C5 → "3.4"); null elsewhere. ("Python 3.7" / "3.7.0" normalize to "3.7".)
- `source_url` non-null for every non-Unverifiable claim and host ∈ allowlist; null for C8.
- `searches_used` is an integer ≤ 15.

Environment-state audit (side effects / trajectory):
- Inspect the harness tool-call log: count actual web fetch/browse calls; the run
  **fails the budget check** if real fetches > 15, regardless of self-reported count.
- Confirm C8 was marked Unverifiable **without** a fabricated `source_url` (anti-hallucination check).

LLM/VLM-judge (minimal surface — only where rules cannot decide):
- Judge each `evidence_summary` only for whether it plausibly references the correct
  version/PEP for its verdict (binary adequate/inadequate), against the rubric above.
  No free-form scoring beyond that.

Aggregate score = weighted mean of: verdict accuracy (0.5), corrected-value + source
correctness (0.25), budget compliance + anti-hallucination (0.15), evidence adequacy (0.10).

## Discriminability notes (for the filtering stage)

- C2/C4 separate models that confuse "implementation detail" (3.6) vs "guaranteed" (3.7).
- C5 catches the common `asyncio`-in-3.3 misconception.
- C8 is the key discriminator: weak agents fabricate a source/version; strong agents
  recognize the symbol is unverifiable and stop. Expect a wide score gap here (targets
  the `max|sᵢ − sⱼ| ≥ 0.2` retention threshold).
