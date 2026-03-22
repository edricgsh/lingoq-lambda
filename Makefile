FUNCTION_NAME = lingoq-subtitle-extractor
REGION = us-east-1
ENDPOINT = http://localhost:4567

.PHONY: deploy-local test-local zip clean

deploy-local:
	@echo "Deploying to LocalStack..."
	@bash localstack-deploy.sh

test-local:
	@echo "Testing Lambda function locally..."
	awslocal lambda invoke \
		--function-name $(FUNCTION_NAME) \
		--payload file://test_event.json \
		--region $(REGION) \
		--endpoint-url $(ENDPOINT) \
		/tmp/lambda-response.json && cat /tmp/lambda-response.json

zip:
	@echo "Creating zip package..."
	@mkdir -p dist
	@pip install -r requirements.txt -t dist/package --quiet
	@cp src/handler.py dist/package/
	@cd dist/package && zip -r ../function.zip . -q
	@echo "Created dist/function.zip"

clean:
	@rm -rf dist/
	@echo "Cleaned dist directory"

docker-build:
	@echo "Building Docker image..."
	docker build -t $(FUNCTION_NAME) .

docker-run:
	@echo "Running Docker container locally..."
	docker run -p 9000:8080 $(FUNCTION_NAME)
