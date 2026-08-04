# Workload Identity  Federation (WIF) Instrustions

### 1. Create the Workload Identity Pool

This is the container that will hold GitHub as a trusted external identity source.
```bash
gcloud iam workload-identity-pools create "${POOL_ID}" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --display-name="GitHub Actions Pool"
  ```
  
### 2. Create the Provider inside that pool

This tells GCP to trust tokens issued by GitHub's OIDC endpoint, and — importantly — the --attribute-condition restricts it to only your repo, so no other GitHub repo in the world can use this trust relationship even if they somehow got the provider name.
```bash
gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --workload-identity-pool="${POOL_ID}" \
  --display-name="GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition="assertion.repository_owner == '${GITHUB_REPO%%/*}'" \
  --issuer-uri="https://token.actions.githubusercontent.com"
 ```
  
### 3. Create the service account (skip if you already made iam-drift-checker from the earlier setup)
 ```bash
 gcloud iam service-accounts create "${SA_NAME}" \
  --project="${PROJECT_ID}" \
  --display-name="IAM Drift Checker"
 ```
 
### 4. Allow your specific GitHub repo to impersonate that service account
This is the actual trust link — it says "tokens asserting they came from your-org/gcp-iam-drift may act as this service account, and nothing else may."
```bash
gcloud iam service-accounts add-iam-policy-binding \
  "${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project="${PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GITHUB_REPO}"
```

### 5. Grant the service account the actual permission it needs (read-only IAM policy access on the tracked project — same as before, included here for completeness)
```bash
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/iam.securityReviewer"
```

### 6. Get the full provider resource name — this is the exact string GitHub needs
```bash
gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --workload-identity-pool="${POOL_ID}" \
  --format="value(name)"
```
the command should return a string like this: 
projects/[PROJECT_NUMBER]/locations/global/workloadIdentityPools/github-pool/providers/github-provider

### 7. Add two GitHub repo secrets

In your repo: Settings → Secrets and variables → Actions → New repository secret

WIF_PROVIDER → the full string from step 6
WIF_SERVICE_ACCOUNT → iam-drift-checker@YOUR_PROJECT_ID.iam.gserviceaccount.com
