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

## Step 2 — execute with a forced shortfall (expected=999)

Download/split/upload succeed (608 real docs land), but `verify_counts` compares
608 against 999 and exits non-zero.

```bash
docker compose exec kestra kestra flow execute platform_docs poc --inputs '{"expected_doc_count": 999}'
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
