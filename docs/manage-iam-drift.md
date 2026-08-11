# Reviewing & Remediating IAM Drift

`scripts/manage_iam_drift.py` is a companion to the daily automated check
(`scripts/check_iam_drift.py`). Where the daily job only **reports** drift,
this is an interactive tool a human runs to **decide what to do about it**,
one difference at a time.

For each individual (role, member) difference between the live GCP policy
and the repo's desired-state JSON, you choose:

- **(a)pprove** — the live change is fine; update the repo's JSON to match
  reality (accept the drift as the new baseline)
- **(r)evert** — undo it; push the repo's version of that binding back onto
  the live GCP policy
- **(s)kip** — leave both sides alone for now

Nothing is applied until you confirm — it defaults to a dry run.

## Usage

```bash
# See the plan, decide item by item, apply nothing yet
python scripts/manage_iam_drift.py

# Actually apply the decisions you made
python scripts/manage_iam_drift.py --apply

# Skip the "which project?" prompt if you track more than one
python scripts/manage_iam_drift.py --project my-project-1 --apply
```

Example prompt:
```
[1/4] LIVE HAS EXTRA:  user:someone@example.com  has  roles/editor  (not in repo's desired state)
  (a)pprove into repo / (r)evert on GCP / (s)kip / (q)uit review:
```

## Permissions required

This is the one tool in the repo that needs **write** access — everything
else (the daily checker, this tool's own read step) only needs read access.

| Action | Permission needed | Role that grants it |
|---|---|---|
| Reading live policy | `resourcemanager.projects.getIamPolicy` | `roles/iam.securityReviewer` |
| Writing live policy (a "revert" decision) | `resourcemanager.projects.setIamPolicy` | `roles/resourcemanager.projectIamAdmin` |

Grant it to whoever is allowed to run this tool — not to the automated
daily-check service account, which should stay read-only permanently:
```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="user:the-engineer@example.com" \
  --role="roles/resourcemanager.projectIamAdmin"
```

`roles/resourcemanager.projectIamAdmin` is scoped to IAM policy only — it
can't touch billing, compute, storage, etc. Prefer it over `roles/owner`
for this purpose. Remember, least privilege. 

## What "approve" and "revert" actually touch

- **Approve** only rewrites the local policy JSON file. It does not touch
  GCP because the change(s) already exist on GCP. It then asks (separately)
  whether to `git commit` + `git push` the change for you.
- **Revert** only rewrites the *live* GCP policy, via
  `gcloud ... set-iam-policy`. It preserves the policy's `etag`, which is
  GCP's built-in protection against overwriting a change someone else made
  in the meantime. It asks for a typed `yes` confirmation, separate from
  `--apply`, since this is the more consequential half.

## A caveat on IAM Conditions

The diff groups bindings by **(role, condition)** together, matching how
GCP itself treats them — so a conditional and unconditional grant of the
same role are always shown as separate items.

This means an *edit* to an existing condition (rewording, a changed CEL
expression, a new expiry date) doesn't show up as one change — it shows up
as two: the member going *missing* under the old condition, and *extra*
under the new one. Approving or reverting only one of that pair can leave
the member listed under both conditions in your desired-state JSON.

**Before running `--apply` on anything involving Conditions**, skim the
diff for a member/role that appears as both missing and extra — that's
almost always one condition edit, not two independent changes, and both
lines should be approved or reverted together.

If your bindings don't use Conditions, this doesn't apply to you.
