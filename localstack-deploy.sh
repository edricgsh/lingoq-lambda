#!/bin/bash
set -e

FUNCTION_NAME="lingoq-subtitle-extractor"
REGION="us-east-1"
ENDPOINT="http://localhost:4567"

echo "Deploying Lambda function: ${FUNCTION_NAME}"

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
    --region "${REGION}" \
    --endpoint-url "${ENDPOINT}"
fi

echo "Lambda function deployed successfully: ${FUNCTION_NAME}"
