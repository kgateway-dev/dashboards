# imports should be after the set up flags so are lower

# https://www.gnu.org/software/make/manual/html_node/Special-Variables.html#Special-Variables
.DEFAULT_GOAL := help

#----------------------------------------------------------------------------------
# Help
#----------------------------------------------------------------------------------
# Our Makefile is quite large, and hard to reason through
# `make help` can be used to self-document targets
# To update a target to be self-documenting (and appear with the `help` command),
# place a comment after the target that is prefixed by `##`. For example:
#	custom-target: ## comment that will appear in the documentation when running `make help`
#
# **NOTE TO DEVELOPERS**
# As you encounter make targets that are frequently used, please make them self-documenting
.PHONY: help
help: NAME_COLUMN_WIDTH=35
help: LINE_COLUMN_WIDTH=5
help: ## Output the self-documenting make targets
	@grep -hnE '^[%a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = "[:]|(## )"}; {printf "\033[36mL%-$(LINE_COLUMN_WIDTH)s%-$(NAME_COLUMN_WIDTH)s\033[0m %s\n", $$1, $$2, $$4}'

#----------------------------------------------------------------------------------
# Base
#----------------------------------------------------------------------------------

ROOTDIR := $(shell pwd)

export IMAGE_REGISTRY ?= ghcr.io/kgateway-dev

# A semver resembling 1.0.1-dev. Most calling GHA jobs customize this. Exported for use in goreleaser.yaml.
VERSION ?= 1.0.1-dev
export VERSION

TEST_ASSET_DIR ?= $(ROOTDIR)/_test

.PHONY: init-git-hooks
init-git-hooks:  ## Use the tracked version of Git hooks from this repo
	git config core.hooksPath .githooks

#----------------------------------------------------------------------------
# Analyze
#----------------------------------------------------------------------------

.PHONY: analyze
analyze:  lint-charts ## Run linters

#----------------------------------------------------------------------------------
# Test
#----------------------------------------------------------------------------------

PYTHON ?= python3
PROMTOOL ?= promtool

.PHONY: test-dashboard-queries
test-dashboard-queries: ## Test dashboard queries with promtool
	PROMTOOL="$(PROMTOOL)" $(PYTHON) -B -m unittest discover -s tests -p 'test_*.py' -v

#----------------------------------------------------------------------------------
# Clean
#----------------------------------------------------------------------------------

.PHONY: clean
clean: ## Clean the project
	rm -rf _output
	rm -rf _test
	git clean -f -X install

#----------------------------------------------------------------------------------
# Helm
#----------------------------------------------------------------------------------

HELM ?= helm
HELM_PACKAGE_ARGS ?= --version $(VERSION)
HELM_CHART_DIR_DASHBOARDS=install/helm/kgateway-dashboards

.PHONY: package-charts
package-charts: package-kgateway-dashboards-chart ## Package the charts

.PHONY: package-kgateway-dashboards-chart
package-kgateway-dashboards-chart: ## Package the kgateway dashboards chart
	mkdir -p $(TEST_ASSET_DIR); \
	$(HELM) package $(HELM_PACKAGE_ARGS) --destination $(TEST_ASSET_DIR) $(HELM_CHART_DIR_DASHBOARDS); \
	$(HELM) repo index $(TEST_ASSET_DIR);

.PHONY: release-charts
release-charts: package-charts ## Release the charts
	$(HELM) push $(TEST_ASSET_DIR)/kgateway-dashboards-$(VERSION).tgz oci://$(IMAGE_REGISTRY)/charts

.PHONY: deploy-kgateway-dashboards-chart
deploy-kgateway-dashboards-chart: ## Deploy the kgateway dashboards chart
	$(HELM) upgrade --install kgateway-dashboards $(TEST_ASSET_DIR)/kgateway-dashboards-$(VERSION).tgz --namespace kgateway-system --create-namespace

.PHONY: lint-charts
lint-charts: ## Lint the charts
	$(HELM) lint $(HELM_CHART_DIR_DASHBOARDS)

#----------------------------------------------------------------------------------
# Printing makefile variables utility
#----------------------------------------------------------------------------------

# use `make print-MAKEFILE_VAR` to print the value of MAKEFILE_VAR

print-%  : ; @echo $($*)
