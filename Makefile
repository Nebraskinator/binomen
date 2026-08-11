.PHONY: help install index fixture test lint smoke verify eval report clean

help:
	@echo "install   install the package with dev and eval extras"
	@echo "index     download NCBI taxdump and build the backbone index (~1 GB download)"
	@echo "fixture   build the small synthetic index used by the tests (no network)"
	@echo "test      run the test suite"
	@echo "lint      ruff"
	@echo "smoke     exercise all eight tools against the built index"
	@echo "verify    check case ground truth against the authorities (do this before eval)"
	@echo "eval      run the case set in both conditions (needs ANTHROPIC_API_KEY)"
	@echo "report    render the most recent run"

install:
	pip install -e ".[eval,dev]"

index:
	binomen-build-index

fixture:
	python tests/fixtures/make_fixture.py
	binomen-build-index --fixture tests/fixtures/taxdump --out data/fixture.sqlite --version fixture-v1

test:
	python -m pytest tests -q

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

clean:
	rm -rf data/*.sqlite data/cache .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
