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
PROVIDER_CONDITION="assertion.repository=='${REPO}' && assertion.ref=='refs/heads/main'"
SECRETS=(ANTHROPIC_API_KEY VOYAGE_API_KEY DEEPGRAM_API_KEY LINEAR_API_KEY GAP_LOG_SALT ACCESS_CODE TWILIO_AUTH_TOKEN TRANSFER_TO)

cd "$(dirname "$0")/.."

gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com --project "$PROJECT"

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
    --member "serviceAccount:${RUN_SA}" --role roles/secretmanager.secretAccessor --condition=None >/dev/null
done

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:${DEPLOY_SA}" --role roles/run.admin --condition=None >/dev/null
gcloud artifacts repositories add-iam-policy-binding "$REGISTRY" --location "$REGION" --project "$PROJECT" \
  --member "serviceAccount:${DEPLOY_SA}" --role roles/artifactregistry.writer --condition=None >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$RUN_SA" --project "$PROJECT" \
  --member "serviceAccount:${DEPLOY_SA}" --role roles/iam.serviceAccountUser --condition=None >/dev/null

gcloud iam workload-identity-pools describe "$POOL" --location global --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create "$POOL" --location global --project "$PROJECT"
gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --workload-identity-pool "$POOL" --location global --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
    --workload-identity-pool "$POOL" --location global --project "$PROJECT" \
    --issuer-uri https://token.actions.githubusercontent.com \
    --attribute-mapping google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref \
    --attribute-condition "$PROVIDER_CONDITION"
gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" \
  --workload-identity-pool "$POOL" --location global --project "$PROJECT" \
  --attribute-condition "$PROVIDER_CONDITION" >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --project "$PROJECT" \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}" \
  --role roles/iam.workloadIdentityUser --condition=None >/dev/null

echo "WIF_PROVIDER=projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "DEPLOY_SA=${DEPLOY_SA}"
echo "RUN_SA=${RUN_SA}"
