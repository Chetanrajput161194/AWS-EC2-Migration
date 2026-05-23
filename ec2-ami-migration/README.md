# EC2 AMI Migration Automation

Production-oriented Python tooling that runs on a source-account EC2 instance. It discovers EC2 instances in a region, creates AMIs in parallel, monitors completion, shares each AMI and its EBS snapshots with a target account as soon as it is available, sends per-AMI SNS alerts immediately, and sends a final summary when all work finishes.

## Features

- Automatic discovery of **running** and **stopped** instances (terminated and other states are skipped)
- Parallel AMI creation and post-available sharing via `ThreadPoolExecutor`
- Continuous AMI status polling with configurable interval
- Per-AMI SNS notification as soon as sharing completes (or on failure)
- Final SNS summary when all instances reach a terminal state
- Resume from `migration_state.json` — completed AMIs are not recreated
- Retries for throttling and transient AWS errors
- Console and file logging with progress summaries

## Requirements

- Python 3.12+
- IAM permissions on the source account instance role (see below)
- SNS topic in the configured region

## Quick start

```bash
cd ec2-ami-migration
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.yaml.sample config.yaml
# Edit config.yaml with your account ID, SNS ARN, and settings
python migrate.py
```

Dry run (discovery only):

```bash
python migrate.py --dry-run
```

Verbose logging:

```bash
python migrate.py -v
```

## Configuration

Copy `config.yaml.sample` to `config.yaml`.

| Key | Description |
|-----|-------------|
| `target_account_id` | AWS account ID that receives AMI and snapshot shares |
| `sns_topic_arn` | SNS topic for per-AMI and summary notifications |
| `region` | Region to scan (e.g. `ap-south-1`) |
| `max_parallel_workers` | Thread pool size for create/share work |
| `polling_interval_seconds` | Seconds between AMI status checks (minimum 5) |
| `ami_name_prefix` | Optional prefix before `migration-<name>-<timestamp>` |
| `state_file` | JSON state file for resume |
| `log_file` | Log file path |
| `no_reboot` | Pass `NoReboot` to `CreateImage` when `true` |
| `retry_failed_on_resume` | Re-attempt instances marked `failed` on restart |

### AMI naming

```
[ami_name_prefix]migration-<instance-name>-<YYYYMMDD-HHMMSS>
```

Example: `migration-web-server-01-20250523-143022`

### Tags on AMIs and snapshots

- `Name` — AMI name
- `MigrationSourceInstanceId`
- `MigrationSourceInstanceName`
- `MigrationManagedBy` — `ec2-ami-migration`
- `MigrationTargetAccount`
- `MigrationCreatedAt` — UTC ISO timestamp

## SNS message format

**Per-instance** (`event_type: ami_migration_instance`):

```json
{
  "instance_name": "web-01",
  "instance_id": "i-0abc123",
  "ami_id": "ami-0def456",
  "status": "success",
  "failure_reason": null,
  "snapshot_ids": ["snap-..."],
  "region": "ap-south-1",
  "target_account_id": "123456789012"
}
```

`status` is `success`, `failure`, or the internal workflow status. `failure_reason` is set when applicable.

**Summary** (`event_type: ami_migration_summary`):

Includes `progress` counts and a list of all instances with final status.

## Resume behavior

State is persisted after each instance update. On restart:

- **completed** — skipped (no new AMI)
- **creating** / **available** — monitoring and sharing continue for the existing `ami_id`
- **failed** — skipped unless `retry_failed_on_resume: true`

Interrupt safely with `Ctrl+C`; re-run `python migrate.py` to resume.

## IAM policy (source account)

Attach to the EC2 instance profile running this script:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeImages",
        "ec2:DescribeSnapshots",
        "ec2:CreateImage",
        "ec2:ModifyImageAttribute",
        "ec2:ModifySnapshotAttribute",
        "ec2:CreateTags"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "sns:Publish",
      "Resource": "arn:aws:sns:ap-south-1:ACCOUNT_ID:your-topic-name"
    }
  ]
}
```

Replace the SNS ARN with your topic. Target account must accept shared AMIs (no extra action required for standard cross-account AMI copy workflows).

## Project layout

```
ec2-ami-migration/
├── migrate.py              # CLI entry point
├── requirements.txt
├── config.yaml.sample
├── README.md
└── ami_migration/
    ├── config.py
    ├── discovery.py
    ├── ami_ops.py
    ├── notifications.py
    ├── orchestrator.py
    ├── state.py
    ├── retry.py
    └── logging_setup.py
```

## Operational notes

- Run during a maintenance window if `no_reboot: false` (default), since instances may reboot during imaging.
- Large fleets increase API call volume; tune `max_parallel_workers` and `polling_interval_seconds` if you hit throttling (retries are built in).
- Copy the entire project directory to the migration EC2 host, including the `ami_migration` package.
- Exit code `0` — all instances completed; `1` — one or more failures; `2` — config error; `130` — interrupted.

## Target account

After sharing, in the target account use **EC2 → AMIs → Shared with me** or copy the AMI into the target region/account as per your migration runbook.
