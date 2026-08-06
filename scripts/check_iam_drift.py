#!/usr/bin/env python3
"""
GCP IAM Policy Drift Checker
-----------------------------
For each resource listed in projects.yaml:
  1. Load the desired-state policy JSON from the repo.
  2. Fetch the live policy from GCP via `gcloud`.
  3. Normalize both and compare.
  4. Log "No change - <timestamp>" if identical, or
     "Policy is different" + send an alert if not.

Requires: gcloud CLI authenticated (via Workload Identity Federation in CI,
or `gcloud auth login` locally), PyYAML, requests.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import requests
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS_FILE = os.path.join(REPO_ROOT, "projects.yaml")

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")  # set as a GitHub secret


def load_config():
    with open(PROJECTS_FILE, "r") as f:
        config = yaml.safe_load(f)
    return config.get("projects", [])


def load_desired_policy(policy_file):
    path = os.path.join(REPO_ROOT, policy_file)
    with open(path, "r") as f:
        return json.load(f)


def fetch_live_policy(resource_type, resource_id):
    """Call gcloud to get the current IAM policy for a project/folder/org."""
    type_to_command = {
        "project": ["gcloud", "projects", "get-iam-policy", resource_id],
        "folder": ["gcloud", "resource-manager", "folders", "get-iam-policy", resource_id],
        "organization": ["gcloud", "organizations", "get-iam-policy", resource_id],
    }
    if resource_type not in type_to_command:
        raise ValueError(f"Unknown resource type: {resource_type}")

    cmd = type_to_command[resource_type] + ["--format=json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"gcloud failed for {resource_type} {resource_id}: {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def normalize(policy):
    """Strip volatile fields (etag, version) and sort bindings/members so
    that ordering differences don't register as false drift."""
    bindings = policy.get("bindings", [])
    normalized = []
    for b in bindings:
        entry = {
            "role": b["role"],
            "members": sorted(b.get("members", [])),
        }
        if "condition" in b:
            entry["condition"] = b["condition"]
        normalized.append(entry)
    normalized.sort(key=lambda b: (b["role"], json.dumps(b.get("condition", {}), sort_keys=True)))
    return normalized


def diff_summary(desired, live):
    """Produce a short human-readable summary of what changed."""
    desired_by_role = {b["role"]: set(b["members"]) for b in desired}
    live_by_role = {b["role"]: set(b["members"]) for b in live}

    lines = []
    all_roles = set(desired_by_role) | set(live_by_role)
    for role in sorted(all_roles):
        d_members = desired_by_role.get(role, set())
        l_members = live_by_role.get(role, set())
        added = l_members - d_members       # present live, not in desired
        removed = d_members - l_members     # in desired, missing live
        if role not in desired_by_role:
            lines.append(f"+ New role in live policy: {role} ({', '.join(sorted(l_members))})")
        elif role not in live_by_role:
            lines.append(f"- Role removed from live policy: {role} (was: {', '.join(sorted(d_members))})")
        else:
            if added:
                lines.append(f"~ {role}: unexpected member(s) added: {', '.join(sorted(added))}")
            if removed:
                lines.append(f"~ {role}: expected member(s) missing: {', '.join(sorted(removed))}")
    return lines


def send_slack_alert(resource_id, summary_lines):
    if not SLACK_WEBHOOK_URL:
        print("WARNING: SLACK_WEBHOOK_URL not set, skipping Slack alert.", file=sys.stderr)
        return
    text = f":rotating_light: *IAM policy drift detected* on `{resource_id}`\n"
    text += "\n".join(f"• {line}" for line in summary_lines) if summary_lines else "(no detail available)"
    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"WARNING: failed to send Slack alert: {e}", file=sys.stderr)

    # --- To alert via email instead/also, add an SMTP or SendGrid call here ---


def main():
    resources = load_config()
    if not resources:
        print("No resources configured in projects.yaml", file=sys.stderr)
        sys.exit(1)

    overall_drift_found = False
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    for resource in resources:
        resource_type = resource["type"]
        resource_id = resource["id"]
        policy_file = resource["policy_file"]

        try:
            desired_raw = load_desired_policy(policy_file)
            live_raw = fetch_live_policy(resource_type, resource_id)
        except Exception as e:
            print(f"ERROR checking {resource_type} {resource_id}: {e}", file=sys.stderr)
            overall_drift_found = True
            continue

        desired_norm = normalize(desired_raw)
        live_norm = normalize(live_raw)

        if desired_norm == live_norm:
            print(f"No change - {now} ({resource_type}: {resource_id})")
        else:
            print(f"Policy is different ({resource_type}: {resource_id})")
            summary = diff_summary(desired_norm, live_norm)
            for line in summary:
                print(f"  {line}")
            send_slack_alert(resource_id, summary)
            overall_drift_found = True

    # Non-zero exit code makes the drift visible as a failed GitHub Actions run too
    sys.exit(1 if overall_drift_found else 0)


if __name__ == "__main__":
    main()
