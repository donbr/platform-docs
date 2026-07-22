"""Emit the dynamic expected doc count as a Kestra output var.

Kestra's scripts plugin captures a ``::{"outputs": {...}}::`` line from stdout
into the task's outputs, so downstream JDBC tasks can reference
``{{ outputs.compute_expected.vars.count }}`` for accurate telemetry.
"""
import json

from spikes.kestra import poc_config


def kestra_output_line(count: int) -> str:
    return "::" + json.dumps({"outputs": {"count": count}}) + "::"


def main():
    n = poc_config.expected_doc_count(poc_config.POC_SOURCES)
    print(f"expected_doc_count={n}")
    print(kestra_output_line(n))


if __name__ == "__main__":
    main()
