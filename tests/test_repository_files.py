"""Tests for repository governance and automation files."""

from __future__ import annotations

from pathlib import Path
import re

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


def test_release_workflow_has_publish_and_attestation_steps():
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "actions/upload-artifact@v4" in workflow
    assert "actions/attest-build-provenance@v3" in workflow
    assert "actions: read" in workflow
    assert "actions/github-script@v7" in workflow
    assert "context.payload.release.target_commitish || context.payload.repository.default_branch" in workflow
    assert 'latestSuccessfulRun("corpus-baseline.yml", "corpus-baseline-", targetBranch)' in workflow
    assert 'latestSuccessfulRun("corpus-baseline-compare.yml", "corpus-baseline-compare-", targetBranch)' in workflow
    assert 'latestSuccessfulRun("corpus-baseline-refresh.yml", "corpus-baseline-refresh-", targetBranch)' in workflow
    assert "params.branch = branchName" in workflow
    assert "target_branch: targetBranch" in workflow
    assert "listWorkflowRunArtifacts" in workflow
    assert "artifact_id" in workflow
    assert "artifact_url" in workflow
    assert "artifact_expired" in workflow
    assert "actions/runs/${successful.id}/artifacts/${artifact.id}" in workflow
    assert "build/release-corpus-baseline-state.json" in workflow
    assert "build/release-corpus-baseline-state.md" in workflow
    assert "{ target_branch: targetBranch, baseline, compare, refresh }" in workflow
    assert "python scripts/render_release_corpus_state.py \\" in workflow
    assert 'COMPARE_RUN_ID="$(python - <<\'PY\'' in workflow
    assert 'COMPARE_ARTIFACT_NAME="$(python - <<\'PY\'' in workflow
    assert 'gh run download "$COMPARE_RUN_ID" --dir build/release-compare --pattern "$COMPARE_ARTIFACT_NAME"' in workflow
    assert 'find build/release-compare -name \'corpus-baseline-compare.json\' -print -quit' in workflow
    assert 'find build/release-compare -name \'corpus-baseline-compare.md\' -print -quit' in workflow
    assert 'cmd=(python scripts/render_release_corpus_state.py --state build/release-corpus-baseline-state.json)' in workflow
    assert 'cmd+=(--compare-report "$COMPARE_REPORT")' in workflow
    assert 'cmd+=(--compare-markdown "$COMPARE_MARKDOWN")' in workflow
    assert 'cmd+=(--json --output build/release-corpus-baseline-state.json)' in workflow
    assert '--state build/release-corpus-baseline-state.json \\' in workflow
    assert "--markdown \\" in workflow
    assert '--output build/release-corpus-baseline-state.md' in workflow
    assert 'cat build/release-corpus-baseline-state.md >> "$GITHUB_STEP_SUMMARY"' in workflow
    assert "name: release-corpus-state" in workflow
    assert "id: upload-release-corpus-state" in workflow
    assert "id: upload-release-dist" in workflow
    assert "--release-notes \\" in workflow
    assert '--release-tag "$RELEASE_TAG" \\' in workflow
    assert '--workflow-run-url "$WORKFLOW_RUN_URL" \\' in workflow
    assert '--state-artifact-url "$STATE_ARTIFACT_URL" \\' in workflow
    assert '--dist-artifact-url "$DIST_ARTIFACT_URL" \\' in workflow
    assert "build/release-corpus-audit.md" in workflow
    assert 'cat build/release-corpus-audit.md >> "$GITHUB_STEP_SUMMARY"' in workflow
    assert "callchain-release-corpus-audit" in workflow
    assert "context.payload.release.id" in workflow
    assert "github.rest.repos.getRelease" in workflow
    assert "github.rest.repos.updateRelease" in workflow
    assert "--corpus-baseline-state build/release-corpus-baseline-state.json" in workflow
    assert "subject-path: dist/*" in workflow
    assert "attestations: write" in workflow
    assert "artifact-metadata: write" in workflow
    assert "contents: write" in workflow
    assert "python scripts/check_release.py \\" in workflow
    assert '--expected-tag "$RELEASE_TAG" \\' in workflow


def test_dependabot_configuration_covers_python_and_actions():
    config = Path(".github/dependabot.yml").read_text(encoding="utf-8")

    assert 'package-ecosystem: "pip"' in config
    assert 'package-ecosystem: "github-actions"' in config
    assert 'interval: "weekly"' in config


def test_repository_automation_workflows_cover_dependency_review_and_triage():
    dependency_review = Path(".github/workflows/dependency-review.yml").read_text(encoding="utf-8")
    release_drafter = Path(".github/workflows/release-drafter.yml").read_text(encoding="utf-8")
    autolabeler = Path(".github/workflows/pr-autolabeler.yml").read_text(encoding="utf-8")
    label_sync = Path(".github/workflows/label-sync.yml").read_text(encoding="utf-8")
    stale = Path(".github/workflows/stale.yml").read_text(encoding="utf-8")

    assert "actions/dependency-review-action@v4" in dependency_review
    assert "release-drafter/release-drafter@v7" in release_drafter
    assert "release-drafter/release-drafter/autolabeler@v7" in autolabeler
    assert "EndBug/label-sync@v2" in label_sync
    assert "actions/stale@v10" in stale
    assert "days-before-issue-stale: 45" in stale
    assert "days-before-pr-close: 14" in stale
    assert "repo-token: ${{ secrets.GITHUB_TOKEN }}" in stale


def test_security_workflows_cover_codeql_dependency_audit_and_sbom_export():
    codeql = Path(".github/workflows/codeql.yml").read_text(encoding="utf-8")
    security_audit = Path(".github/workflows/security-audit.yml").read_text(encoding="utf-8")

    assert "github/codeql-action/init@v4" in codeql
    assert "github/codeql-action/analyze@v4" in codeql
    assert "security-events: write" in codeql
    assert "language: actions" in codeql
    assert "queries: security-extended" in codeql

    assert "pypa/gh-action-pip-audit@v1.1.0" in security_audit
    assert "python -m pip_audit -f cyclonedx-json" in security_audit
    assert "actions/upload-artifact@v4" in security_audit
    assert "name: callchain-sbom" in security_audit


def test_label_taxonomy_and_templates_cover_triage_and_release_labels():
    labels = Path(".github/labels.yml").read_text(encoding="utf-8")
    release_drafter = Path(".github/release-drafter.yml").read_text(encoding="utf-8")
    bug_template = Path(".github/ISSUE_TEMPLATE/bug_report.yml").read_text(encoding="utf-8")
    feature_template = Path(".github/ISSUE_TEMPLATE/feature_request.yml").read_text(encoding="utf-8")

    for label in [
        "bug",
        "enhancement",
        "feature",
        "maintenance",
        "dependencies",
        "ci",
        "documentation",
        "needs-triage",
        "needs-repro",
        "corpus-drift-approved",
        "security",
        "stale",
        "skip-changelog",
        "major",
        "minor",
    ]:
        assert f'- name: "{label}"' in labels

    assert "version-resolver:" in release_drafter
    assert "autolabeler:" in release_drafter
    assert 'labels: ["bug", "needs-triage"]' in bug_template
    assert 'labels: ["enhancement", "needs-triage"]' in feature_template


def test_dev_tooling_covers_audit_targets_ignore_rules_and_security_hooks():
    makefile = Path("Makefile").read_text(encoding="utf-8")
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    pre_commit = Path(".pre-commit-config.yaml").read_text(encoding="utf-8")
    security = Path("SECURITY.md").read_text(encoding="utf-8")

    assert "\naudit:\n\tpython -m pip_audit .\n" in makefile
    assert "build/callchain-sbom.cdx.json" in makefile
    assert '"pip-audit>=2.7"' in pyproject
    assert "*.sarif" in gitignore
    assert "*.cdx.json" in gitignore
    assert "check-toml" in pre_commit
    assert "detect-private-key" in pre_commit
    assert "Security Policy" in security
    assert "\ncorpus-check:\n\tpython scripts/check_corpus.py $(ARGS)\n" in makefile
    assert "\ncorpus-sources:\n\tpython scripts/check_corpus_sources.py $(ARGS)\n" in makefile
    assert "\ncorpus-sync:\n\tpython scripts/sync_corpus_sources.py $(ARGS)\n" in makefile
    assert "\ncorpus-refresh:\n\tpython scripts/refresh_corpus_source.py $(ARGS)\n" in makefile
    assert "\ncorpus-materialize:\n\tpython scripts/materialize_corpus_source.py $(ARGS)\n" in makefile
    assert "\ncorpus-verify-archive:\n\tpython scripts/verify_corpus_source_archive.py $(ARGS)\n" in makefile
    assert "\ncorpus-snapshot:\n\t@mkdir -p build\n\tpython scripts/check_corpus.py --json --output build/corpus-snapshot.json $(ARGS)\n" in makefile
    assert '\ncorpus-baseline:\n\t@mkdir -p build\n\tpython scripts/check_corpus_sources.py\n\tpython scripts/check_corpus.py --json --output build/corpus-baseline-snapshot.json $(ARGS)\n\tpython scripts/benchmark_corpus.py --iterations "$(BENCHMARK_ITERATIONS)" --warmup "$(BENCHMARK_WARMUP)" --json --output build/corpus-baseline-benchmark.json $(ARGS)\n' in makefile
    assert "benchmark_corpus.py --output build/corpus-benchmark.json --json $(ARGS)" in makefile
    assert 'compare_corpus_reports.py --baseline "$(BASELINE)" --candidate "$(CANDIDATE)" $(ARGS)' in makefile
    assert "\ninstall-smoke: build\n\tpython scripts/install_smoke.py --dist-dir dist --example examples/smoke_repo $(ARGS)\n" in makefile
    assert "\npublished-smoke:\n" in makefile
    assert 'PACKAGE_SPEC is required, e.g. make published-smoke PACKAGE_SPEC=callchain==<version>' in makefile
    assert 'python scripts/install_smoke.py --package-spec "$(PACKAGE_SPEC)" --example examples/smoke_repo $(ARGS)' in makefile
    assert "\nrelease-corpus-state:\n\tpython scripts/render_release_corpus_state.py $(ARGS)\n" in makefile


def test_community_files_and_typed_package_metadata_exist():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    citation = Path("CITATION.cff").read_text(encoding="utf-8")
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    support = Path("SUPPORT.md").read_text(encoding="utf-8")
    pyproject_data = tomllib.loads(pyproject)
    version = pyproject_data["project"]["version"]
    release_date_match = re.search(rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$", changelog, re.MULTILINE)

    assert '"Typing :: Typed"' in pyproject
    assert 'Support = "https://github.com/callchain/callchain/discussions"' in pyproject
    assert 'Security = "https://github.com/callchain/callchain/security/policy"' in pyproject
    assert "[tool.hatch.build.targets.sdist]" in pyproject
    assert '"/legacy"' in pyproject
    assert release_date_match is not None
    assert f"version: {version}" in citation
    assert f"date-released: {release_date_match.group(1)}" in citation
    assert "GitHub Discussions" in support


def test_repo_contains_governance_and_release_docs():
    for path in [
        "CITATION.cff",
        "CODE_OF_CONDUCT.md",
        "SECURITY.md",
        "SUPPORT.md",
        "RELEASING.md",
        "test_repos/corpus.toml",
        "test_repos/sources.toml",
        "scripts/check_corpus_sources.py",
        "scripts/sync_corpus_sources.py",
        "scripts/refresh_corpus_source.py",
        "scripts/materialize_corpus_source.py",
        "scripts/verify_corpus_source_archive.py",
        "scripts/compare_corpus_reports.py",
        "scripts/install_smoke.py",
        "scripts/render_release_corpus_state.py",
        "examples/README.md",
        "examples/smoke_repo/README.md",
        "examples/python_service/README.md",
        "examples/ts_dashboard/README.md",
        "examples/cpp_library/README.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/labels.yml",
        ".github/workflows/corpus-baseline.yml",
        ".github/workflows/corpus-baseline-compare.yml",
        ".github/workflows/corpus-baseline-refresh.yml",
        ".github/workflows/ci.yml",
        ".github/workflows/corpus-maintenance.yml",
        ".github/workflows/codeql.yml",
        ".github/workflows/testpypi-rehearsal.yml",
        ".github/workflows/post-release-smoke.yml",
        ".github/workflows/label-sync.yml",
        ".github/workflows/security-audit.yml",
        ".github/workflows/stale.yml",
    ]:
        assert Path(path).exists(), f"Expected repository file {path} to exist."


def test_repo_does_not_contain_python_bytecode_artifacts():
    unexpected_files = sorted(
        str(path)
        for root in (Path("tests"), Path("legacy"))
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".pyc", ".pyo"}
    )
    unexpected_dirs = sorted(
        str(path)
        for root in (Path("tests"), Path("legacy"))
        if root.exists()
        for path in root.rglob("__pycache__")
    )

    assert unexpected_files == []
    assert unexpected_dirs == []


def test_ci_workflow_includes_dedicated_corpus_job():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "\n  corpus:\n" in workflow
    assert "python scripts/check_corpus.py" in workflow
    assert "python scripts/check_corpus_sources.py" in workflow


def test_ci_workflow_includes_install_smoke_matrix():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "\n  install-smoke:\n" in workflow
    assert 'python-version: ["3.10", "3.12", "3.13"]' in workflow
    assert "python -m pip install build" in workflow
    assert "python -m build" in workflow
    assert "python scripts/install_smoke.py \\" in workflow
    assert "--dist-dir dist \\" in workflow
    assert "--example examples/smoke_repo \\" in workflow
    assert "--json \\" in workflow
    assert "--output build/install-smoke.json" in workflow
    assert "name: install-smoke-${{ matrix.python-version }}" in workflow


def test_registry_smoke_workflows_cover_testpypi_rehearsal_and_post_release_checks():
    rehearsal = Path(".github/workflows/testpypi-rehearsal.yml").read_text(encoding="utf-8")
    post_release = Path(".github/workflows/post-release-smoke.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in rehearsal
    assert "environment:" in rehearsal
    assert "name: testpypi" in rehearsal
    assert "id-token: write" in rehearsal
    assert "pypa/gh-action-pypi-publish@release/v1" in rehearsal
    assert "repository-url: https://test.pypi.org/legacy/" in rehearsal
    assert "skip-existing: true" in rehearsal
    assert 'python scripts/install_smoke.py \\' in rehearsal
    assert '--package-spec "callchain==${PACKAGE_VERSION}" \\' in rehearsal
    assert "--index-url https://test.pypi.org/simple/ \\" in rehearsal
    assert "--extra-index-url https://pypi.org/simple/ \\" in rehearsal
    assert "build/testpypi-install-smoke.json" in rehearsal
    assert "build/testpypi-local-install-smoke.json" in rehearsal
    assert "name: testpypi-rehearsal-artifacts" in rehearsal

    assert "release:" in post_release
    assert "types: [published]" in post_release
    assert 'VERSION="${RELEASE_TAG#v}"' in post_release
    assert '--package-spec "callchain==${VERSION}" \\' in post_release
    assert "--index-url https://pypi.org/simple/ \\" in post_release
    assert "build/post-release-install-smoke.json" in post_release
    assert "name: post-release-install-smoke" in post_release


def test_codeowners_has_real_owner_assignments():
    codeowners = Path(".github/CODEOWNERS").read_text(encoding="utf-8")

    assert "@X-PG13" in codeowners
    assert "Replace these placeholders" not in codeowners


def test_examples_readme_covers_multiple_realistic_scenarios():
    examples_readme = Path("examples/README.md").read_text(encoding="utf-8")
    root_readme = Path("README.md").read_text(encoding="utf-8")

    assert "python_service" in examples_readme
    assert "ts_dashboard" in examples_readme
    assert "cpp_library" in examples_readme
    assert "I want to inspect a layered Python service" in root_readme
    assert "I want to inspect a TypeScript frontend-style flow" in root_readme
    assert "I want to inspect a tiny C++ library" in root_readme


def test_corpus_maintenance_workflow_supports_manual_materialization_and_archive_verification():
    workflow = Path(".github/workflows/corpus-maintenance.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "source_name:" in workflow
    assert "ref:" in workflow
    assert "materialize:" in workflow
    assert "verify_archive:" in workflow
    assert "compare_snapshots:" in workflow
    assert "python scripts/check_corpus_sources.py" in workflow
    assert 'python scripts/verify_corpus_source_archive.py "$SOURCE_NAME"' in workflow
    assert 'python scripts/materialize_corpus_source.py "$SOURCE_NAME" --ref "$TARGET_REF"' in workflow
    assert "python scripts/check_corpus.py --json --output build/corpus-before.json" in workflow
    assert "python scripts/check_corpus.py --json --output build/corpus-after.json" in workflow
    assert "python scripts/compare_corpus_reports.py --baseline build/corpus-before.json --candidate build/corpus-after.json" in workflow
    assert "actions/upload-artifact@v4" in workflow


def test_corpus_baseline_workflow_captures_and_uploads_official_baseline_artifacts():
    workflow = Path(".github/workflows/corpus-baseline.yml").read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert 'cron: "0 3 * * 1"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "python scripts/check_corpus_sources.py" in workflow
    assert "python scripts/verify_corpus_source_archive.py click-src" in workflow
    assert "python scripts/check_corpus.py --json --output build/corpus-baseline-snapshot.json" in workflow
    assert "python scripts/benchmark_corpus.py --iterations 3 --warmup 1 --json --output build/corpus-baseline-benchmark.json" in workflow
    assert 'echo "## Corpus Baseline"' in workflow
    assert "python scripts/check_corpus.py" in workflow
    assert "python scripts/benchmark_corpus.py --iterations 3 --warmup 1" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "retention-days: 30" in workflow
    assert "build/corpus-baseline-snapshot.json" in workflow
    assert "build/corpus-baseline-benchmark.json" in workflow


def test_corpus_baseline_compare_workflow_consumes_latest_official_artifact_and_gates_summary_drift():
    workflow = Path(".github/workflows/corpus-baseline-compare.yml").read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "allow_summary_drift:" in workflow
    assert "actions: read" in workflow
    assert "pull-requests: write" in workflow
    assert 'const labelName = "corpus-drift-approved";' in workflow
    assert 'workflow_dispatch input allow_summary_drift' in workflow
    assert 'core.setOutput("waived", waived ? "true" : "false")' in workflow
    assert "github.rest.pulls.listFiles" in workflow
    assert "build/corpus-changed-files.json" in workflow
    assert 'gh run list --workflow "Corpus Baseline"' in workflow
    assert 'gh run download "$RUN_ID" --dir build/official-baseline --pattern "corpus-baseline-*"' in workflow
    assert "python scripts/check_corpus_sources.py" in workflow
    assert "python scripts/check_corpus.py --json --output build/corpus-current-snapshot.json" in workflow
    assert 'cmd=(' in workflow
    assert 'cmd+=(--changed-files build/corpus-changed-files.json)' in workflow
    assert 'cmd+=(--codeowners .github/CODEOWNERS)' in workflow
    assert "--metric summary \\" in workflow
    assert 'cmd+=(--json --output build/corpus-baseline-compare.json)' in workflow
    assert 'cmd+=(--markdown --output build/corpus-baseline-compare.md)' in workflow
    assert "--fail-on-summary-drift" in workflow
    assert 'echo "## Corpus Baseline Compare"' in workflow
    assert 'echo "- Drift waiver: \\`${{ steps.drift-policy.outputs.reason }}\\`"' in workflow
    assert "cat build/corpus-baseline-compare.md" in workflow
    assert "actions/github-script@v7" in workflow
    assert "<!-- callchain-corpus-baseline-compare -->" in workflow
    assert "> Drift waiver: \\`${{ steps.drift-policy.outputs.reason }}\\`" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "if-no-files-found: ignore" in workflow
    assert "build/corpus-baseline-compare.json" in workflow
    assert "build/corpus-baseline-compare.md" in workflow
    assert "steps.drift-policy.outputs.waived != 'true'" in workflow


def test_corpus_baseline_refresh_workflow_captures_post_merge_candidate_for_drift_approved_prs():
    workflow = Path(".github/workflows/corpus-baseline-refresh.yml").read_text(encoding="utf-8")

    assert "pull_request_target:" in workflow
    assert "closed" in workflow
    assert "github.event.pull_request.merged == true" in workflow
    assert "corpus-drift-approved" in workflow
    assert "pull-requests: write" in workflow
    assert "github.event.pull_request.merge_commit_sha" in workflow
    assert "python scripts/check_corpus_sources.py" in workflow
    assert "python scripts/verify_corpus_source_archive.py click-src" in workflow
    assert "python scripts/check_corpus.py --json --output build/corpus-baseline-refresh-snapshot.json" in workflow
    assert "python scripts/benchmark_corpus.py --iterations 3 --warmup 1 --json --output build/corpus-baseline-refresh-benchmark.json" in workflow
    assert 'echo "## Corpus Baseline Refresh"' in workflow
    assert "actions/github-script@v7" in workflow
    assert "<!-- callchain-corpus-baseline-refresh -->" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "corpus-baseline-refresh-${{ github.event.pull_request.number }}-${{ github.run_id }}" in workflow
