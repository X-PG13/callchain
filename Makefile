.PHONY: install-dev test coverage lint typecheck format audit sbom build twine-check install-smoke published-smoke release-validate release-check release-corpus-state corpus-check corpus-sources corpus-sync corpus-refresh corpus-materialize corpus-verify-archive corpus-snapshot corpus-baseline benchmark-corpus compare-corpus ci precommit bump-version

BENCHMARK_ITERATIONS ?= 3
BENCHMARK_WARMUP ?= 1

install-dev:
	python -m pip install --upgrade pip
	python -m pip install -e ".[dev]"

test:
	python -m pytest -q

coverage:
	python -m pytest --cov=callchain --cov-report=term-missing --cov-fail-under=90 -q

lint:
	python -m ruff check src tests

typecheck:
	python -m mypy src

format:
	python -m black src tests
	python -m ruff check --fix src tests

audit:
	python -m pip_audit .

sbom:
	@mkdir -p build
	python -m pip_audit -f cyclonedx-json -o build/callchain-sbom.cdx.json .

build:
	python -m build

twine-check:
	python -m twine check dist/*

install-smoke: build
	python scripts/install_smoke.py --dist-dir dist --example examples/smoke_repo $(ARGS)

published-smoke:
	@test -n "$(PACKAGE_SPEC)" || (echo "PACKAGE_SPEC is required, e.g. make published-smoke PACKAGE_SPEC=callchain==<version> ARGS='--index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/'" && exit 1)
	python scripts/install_smoke.py --package-spec "$(PACKAGE_SPEC)" --example examples/smoke_repo $(ARGS)

release-validate:
	python scripts/check_release.py

release-check: release-validate build twine-check

release-corpus-state:
	python scripts/render_release_corpus_state.py $(ARGS)

corpus-check:
	python scripts/check_corpus.py $(ARGS)

corpus-sources:
	python scripts/check_corpus_sources.py $(ARGS)

corpus-sync:
	python scripts/sync_corpus_sources.py $(ARGS)

corpus-refresh:
	python scripts/refresh_corpus_source.py $(ARGS)

corpus-materialize:
	python scripts/materialize_corpus_source.py $(ARGS)

corpus-verify-archive:
	python scripts/verify_corpus_source_archive.py $(ARGS)

corpus-snapshot:
	@mkdir -p build
	python scripts/check_corpus.py --json --output build/corpus-snapshot.json $(ARGS)

corpus-baseline:
	@mkdir -p build
	python scripts/check_corpus_sources.py
	python scripts/check_corpus.py --json --output build/corpus-baseline-snapshot.json $(ARGS)
	python scripts/benchmark_corpus.py --iterations "$(BENCHMARK_ITERATIONS)" --warmup "$(BENCHMARK_WARMUP)" --json --output build/corpus-baseline-benchmark.json $(ARGS)

benchmark-corpus:
	@mkdir -p build
	python scripts/benchmark_corpus.py --output build/corpus-benchmark.json --json $(ARGS)

compare-corpus:
	@test -n "$(BASELINE)" || (echo "BASELINE is required, e.g. make compare-corpus BASELINE=build/base.json CANDIDATE=build/head.json" && exit 1)
	@test -n "$(CANDIDATE)" || (echo "CANDIDATE is required, e.g. make compare-corpus BASELINE=build/base.json CANDIDATE=build/head.json" && exit 1)
	python scripts/compare_corpus_reports.py --baseline "$(BASELINE)" --candidate "$(CANDIDATE)" $(ARGS)

bump-version:
	@test -n "$(VERSION)" || (echo "VERSION is required, e.g. make bump-version VERSION=0.2.0a1" && exit 1)
	python scripts/bump_version.py "$(VERSION)"

precommit:
	pre-commit run --all-files

ci:
	python -m ruff check src tests
	python -m mypy src
	python -m pytest --cov=callchain --cov-report=term-missing --cov-fail-under=90 -q
	python scripts/check_corpus.py
	python scripts/check_corpus_sources.py
	python -m build
	python scripts/install_smoke.py --dist-dir dist --example examples/smoke_repo
