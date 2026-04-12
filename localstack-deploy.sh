#!/bin/bash
set -e
export AWS_PAGER=""
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1

FUNCTION_NAME="lingoq-subtitle-extractor"
REGION="us-east-1"
ENDPOINT="http://localhost:4567"
SECRET_NAME="lingoq-secrets"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Deploying Lambda function: ${FUNCTION_NAME}"

# ── Seed proxy URL into LocalStack Secrets Manager ───────────────────────────
PROXY_URL="http://dezqsfeo-rotate:3280zcjc03xc@p.webshare.io:80"

echo "Seeding proxy URL into LocalStack Secrets Manager..."
"${SCRIPT_DIR}/../scripts/update-secret.sh" --local --key WEBSHARE_PROXY_URL --value "$PROXY_URL"

# Local: proxy URL is seeded for reference but USE_PROXY=false keeps requests direct
LAMBDA_ENV="Variables={WEBSHARE_PROXY_URL=$PROXY_URL,USE_PROXY=false}"

# Create deployment zip
echo "Creating deployment package..."
mkdir -p dist

# Install dependencies to a package directory
pip install -r requirements.txt -t dist/package --quiet

# Copy handler
cp src/handler.py dist/package/

# Create zip
cd dist/package
zip -r ../function.zip . -q
cd ../..

echo "Deployment package created: dist/function.zip"

if awslocal lambda get-function \
    --function-name "${FUNCTION_NAME}" \
    --region "${REGION}" \
    --endpoint-url "${ENDPOINT}" > /dev/null 2>&1; then
  echo "Updating existing Lambda function..."
  awslocal lambda update-function-code \
    --function-name "${FUNCTION_NAME}" \
    --zip-file fileb://dist/function.zip \
    --region "${REGION}" \
    --endpoint-url "${ENDPOINT}"
  awslocal lambda update-function-configuration \
    --function-name "${FUNCTION_NAME}" \
    --environment "${LAMBDA_ENV}" \
    --region "${REGION}" \
    --endpoint-url "${ENDPOINT}"
else
  echo "Creating new Lambda function..."
  awslocal lambda create-function \
    --function-name "${FUNCTION_NAME}" \
    --runtime python3.12 \
    --role "arn:aws:iam::000000000000:role/lambda-role" \
    --handler "handler.handler" \
    --zip-file fileb://dist/function.zip \
    --timeout 300 \
    --memory-size 512 \
    --environment "${LAMBDA_ENV}" \
    --region "${REGION}" \
    --endpoint-url "${ENDPOINT}"
fi

echo "Lambda function deployed successfully: ${FUNCTION_NAME}"
