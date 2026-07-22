"""Trash (soft-delete) a Drive file using the AIE service account.

Kestra's googleworkspace plugin has no trash task, and its `drive.Delete` does a
*permanent* delete — which Shared drives restrict (`canDeleteChildren: false`).
Trashing is the Shared-drive-appropriate cleanup. Invoked from a Kestra shell
task; reads the SA key from the base64 ``SECRET_AIE_SERVICE_ACCOUNT`` env var
(the same secret Kestra hands to the googleworkspace tasks).

Imports of google libraries are lazy so the module is importable without them.
"""
import argparse
import base64
import json
import os


def trash_file(file_id: str) -> None:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    key = json.loads(base64.b64decode(os.environ["SECRET_AIE_SERVICE_ACCOUNT"]))
    creds = service_account.Credentials.from_service_account_info(
        key, scopes=["https://www.googleapis.com/auth/drive"])
    build("drive", "v3", credentials=creds).files().update(
        fileId=file_id, body={"trashed": True}, supportsAllDrives=True).execute()
    print(f"trashed {file_id}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file-id", required=True)
    args = p.parse_args()
    trash_file(args.file_id)


if __name__ == "__main__":
    main()
