"""Shared bucket pairs and DataSync task names for S3 migration scripts."""

BUCKET_PAIRS: list[tuple[str, str]] = [
    ("opc-cloud-cb", "opc-rm-cloud-cb"),
    ("opc-cloud-s3", "opc-rm-cloud-s3"),
    ("opc-cloud-s3-dev", "opc-rm-cloud-s3-dev"),
    ("opc-cloud-s3-test", "opc-rm-cloud-s3-test"),
    ("opc-cloud-s3-uat", "opc-rm-cloud-s3-uat"),
    ("opc-ds3", "opc-rm-ds3"),
    ("opc-ps3", "opc-rm-ps3"),
    ("onedios-prod", "od-crm-prod"),
    ("onedios-qa", "od-crm-qa"),
    ("onedios-uat", "od-uat"),
]

CFN_TASK_NAMES: list[str] = [
    "opc-cloud-cb-to-opc-rm-cloud-cb",
    "opc-cloud-s3-to-opc-rm-cloud-s3",
    "opc-cloud-s3-dev-to-opc-rm-cloud-s3-dev",
    "opc-cloud-s3-test-to-opc-rm-cloud-s3-test",
    "opc-cloud-s3-uat-to-opc-rm-cloud-s3-uat",
    "opc-ds3-to-opc-rm-ds3",
    "opc-ps3-to-opc-rm-ps3",
    "onedios-prod-to-od-crm-prod",
    "onedios-qa-to-od-crm-qa",
    "onedios-uat-to-od-uat",
]

DEFAULT_REGION = "ap-south-1"
DEFAULT_STACK_NAME = "s3-replication-via-datasync"

DATASYNC_TASK_OPTIONS = {
    "TransferMode": "CHANGED",
    "VerifyMode": "ONLY_FILES_TRANSFERRED",
    "OverwriteMode": "ALWAYS",
    "Atime": "BEST_EFFORT",
    "Mtime": "PRESERVE",
    "PreserveDeletedFiles": "PRESERVE",
    "PreserveDevices": "NONE",
    "PosixPermissions": "NONE",
    "TaskQueueing": "ENABLED",
}
