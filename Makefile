.PHONY: help bootstrap install fetch publish mcpb index audit fixture test test-node conformance lint smoke verify eval report model-context clean

help:
	@echo "bootstrap one command: venv, install, prebuilt index, client, verify"
	@echo "install   install the package with dev and eval extras"
	@echo "fetch     download a prebuilt index instead of building one"
	@echo "publish   (maintainer) write dist/manifest.json + gzipped artifacts"
	@echo "mcpb      (maintainer) build dist/binomen.mcpb, the double-click install"
	@echo "index     download NCBI taxdump and build both index artifacts"
	@echo "audit     report what is in the archive without building anything"
	@echo "fixture   build the small synthetic index used by the tests (no network)"
	@echo "test      run both test suites (Python and Node)"
	@echo "conformance  regenerate the cross-language fixture after a rules change"
	@echo "lint      ruff"
	@echo "smoke     exercise all ten tools against the built index"
	@echo "verify    check case ground truth against the authorities (do this before eval)"
	@echo "eval      run the case set in both conditions (needs ANTHROPIC_API_KEY)"
	@echo "report    render the most recent run"
	@echo "model-context  regenerate docs/MODEL-CONTEXT-*.md from the shipped text"

bootstrap:
	python scripts/bootstrap.py

install:
	pip install -e ".[eval,dev]"

fetch:
	binomen-fetch-index

publish:
	binomen-fetch-index --publish dist/

mcpb:
	python scripts/build_mcpb.py

index:
	binomen-build-index

audit:
	binomen-build-index --audit

fixture:
	python tests/fixtures/make_fixture.py
	binomen-build-index --fixture tests/fixtures/taxdump \
	  --out data/fixture.sqlite --stage1-out data/fixture-stage1.sqlite --version fixture-v2

test:
	python -m pytest tests -q
	cd node && node --test --no-warnings

test-node:
	cd node && node --test --no-warnings

conformance:
	python scripts/emit_conformance.py

lint:
	ruff check src eval scripts tests

smoke:
	python scripts/smoke.py

verify:
	python eval/verify_cases.py --live --write

eval:
	python eval/runner.py --condition both

report:
	python eval/report.py $$(ls -t eval/runs/run-*.jsonl | head -1) --out eval/runs/report.md

# Run this in the same commit as any edit to node/src/tool_descriptions.js.
# The docs carry the text fingerprint, so a stale one does not merely go out of
# date -- it labels itself with a hash that no longer describes it.
model-context:
	node scripts/show_model_context.js --md terse > docs/MODEL-CONTEXT-terse.md
	node scripts/show_model_context.js --md conditional > docs/MODEL-CONTEXT-conditional.md
	node scripts/show_model_context.js --md unconditional > docs/MODEL-CONTEXT-unconditional.md

clean:
	rm -rf data/*.sqlite data/cache .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
