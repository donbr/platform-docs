# Runbook: deliberate failure test (circuit-breaker proof)

Proves the promotion gate blocks a bad alias swap and records a `failed` row.
Run against the live stack (README "Run it", steps 1–4 complete).

## Step 1 — baseline: current sandbox alias targets

```bash
curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_API_URL/aliases" | python3 -c "import sys,json
a=[x for x in json.load(sys.stdin)['result']['aliases'] if x['alias_name'].endswith('-poc-active')]
print(a or 'NONE')"
```
Note the result (`NONE` on first ever run, else prior POC targets).

## Step 2 — execute with a forced shortfall

Download/split/upload succeed, but `verify_counts` compares the actual Qdrant
count against an inflated `expected_doc_count` and exits non-zero. Pass a value
**above the current collection count** (the upload is non-idempotent, so the
count grows across runs — use a safely large number like `99999`):

```bash
# via API:
curl -s -u "$KUSER:$KPASS" -X POST \
  "http://localhost:8080/api/v1/executions/platform_docs/poc" -F "expected_doc_count=99999"
# or the CLI:
docker compose exec kestra kestra flow execute platform_docs poc --inputs '{"expected_doc_count": 99999}'
```

## Step 3 — assert the gate tripped and NO swap happened

```bash
# (a) latest pipeline_runs row is 'failed'
psql "$PLATFORM_DOCS_DB_URL" -c \
  "select status, stage, error from orchestration.pipeline_runs order by started_at desc limit 1;"
# (b) alias targets UNCHANGED from Step 1
curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_API_URL/aliases" | python3 -c "import sys,json
a=[x for x in json.load(sys.stdin)['result']['aliases'] if x['alias_name'].endswith('-poc-active')]
print(a or 'NONE')"
```

**Pass:** (a) `status=failed`; (b) alias targets identical to Step 1 (no swap).
This is success criterion #2.
