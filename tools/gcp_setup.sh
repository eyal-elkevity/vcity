#!/usr/bin/env bash
set -euo pipefail

PROJECT=vcity-512721
PROJECT_NUMBER=250602775916
REGION=us-west1
REPO=eyal-elkevity/vcity
REGISTRY=vcity
POOL=github
PROVIDER=vcity-main
RUN_SA="vcity-run@${PROJECT}.iam.gserviceaccount.com"
DEPLOY_SA="vcity-deploy@${PROJECT}.iam.gserviceaccount.com"
SECRETS=(ANTHROPIC_API_KEY VOYAGE_API_KEY DEEPGRAM_API_KEY LINEAR_API_KEY GAP_LOG_SALT ACCESS_CODE)

cd "$(dirname "$0")/.."

gcloud services enable run artifactregistry secretmanager iamcredentials sts --project "$PROJECT"

gcloud artifacts repositories describe "$REGISTRY" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REGISTRY" --repository-format docker \
    --location "$REGION" --project "$PROJECT"
gcloud artifacts repositories set-cleanup-policies "$REGISTRY" --location "$REGION" --project "$PROJECT" \
  --policy tools/registry_cleanup.json --no-dry-run >/dev/null

for account in vcity-run vcity-deploy; do
  gcloud iam service-accounts describe "${account}@${PROJECT}.iam.gserviceaccount.com" \
    --project "$PROJECT" >/dev/null 2>&1 ||
    gcloud iam service-accounts create "$account" --project "$PROJECT"
done

for secret in "${SECRETS[@]}"; do
  gcloud secrets describe "$secret" --project "$PROJECT" >/dev/null 2>&1 ||
    gcloud secrets create "$secret" --replication-policy automatic --project "$PROJECT"
  gcloud secrets add-iam-policy-binding "$secret" --project "$PROJECT" \
    --member "serviceAccount:${RUN_SA}" --role roles/secretmanager.secretAccessor >/dev/null
done

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:${DEPLOY_SA}" --role roles/run.admin >/dev/null
gcloud artifacts repositories add-iam-policy-binding "$REGISTRY" --location "$REGION" --project "$PROJECT" \
  --member "serviceAccount:${DEPLOY_SA}" --role roles/artifactregistry.writer >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$RUN_SA" --project "$PROJECT" \
  --member "serviceAccount:${DEPLOY_SA}" --role roles/iam.serviceAccountUser >/dev/null

gcloud iam workload-identity-pools describe "$POOL" --location global --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create "$POOL" --location global --project "$PROJECT"
gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --workload-identity-pool "$POOL" --location global --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
    --workload-identity-pool "$POOL" --location global --project "$PROJECT" \
    --issuer-uri https://token.actions.githubusercontent.com \
    --attribute-mapping google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref \
    --attribute-condition "assertion.repository=='${REPO}' && assertion.ref=='refs/heads/main'"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --project "$PROJECT" \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}" \
  --role roles/iam.workloadIdentityUser >/dev/null

echo "WIF_PROVIDER=projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "DEPLOY_SA=${DEPLOY_SA}"
echo "RUN_SA=${RUN_SA}"
