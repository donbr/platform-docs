-- Isolate Kestra's internal metadata (Kestra config targets currentSchema=kestra_system)
create schema if not exists kestra_system;

-- Custom telemetry schema
create schema if not exists orchestration;

create table if not exists orchestration.pipeline_runs (
  run_id             uuid primary key,
  flow               text not null,
  source             text,
  stage              text,          -- download | split | upload | verify | alias_swap
  status             text not null, -- running | success | failed
  environment        text not null default 'poc',  -- poc | staging | prod
  docs_expected      integer,
  docs_uploaded      integer,
  collection_version text,
  alias_swapped_at   timestamptz,
  started_at         timestamptz not null default now(),
  finished_at        timestamptz,
  error              text
);

create index if not exists idx_pipeline_runs_env_status
  on orchestration.pipeline_runs (environment, status);
create index if not exists idx_pipeline_runs_started
  on orchestration.pipeline_runs (started_at desc);
