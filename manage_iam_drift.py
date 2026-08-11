#!/usr/bin/env python3
"""
IAM Drift Review & Remediation Tool
-------------------------------------
Interactive companion to check_iam_drift.py. Instead of just reporting
drift, this walks through EACH individual (role, member) difference and
lets you decide, one at a time:

  [a] approve  - the live change is fine; update the repo's desired-state
                 JSON to match it (accept drift as the new baseline)
  [r] revert   - undo the change; push the desired-state policy's version
                 of this binding back onto the live GCP policy
  [s] skip     - leave both sides alone for now

Nothing is applied until you confirm at the end. Defaults to --dry-run.

Usage:
  python scripts/manage_iam_drift.py                 # dry run, interactive review
  python scripts/manage_iam_drift.py --apply          # actually apply your decisions
  python scripts/manage_iam_drift.py --project my-project-1   # skip project selection
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS_FILE = os.path.join(REPO_ROOT, "projects.yaml")


def load_config():
    with open(PROJECTS_FILE, "r") as f:
        config = yaml.safe_load(f)
    return config.get("projects", [])


def load_desired_policy(policy_file):
    path = os.path.join(REPO_ROOT, policy_file)
    with open(path, "r") as f:
        return json.load(f)


def fetch_live_policy(resource_type, resource_id):
    type_to_command = {
        "project": ["gcloud", "projects", "get-iam-policy", resource_id],
        "folder": ["gcloud", "resource-manager", "folders", "get-iam-policy", resource_id],
        "organization": ["gcloud", "organizations", "get-iam-policy", resource_id],
    }
    cmd = type_to_command[resource_type] + ["--format=json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gcloud failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def set_live_policy(resource_type, resource_id, policy):
    """Writes the given policy to a temp file and applies it with gcloud,
    which handles the etag-based optimistic concurrency check for us."""
    type_to_command = {
        "project": ["gcloud", "projects", "set-iam-policy", resource_id],
        "folder": ["gcloud", "resource-manager", "folders", "set-iam-policy", resource_id],
        "organization": ["gcloud", "organizations", "set-iam-policy", resource_id],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(policy, f, indent=2)
        temp_path = f.name
    try:
        cmd = type_to_command[resource_type] + [temp_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"gcloud set-iam-policy failed: {result.stderr.strip()}")
    finally:
        os.unlink(temp_path)


def condition_key(binding):
    """Returns a hashable key for a binding's condition (or None)."""
    if "condition" in binding:
        return json.dumps(binding["condition"], sort_keys=True)
    return None


def build_binding_map(bindings):
    """(role, condition_key) -> set(members)"""
    m = {}
    for b in bindings:
        key = (b["role"], condition_key(b))
        m.setdefault(key, set()).update(b.get("members", []))
    return m


def map_to_bindings(binding_map):
    bindings = []
    for (role, cond_key), members in binding_map.items():
        if not members:
            continue
        b = {"role": role, "members": sorted(members)}
        if cond_key is not None:
            b["condition"] = json.loads(cond_key)
        bindings.append(b)
    bindings.sort(key=lambda b: (b["role"], b.get("condition", {}) and json.dumps(b["condition"])))
    return bindings


def compute_changes(desired_bindings, live_bindings):
    """Returns a list of individual (role, condition, member) differences."""
    desired_map = build_binding_map(desired_bindings)
    live_map = build_binding_map(live_bindings)
    changes = []
    for key in sorted(set(desired_map) | set(live_map), key=lambda k: k[0]):
        role, cond_key = key
        d_members = desired_map.get(key, set())
        l_members = live_map.get(key, set())
        for member in sorted(d_members - l_members):
            changes.append({"role": role, "condition": cond_key, "member": member, "status": "missing_in_live"})
        for member in sorted(l_members - d_members):
            changes.append({"role": role, "condition": cond_key, "member": member, "status": "extra_in_live"})
    return changes


def describe(change):
    if change["status"] == "extra_in_live":
        return f"LIVE HAS EXTRA:  {change['member']}  has  {change['role']}  (not in repo's desired state)"
    else:
        return f"LIVE IS MISSING: {change['member']}  should have  {change['role']}  (per repo's desired state)"


def review_changes(changes):
    decisions = []
    print(f"\n{len(changes)} individual difference(s) found.\n")
    for i, change in enumerate(changes, 1):
        print(f"[{i}/{len(changes)}] {describe(change)}")
        while True:
            choice = input("  (a)pprove into repo / (r)evert on GCP / (s)kip / (q)uit review: ").strip().lower()
            if choice in ("a", "r", "s"):
                decisions.append((change, choice))
                break
            elif choice == "q":
                print("Stopping review early. Decisions made so far will still be applied.")
                return decisions
            else:
                print("  Please enter a, r, s, or q.")
    return decisions


def apply_approvals(desired_raw, decisions):
    """Approved changes update the repo's desired-state bindings."""
    desired_map = build_binding_map(desired_raw.get("bindings", []))
    changed = False
    for change, choice in decisions:
        if choice != "a":
            continue
        key = (change["role"], change["condition"])
        if change["status"] == "extra_in_live":
            desired_map.setdefault(key, set()).add(change["member"])
        else:  # missing_in_live -> accept that it's gone
            desired_map[key].discard(change["member"])
        changed = True
    if not changed:
        return None
    return {"bindings": map_to_bindings(desired_map)}


def apply_reverts(live_raw, decisions):
    """Reverted changes update the live GCP policy to match the repo."""
    live_map = build_binding_map(live_raw.get("bindings", []))
    changed = False
    for change, choice in decisions:
        if choice != "r":
            continue
        key = (change["role"], change["condition"])
        if change["status"] == "extra_in_live":
            live_map.setdefault(key, set()).discard(change["member"])
        else:  # missing_in_live -> add it back to live
            live_map.setdefault(key, set()).add(change["member"])
        changed = True
    if not changed:
        return None
    new_policy = {"bindings": map_to_bindings(live_map)}
    if "etag" in live_raw:
        new_policy["etag"] = live_raw["etag"]
    if "version" in live_raw:
        new_policy["version"] = live_raw["version"]
    return new_policy


def maybe_git_commit_and_push(policy_file, apply_flag):
    if not apply_flag:
        return
    if subprocess.run(["git", "-C", REPO_ROOT, "rev-parse", "--is-inside-work-tree"],
                       capture_output=True).returncode != 0:
        print("(Not inside a git repo -- skipping commit/push. Commit the change manually.)")
        return
    do_push = input("Commit and push this change to GitHub now? [y/N]: ").strip().lower()
    if do_push != "y":
        print("Left the file changed locally, uncommitted.")
        return
    subprocess.run(["git", "-C", REPO_ROOT, "add", policy_file], check=True)
    subprocess.run(["git", "-C", REPO_ROOT, "commit", "-m", f"Approve IAM drift into desired state: {policy_file}"], check=True)
    subprocess.run(["git", "-C", REPO_ROOT, "push"], check=True)
    print("Pushed.")


def select_resource(args):
    resources = load_config()
    if args.project:
        matches = [r for r in resources if r["id"] == args.project]
        if not matches:
            print(f"'{args.project}' not found in projects.yaml", file=sys.stderr)
            sys.exit(1)
        return matches[0]
    if len(resources) == 1:
        return resources[0]
    print("Multiple resources configured. Pick one:")
    for i, r in enumerate(resources, 1):
        print(f"  [{i}] {r['type']}: {r['id']}")
    idx = int(input("Enter number: ").strip())
    return resources[idx - 1]


def main():
    parser = argparse.ArgumentParser(description="Review and remediate IAM drift interactively.")
    parser.add_argument("--project", help="Resource id from projects.yaml to review (skips selection prompt)")
    parser.add_argument("--apply", action="store_true", help="Actually apply decisions. Without this, only a plan is shown.")
    args = parser.parse_args()

    resource = select_resource(args)
    resource_type, resource_id, policy_file = resource["type"], resource["id"], resource["policy_file"]

    print(f"Reviewing {resource_type}: {resource_id} against {policy_file}")
    desired_raw = load_desired_policy(policy_file)
    live_raw = fetch_live_policy(resource_type, resource_id)

    changes = compute_changes(desired_raw.get("bindings", []), live_raw.get("bindings", []))
    if not changes:
        print("No drift. Nothing to review.")
        return

    decisions = review_changes(changes)

    approved_count = sum(1 for _, c in decisions if c == "a")
    reverted_count = sum(1 for _, c in decisions if c == "r")
    print(f"\nSummary: {approved_count} approved into repo, {reverted_count} to revert on GCP, "
          f"{len(decisions) - approved_count - reverted_count} skipped.")

    if not args.apply:
        print("\n[DRY RUN] No changes applied. Re-run with --apply to actually make these changes.")
        return

    new_desired = apply_approvals(desired_raw, decisions)
    if new_desired is not None:
        full_path = os.path.join(REPO_ROOT, policy_file)
        with open(full_path, "w") as f:
            json.dump(new_desired, f, indent=2)
            f.write("\n")
        print(f"Updated {policy_file} with approved drift.")
        maybe_git_commit_and_push(policy_file, args.apply)

    new_live = apply_reverts(live_raw, decisions)
    if new_live is not None:
        confirm = input(f"About to run set-iam-policy on {resource_type} {resource_id}. Type 'yes' to confirm: ").strip()
        if confirm == "yes":
            set_live_policy(resource_type, resource_id, new_live)
            print("Live policy updated.")
        else:
            print("Skipped applying to GCP.")


if __name__ == "__main__":
    main()
