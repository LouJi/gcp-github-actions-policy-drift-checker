# Workload Identity  Federation (WIF) Instructions

### 1. Enable APIs
```bash
gcloud services enable \
iam.googleapis.com \
iamcredentials.googleapis.com \
sts.googleapis.com \
cloudresourcemanager.googleapis.com \
--project=[PROJECT_ID]


### 2. Create the Workload Identity Pool

This is the container that will hold GitHub as a trusted external identity source. You can replace "github-pool" with another name. Keep in mind that you will have to use that same name throughout this instructional where ever
```bash
gcloud iam workload-identity-pools create github-pool \
  --project=[PROJECT ID] \
  --location="global" \
  --display-name="GitHub Actions Pool"
  ```
  
### 3. Create the Provider inside that pool

This tells GCP to trust tokens issued by GitHub's OIDC endpoint, and the "--attribute-condition" restricts it to only your repo, so no other GitHub repo in the world can use this trust relationship even if they somehow got the provider name. "github-provider" can also be replaced by a name of your choosing.
```bash
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --project=[PROJECT_ID] \
  --location="global" \
  --workload-identity-pool=github-pool \
  --display-name="GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition="assertion.repository_owner == [GITHUB \
  --issuer-uri="https://token.actions.githubusercontent.com"
 ```
  
### 4. Allow your specific GitHub repo to impersonate that service account
This is the actual trust link — it says "tokens asserting they came from your-org/gcp-iam-drift may act as this service account, and nothing else may."
```bash
gcloud iam service-accounts add-iam-policy-binding "[Service Account Name] @[PROJECT ID].iam.gserviceaccount.com" \
  --project=[PROJECT ID] \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/[PROJECT NUMBER]/locations/global/workloadIdentityPools/github-pool/attribute.repository/[GITHUB REPO]"
```

### 5. Grant the service account the actual permission it needs (read-only IAM policy access on the tracked project — same as before, included here for completeness)
```bash
gcloud projects add-iam-policy-binding [PROJECT ID] \
  --member="serviceAccount:[Service Account Name]@[PROJECT ID].iam.gserviceaccount.com" \
  --role="roles/iam.securityReviewer"
```

### 6. Get the full provider resource name — this is the exact string GitHub needs
```bash
gcloud iam workload-identity-pools providers describe github-provider \
  --project=[PROJECT ID] \
  --location="global" \
  --workload-identity-pool=github-pool \
  --format="value(name)"
```
the command should return a string like this: 
projects/[PROJECT NUMBER]/locations/global/workloadIdentityPools/github-pool/providers/github-provider

### 7. Add two GitHub repo secrets

In your repo, go to: Settings → Secrets and variables → Actions → New repository secret

WIF_PROVIDER → the full string from step 6
WIF_SERVICE_ACCOUNT → iam-drift-checker@YOUR_PROJECT_ID.iam.gserviceaccount.com
