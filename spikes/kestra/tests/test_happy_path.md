# Runbook: end-to-end happy-path run (real payload)

Uploads the 608 OpenAI/Vue/Supabase docs into both POC collections, promotes the
sandbox aliases, and (optionally) drops a report into Google Drive. Run against
the live stack (README "Run it", steps 1–4 complete).

## Step 1 — execute with defaults

```bash
docker compose exec kestra kestra flow execute platform_docs poc
```
Expected: all tasks green through `alias_swap` and `record_run_success`.

## Step 2 — TPM-hold / no-silent-loss check (EXACT counts)

Kestra's concurrency is flow-level, not token-aware; the OpenAI 5M-TPM ceiling is
held entirely by the in-script `--batch-size 25 --workers 2` caps + retries.
Prove it held with zero silent batch loss.

```bash
# (a) exact counts must EQUAL 608 (not merely >=)
for c in platform-docs-poc-v1 platform-docs-poc-fastembed-v1; do
  echo -n "$c: "
  curl -s -H "api-key: $QDRANT_API_KEY" -H "Content-Type: application/json" \
    -X POST "$QDRANT_API_URL/collections/$c/points/count" -d '{"exact":true}'
done
# (b) no upload task reported a dropped batch
docker compose logs kestra 2>/dev/null | grep -E 'Successful:|Failed: [1-9]' | tail -20
```
**Pass:** (a) both counts exactly `608`; (b) `Successful:` lines present, **no
`Failed: [1-9]…`** line. If short/failed, drop Supabase to `--batch-size 10
--workers 1` (CLAUDE.md Pitfall 6) and re-run. This is success criterion #1.

## Step 3 — aliases point at POC collections

```bash
curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_API_URL/aliases" | python3 -c "import sys,json
print([x for x in json.load(sys.stdin)['result']['aliases'] if x['alias_name'].endswith('-poc-active')])"
```
**Pass:** `platform-docs-poc-active -> platform-docs-poc-v1` and
`platform-docs-fastembed-poc-active -> platform-docs-poc-fastembed-v1`.

## Step 4 — telemetry complete + production untouched

```bash
psql "$PLATFORM_DOCS_DB_URL" -c \
  "select status, docs_expected, docs_uploaded, alias_swapped_at from orchestration.pipeline_runs order by started_at desc limit 1;"
curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_API_URL/aliases" | python3 -c "import sys,json
print([x for x in json.load(sys.stdin)['result']['aliases'] if x['alias_name'] in ('platform-docs','platform-docs-fastembed')])"
```
**Pass:** latest row `status=success`, `docs_uploaded=608`, `alias_swapped_at`
set; production aliases STILL point at `platform-docs-v2` /
`platform-docs-fastembed-v2`. Success criteria #1, #3, #4.

## Step 5 — Google Drive report (only if a service account is configured)

```bash
docker compose exec kestra kestra flow execute platform_docs poc --inputs '{"upload_to_drive": true}'
```
**Pass:** a file `platform-docs-poc-report-<execution-id>.md` appears in the Drive
folder `1WSgQQCMT9tgnM-108HtyXfIUZtgliBwR` with the collection counts and alias
targets. (Upload is `allowFailure: true` — if the SA is missing/misconfigured the
run still succeeds, the report just won't appear.)
