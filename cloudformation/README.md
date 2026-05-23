# S3 cross-account migration (AWS DataSync)

CloudFormation for copying ten source buckets using **agentless** S3-to-S3 DataSync in **ap-south-1**.

| Source | Destination |
|--------|-------------|
| opc-cloud-cb | opc-rm-cloud-cb |
| opc-cloud-s3 | opc-rm-cloud-s3 |
| opc-cloud-s3-dev | opc-rm-cloud-s3-dev |
| opc-cloud-s3-test | opc-rm-cloud-s3-test |
| opc-cloud-s3-uat | opc-rm-cloud-s3-uat |
| opc-ds3 | opc-rm-ds3 |
| opc-ps3 | opc-rm-ps3 |
| onedios-prod | od-crm-prod |
| onedios-qa | od-crm-qa |
| onedios-uat | od-uat |

## Templates

| Template | Purpose |
|----------|---------|
| `datasync-tasks.yaml` | DataSync locations and tasks (**requires existing IAM role**) |
| `datasync-iam-role.yaml` | Optional — creates the IAM role if you do not have one |

The DataSync stack does **not** use cross-stack exports, stack sets, or `ImportValue`. You pass the IAM role **directly** at deploy time.

## Deploy DataSync (existing IAM role)

Use **either** a full role ARN **or** a role name in the same account.

### Option A — role ARN

```bash
aws cloudformation deploy \
  --template-file cloudformation/datasync-tasks.yaml \
  --stack-name opc-s3-datasync-tasks \
  --parameter-overrides \
      DataSyncAccessRoleArn=arn:aws:iam::ACCOUNT_ID:role/YOUR_ROLE_NAME \
      DataSyncAccessRoleName="" \
      AwsRegion=ap-south-1 \
      CreateDestinationBuckets=false \
      StartTaskExecutionOnCreate=false \
  --region ap-south-1
```

### Option B — role name only

```bash
aws cloudformation deploy \
  --template-file cloudformation/datasync-tasks.yaml \
  --stack-name opc-s3-datasync-tasks \
  --parameter-overrides \
      DataSyncAccessRoleArn="" \
      DataSyncAccessRoleName=YOUR_ROLE_NAME \
      AwsRegion=ap-south-1 \
  --region ap-south-1
```

The role must trust `datasync.amazonaws.com` and have S3 access to all source and destination buckets.

## Optional — create IAM role via CloudFormation

```bash
aws cloudformation deploy \
  --template-file cloudformation/datasync-iam-role.yaml \
  --stack-name opc-s3-datasync-iam \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-south-1
```

Then deploy tasks with `DataSyncAccessRoleName=opc-s3-datasync-iam-datasync-s3` (or copy the ARN from stack output `DataSyncRoleArn` into `DataSyncAccessRoleArn`).

## Start copies

```bash
aws cloudformation describe-stacks \
  --stack-name opc-s3-datasync-tasks \
  --query "Stacks[0].Outputs[?OutputKey=='TaskArns'].OutputValue" \
  --output text \
  --region ap-south-1 | tr ',' '\n' | while read -r TASK; do
  aws datasync start-task-execution --task-arn "$TASK" --region ap-south-1
done
```

## Source account bucket policy

Use `source-bucket-policy-snippet.json` on each source bucket (update account ID and role name).

## Helper scripts (Python)

Install dependencies once:

```bash
pip install -r cloudformation/requirements-aws.txt
```

Run from the `cloudformation/` directory (shared `migration_config.py`):

```bash
cd cloudformation
```

### Compare source vs destination object counts

```bash
python compare_s3_bucket_counts.py \
  --source-profile source-account \
  --dest-profile dest-account \
  --region ap-south-1

# Optional total bytes (slower)
python compare_s3_bucket_counts.py --compare-bytes --source-profile source --dest-profile dest
```

### Set existing tasks to “changed data only” (`TransferMode=CHANGED`)

```bash
python set_datasync_transfer_mode_changed.py --region ap-south-1

# Preview
python set_datasync_transfer_mode_changed.py --dry-run

# Explicit task ARNs
python set_datasync_transfer_mode_changed.py --task-arn arn:aws:datasync:... --task-arn arn:aws:datasync:...
```

Reads task ARNs from stack output `TaskArns` (default stack `opc-s3-datasync-tasks`), or resolves tasks by CFN task name.

## Parameter files

- `parameters-datasync.example.json` — pass role ARN
- `parameters-datasync-by-role-name.example.json` — pass role name only
- `parameters-iam.example.json` — optional IAM stack
