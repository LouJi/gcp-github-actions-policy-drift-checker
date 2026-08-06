# gcp-github-actions-policy-drift-checker
Uses GitHub Actions to check a GCP project's policies to make it was not changed from an ideal state.
# GCP IAM Policy Drift Checker

Daily job that compares your GCP project's live IAM policy against a
desired-state JSON file checked into this repo. Logs "No change" when
they match, logs "Policy is different" and alerts on Slack when they don't.

## How it works

1. `.github/workflows/iam-drift-check.yml` runs on a daily schedule (and
   can be triggered manually).
2. It authenticates to GCP using **Workload Identity Federation** (no
   long-lived service account keys).
3. `scripts/check_iam_drift.py` reads `projects.yaml`, and for each
   listed resource:
   - loads the desired policy from `policies/<file>.json`
   - fetches the live policy with `gcloud ... get-iam-policy`
   - normalizes both (ignores `etag`, sorts bindings/members) and compares
   - logs the result, and posts to Slack if there's drift

## One-time setup

### 1. Create a GCP service account for this job
```bash
gcloud iam service-accounts create iam-drift-checker \
  --display-name="IAM Drift Checker"
```

Grant it permission to **read** IAM policy on each project you're tracking
(this does NOT need write access):
```bash
gcloud projects add-iam-policy-binding [PROJECT_ID] \
  --member="serviceAccount:iam-drift-checker@[PROJECT_ID].iam.gserviceaccount.com" \
  --role="roles/iam.securityReviewer"
```
(`roles/iam.securityReviewer` includes `resourcemanager.projects.getIamPolicy`.
For folder/org scope, grant the equivalent role at that level instead.)

### 2. Set up Workload Identity Federation (WIF)
This lets GitHub Actions authenticate as that service account without a
downloaded key. Follow [WIF instructions](https://github.com/LouJi/gcp-github-actions-policy-drift-checker/edit/main/wif-instructions.md).


You'll end up with two values to store as GitHub repo secrets:
- `WIF_PROVIDER` — the full provider resource name
- `WIF_SERVICE_ACCOUNT` — `iam-drift-checker@YOUR_PROJECT_ID.iam.gserviceaccount.com`

### 3. Add a Slack alert webhook
Create an "Incoming Webhook" in Slack, then add it as a repo secret:
- `SLACK_WEBHOOK_URL`

(Want email instead/also? There's a marked spot in
`scripts/check_iam_drift.py` — `send_slack_alert()` — to add an SMTP or
SendGrid call.)

### 4. Set your desired-state policy
Replace the contents of `policies/my-project-1.json` with your project's
actual desired IAM bindings. Easiest way to get a starting point:
```bash
gcloud projects get-iam-policy YOUR_PROJECT_ID --format=json > policies/my-project-1.json
```
Then remove the `etag` field (the script ignores it anyway, but it's noise)
and edit it to reflect the policy you *want*, not necessarily what's live
today.

### 5. Update `projects.yaml`
Set `id` to your real project ID and confirm `policy_file` points at the
right JSON file.

### 6. Adjust the schedule
Edit the `cron` line in `.github/workflows/iam-drift-check.yml` to your
preferred time (cron is UTC, no DST auto-adjustment).

## Adding more projects later
Just append another entry to `projects.yaml` and add a matching
`policies/<name>.json` file — the script loops over the whole list
automatically, and the GCP service account needs the same read role
granted on each additional project (or folder/org).

## Testing locally
Use the following command to test manually.
```bash
gcloud auth application-default login
pip install -r requirements.txt
python scripts/check_iam_drift.py
```
