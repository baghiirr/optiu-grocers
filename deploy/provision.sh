#!/bin/bash
# One-time Azure provisioning script for the Clover → OptiGrocer connector.
# Run this once per merchant. Each merchant gets their own job + secrets.
#
# Prerequisites:
#   az login
#   az extension add --name containerapp
#
# Usage:
#   bash deploy/provision.sh

set -euo pipefail

# ── Fill these in before running ──────────────────────────────────────────────

CLOVER_API_TOKEN="<paste merchant Clover token>"
CLOVER_MERCHANT_ID="<paste merchant ID>"
CLOVER_REGION="us"                          # us | eu | la

OPTI_API_KEY="<paste from .env>"
OPTI_BASE_URL="https://grocers-app.optiu.ai"

# Azure config — change SUFFIX to something unique per merchant (e.g. store name)
SUFFIX="afghgrocer"                         # lowercase, no hyphens, max 13 chars
RESOURCE_GROUP="optiu-${SUFFIX}-rg"
LOCATION="eastus"
STORAGE_ACCOUNT="optiu${SUFFIX}sa"         # globally unique, lowercase, no hyphens
FILE_SHARE="clover-data"
ACR_NAME="optiu${SUFFIX}acr"              # globally unique, alphanumeric only
ENV_NAME="optiu-${SUFFIX}-env"
JOB_NAME="optiu-${SUFFIX}-sync"
CRON_SCHEDULE="0 5 * * *"                  # 5 AM UTC daily

# ── 1. Resource group ─────────────────────────────────────────────────────────
echo "Creating resource group..."
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION"

# ── 2. Storage account + file share (persists clover_data.sqlite3 across runs) ─
echo "Creating storage..."
az storage account create \
  --name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku Standard_LRS \
  --min-tls-version TLS1_2

STORAGE_KEY=$(az storage account keys list \
  --account-name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query "[0].value" -o tsv)

az storage share create \
  --name "$FILE_SHARE" \
  --account-name "$STORAGE_ACCOUNT" \
  --account-key "$STORAGE_KEY"

# ── 3. Container Registry ─────────────────────────────────────────────────────
echo "Creating container registry..."
az acr create \
  --name "$ACR_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku Basic \
  --admin-enabled true

ACR_PASSWORD=$(az acr credential show \
  --name "$ACR_NAME" \
  --query "passwords[0].value" -o tsv)

# ── 4. Container Apps Environment ─────────────────────────────────────────────
echo "Creating Container Apps environment..."
az containerapp env create \
  --name "$ENV_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION"

az containerapp env storage set \
  --name "$ENV_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --storage-name "cloverdata" \
  --azure-file-account-name "$STORAGE_ACCOUNT" \
  --azure-file-account-key "$STORAGE_KEY" \
  --azure-file-share-name "$FILE_SHARE" \
  --access-mode ReadWrite

# ── 5. Build & push Docker image ──────────────────────────────────────────────
echo "Building and pushing image..."
az acr build \
  --registry "$ACR_NAME" \
  --image "clover-connector:latest" \
  .

# ── 6. Container App Job (scheduled) ─────────────────────────────────────────
echo "Creating scheduled job..."
IMAGE="${ACR_NAME}.azurecr.io/clover-connector:latest"

az containerapp job create \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$ENV_NAME" \
  --trigger-type "Schedule" \
  --cron-expression "$CRON_SCHEDULE" \
  --replica-timeout 1800 \
  --replica-retry-limit 2 \
  --parallelism 1 \
  --replica-completion-count 1 \
  --image "$IMAGE" \
  --cpu 0.25 \
  --memory 0.5Gi \
  --registry-server "${ACR_NAME}.azurecr.io" \
  --registry-username "$ACR_NAME" \
  --registry-password "$ACR_PASSWORD" \
  --secrets \
    "clover-token=$CLOVER_API_TOKEN" \
    "clover-mid=$CLOVER_MERCHANT_ID" \
    "opti-api-key=$OPTI_API_KEY" \
  --env-vars \
    "CLOVER_API_TOKEN=secretref:clover-token" \
    "CLOVER_MERCHANT_ID=secretref:clover-mid" \
    "CLOVER_REGION=$CLOVER_REGION" \
    "OPTI_API_KEY=secretref:opti-api-key" \
    "OPTI_BASE_URL=$OPTI_BASE_URL" \
    "CLOVER_DB_PATH=/data/clover_data.sqlite3"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "All done. Resources created in resource group: $RESOURCE_GROUP"
echo ""
echo "Run the initial backfill manually (one-time, pulls 15 months of history):"
echo "  az containerapp job start --name $JOB_NAME --resource-group $RESOURCE_GROUP"
echo ""
echo "After that it runs automatically every day at 5 AM UTC."
echo ""
echo "To update the image after code changes:"
echo "  az acr build --registry $ACR_NAME --image clover-connector:latest ."
echo "  az containerapp job update --name $JOB_NAME --resource-group $RESOURCE_GROUP --image $IMAGE"
