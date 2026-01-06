.PHONY: install install-dev clean build package help

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install package for local development
	pip install -e .

install-dev: ## Install package with development dependencies
	pip install -e ".[dev,server]"

clean: ## Clean build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf lambda-package.zip
	rm -rf .pytest_cache
	rm -rf .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

build: ## Build the package
	pip install build
	python -m build

package: clean ## Create Lambda deployment ZIP package
	@echo "Creating Lambda deployment package..."
	@mkdir -p dist/lambda-package
	@pip install -t dist/lambda-package .
	@cp -r filmdrop_titiler dist/lambda-package/
	@cd dist/lambda-package && zip -r9 ../lambda-package.zip . -x "*.pyc" "*__pycache__*" "*.dist-info/*" "tests/*"
	@echo "Lambda package created: dist/lambda-package.zip"

test: ## Run tests
	pytest

run: ## Run the application locally
	uvicorn filmdrop_titiler.main:app --reload --port 8000
