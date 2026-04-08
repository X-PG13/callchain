"""Tests for corpus regression and benchmark helpers."""

from __future__ import annotations

from dataclasses import replace
import io
import json
import runpy
import subprocess
import sys
import tarfile
import warnings
from pathlib import Path
import zipfile

import pytest

from callchain.devtools import corpus


def test_load_manifest_parses_defaults_and_aliases(tmp_path):
    manifest = tmp_path / "corpus.toml"
    manifest.write_text(
        '[[projects]]\n'
        'name = "sample"\n'
        'path = "repo"\n'
        'languages = ["py", "python"]\n'
        'exclude = ["vendor/**"]\n'
        'restrict_dir = "src"\n'
        "max_depth = 5\n"
        "max_chains = 7\n"
        "only_cross_file = true\n"
        "cache = true\n"
        "min_files = 1\n",
        encoding="utf-8",
    )

    entries = corpus.load_manifest(manifest)

    assert entries == [
        corpus.CorpusEntry(
            name="sample",
            path="repo",
            languages=(corpus.Language.PYTHON,),
            exclude=("vendor/**",),
            restrict_dir="src",
            max_depth=5,
            max_chains=7,
            only_cross_file=True,
            cache=True,
            min_files=1,
        )
    ]


@pytest.mark.parametrize(
    ("raw_toml", "message"),
    [
        ('version = 2\n[[projects]]\npath = "repo"\n', "Unsupported corpus manifest version"),
        ("", "must define at least one"),
        ("projects = []\n", "must define at least one"),
        ('projects = ["repo"]\n', "must be a TOML table"),
        ('[[projects]]\nname = ""\npath = "repo"\n', "non-empty string 'name'"),
        ('[[projects]]\nname = "sample"\n', "non-empty string 'path'"),
        ('[[projects]]\npath = "repo"\nlanguages = "python"\n', "must be a list of strings"),
        ('[[projects]]\npath = "repo"\nlanguages = [1]\n', "entries must be strings"),
        ('[[projects]]\npath = "repo"\nlanguages = ["elixir"]\n', "Unsupported corpus language"),
        ('[[projects]]\npath = "repo"\nexclude = [1]\n', "exclude"),
        ('[[projects]]\npath = "repo"\nrestrict_dir = 1\n', "restrict_dir"),
    ],
)
def test_load_manifest_rejects_invalid_shapes(tmp_path, raw_toml, message):
    manifest = tmp_path / "corpus.toml"
    manifest.write_text(raw_toml, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        corpus.load_manifest(manifest)


@pytest.mark.parametrize(
    ("raw_toml", "message"),
    [
        ('version = 2\n[[sources]]\nname = "sample"\nkind = "local"\nanalyzed_path = "repo"\nroot_path = "repo"\nlicense_spdx = "Apache-2.0"\nlicense_file = "LICENSE"\n', "Unsupported source registry version"),
        ("", "must define at least one"),
        ("sources = []\n", "must define at least one"),
        ('sources = ["repo"]\n', "must be a TOML table"),
        ('[[sources]]\nname = ""\nkind = "local"\nanalyzed_path = "repo"\nroot_path = "repo"\nlicense_spdx = "Apache-2.0"\nlicense_file = "LICENSE"\n', "non-empty string 'name'"),
        ('[[sources]]\nname = "sample"\nkind = "odd"\nanalyzed_path = "repo"\nroot_path = "repo"\nlicense_spdx = "Apache-2.0"\nlicense_file = "LICENSE"\n', "supported kind"),
        ('[[sources]]\nname = "sample"\nkind = "local"\nanalyzed_path = "repo"\nroot_path = "repo"\nlicense_spdx = "Apache-2.0"\nlicense_file = "LICENSE"\nnotes = 1\n', "notes"),
    ],
)
def test_load_source_registry_rejects_invalid_shapes(tmp_path, raw_toml, message):
    registry = tmp_path / "sources.toml"
    registry.write_text(raw_toml, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        corpus.load_source_registry(registry)


def test_run_entry_and_check_manifest_pass_on_temp_project(tmp_path):
    project = _write_sample_project(tmp_path)
    manifest = tmp_path / "corpus.toml"
    manifest.write_text(
        "[[projects]]\n"
        'name = "sample-auto"\n'
        f'path = "{project.name}"\n'
        'restrict_dir = "src"\n'
        'exclude = ["tests/**"]\n'
        "max_depth = 10\n"
        "max_chains = 10\n"
        "only_cross_file = true\n"
        "cache = true\n"
        "min_files = 2\n"
        "min_functions = 3\n"
        "min_edges = 2\n"
        "min_chains = 1\n"
        "max_parse_errors = 0\n",
        encoding="utf-8",
    )

    runs = corpus.check_manifest(manifest)

    assert len(runs) == 1
    run = runs[0]
    assert run.name == "sample-auto"
    assert run.path == project.name
    assert run.languages == ("python",)
    assert run.files == 2
    assert run.functions == 3
    assert run.classes == 0
    assert run.edges == 2
    assert run.chains == 1
    assert run.parse_errors == 0
    assert run.total_seconds >= 0


def test_source_inventory_validates_local_and_vendored_sources(tmp_path):
    manifest, registry = _write_corpus_source_fixture(tmp_path)

    report = corpus.source_inventory(manifest, registry)

    assert report["manifest"] == str(manifest.resolve())
    assert report["registry"] == str(registry.resolve())
    by_name = {entry["name"]: entry for entry in report["entries"]}
    assert by_name["local-sample"]["kind"] == "local"
    assert by_name["vendored-sample"]["kind"] == "vendored"
    assert by_name["vendored-sample"]["pyproject_version"] == "1.2.3"
    assert by_name["vendored-sample"]["pyproject_license"] == "BSD-3-Clause"
    assert by_name["vendored-sample"]["source_ref"] == "fixture-ref"
    assert by_name["vendored-sample"]["rendered_archive_url"].startswith("file://")
    assert by_name["vendored-sample"]["archive_sha256"]
    assert by_name["vendored-sample"]["content_sha256"] == by_name["vendored-sample"]["computed_sha256"]
    rendered = corpus.format_source_inventory(report)
    assert "Corpus source inventory passed" in rendered
    assert "vendored-sample: kind=vendored" in rendered


def test_source_inventory_requires_content_sha256(tmp_path):
    manifest, registry = _write_corpus_source_fixture(tmp_path)
    registry_text = registry.read_text(encoding="utf-8")
    vendored_hash = corpus._compute_tree_sha256(tmp_path / "vendored_repo")

    registry.write_text(
        registry_text.replace(f'content_sha256 = "{vendored_hash}"\n', ""),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must define a content_sha256"):
        corpus.source_inventory(manifest, registry)


def test_run_entry_rejects_missing_project_path(tmp_path):
    entry = corpus.CorpusEntry(name="missing", path="does-not-exist")

    with pytest.raises(ValueError, match="does not exist"):
        corpus.run_entry(entry, tmp_path)


def test_check_manifest_reports_threshold_failures(tmp_path):
    project = _write_sample_project(tmp_path)
    manifest = tmp_path / "corpus.toml"
    manifest.write_text(
        "[[projects]]\n"
        'name = "sample"\n'
        f'path = "{project.name}"\n'
        "min_functions = 99\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected functions >= 99"):
        corpus.check_manifest(manifest)


def test_source_inventory_rejects_registry_drift_and_bad_paths(tmp_path):
    manifest, registry = _write_corpus_source_fixture(tmp_path)
    registry_text = registry.read_text(encoding="utf-8")
    vendored_hash = corpus._compute_tree_sha256(tmp_path / "vendored_repo")

    with pytest.raises(ValueError, match="missing source metadata"):
        registry.write_text(registry_text.replace('name = "local-sample"', 'name = "orphan-local"'), encoding="utf-8")
        corpus.source_inventory(manifest, registry)

    registry.write_text(
        registry_text
        + '\n[[sources]]\nname = "extra-sample"\nkind = "local"\nanalyzed_path = "local_repo"\nroot_path = "local_repo"\nlicense_spdx = "Apache-2.0"\nlicense_file = "LICENSE"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unreferenced source metadata"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(
        registry_text.replace('name = "vendored-sample"', 'name = "local-sample"', 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate source name"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(registry_text.replace('analyzed_path = "vendored_repo/src"', 'analyzed_path = "vendored_repo/other"'), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match manifest path"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(registry_text.replace('analyzed_path = "vendored_repo/src"', 'analyzed_path = "missing_repo/src"'), encoding="utf-8")
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('path = "vendored_repo/src"', 'path = "missing_repo/src"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="analyzed path does not exist"):
        corpus.source_inventory(manifest, registry)

    manifest, registry = _write_corpus_source_fixture(tmp_path)
    registry_text = registry.read_text(encoding="utf-8")
    registry.write_text(registry_text.replace('license_file = "vendored_repo/LICENSE.txt"', 'license_file = "vendored_repo/MISSING.txt"'), encoding="utf-8")
    with pytest.raises(ValueError, match="license file does not exist"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(registry_text.replace('root_path = "vendored_repo"', 'root_path = "missing_root"'), encoding="utf-8")
    with pytest.raises(ValueError, match="root path does not exist"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(registry_text.replace('root_path = "vendored_repo"', 'root_path = "local_repo"'), encoding="utf-8")
    with pytest.raises(ValueError, match="must live within its root_path"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(
        registry_text.replace(f'content_sha256 = "{vendored_hash}"', 'content_sha256 = "bogus-hash"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not match computed checksum"):
        corpus.source_inventory(manifest, registry)


def test_source_inventory_rejects_vendored_metadata_drift(tmp_path):
    manifest, registry = _write_corpus_source_fixture(tmp_path)
    registry_text = registry.read_text(encoding="utf-8")
    vendored_hash = corpus._compute_tree_sha256(tmp_path / "vendored_repo")

    registry.write_text(registry_text.replace('upstream_url = "https://example.com/vendor/sample"', ""), encoding="utf-8")
    with pytest.raises(ValueError, match="must define an upstream_url"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(registry_text.replace('version = "1.2.3"', ""), encoding="utf-8")
    with pytest.raises(ValueError, match="must define a version"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(registry_text.replace('version = "1.2.3"', 'version = "9.9.9"'), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match local pyproject version"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(registry_text.replace('license_spdx = "BSD-3-Clause"', 'license_spdx = "MIT"'), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match local pyproject license"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(registry_text.replace('source_ref = "fixture-ref"', ""), encoding="utf-8")
    with pytest.raises(ValueError, match="must define a source_ref"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(registry_text.replace('archive_url = "', '# archive_url = "', 1), encoding="utf-8")
    with pytest.raises(ValueError, match="must define an archive_url"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(registry_text.replace('archive_sha256 = "', '# archive_sha256 = "', 1), encoding="utf-8")
    with pytest.raises(ValueError, match="must define an archive_sha256"):
        corpus.source_inventory(manifest, registry)

    registry.write_text(registry_text.replace('source_ref = "fixture-ref"', 'source_ref = "stale-ref"'), encoding="utf-8")
    git_dir = tmp_path / "vendored_repo" / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("feedbead\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not match detected git ref"):
        corpus.source_inventory(manifest, registry)

    (tmp_path / "vendored_repo" / "pyproject.toml").write_text('[tool.example]\nvalue = 1\n', encoding="utf-8")
    (tmp_path / "vendored_repo" / ".git" / "HEAD").unlink(missing_ok=True)
    updated_hash = corpus._compute_tree_sha256(tmp_path / "vendored_repo")
    registry.write_text(
        registry.read_text(encoding="utf-8")
        .replace('source_ref = "stale-ref"', 'source_ref = "fixture-ref"')
        .replace(f'content_sha256 = "{vendored_hash}"', f'content_sha256 = "{updated_hash}"'),
        encoding="utf-8",
    )
    report = corpus.source_inventory(manifest, registry)
    vendored = next(entry for entry in report["entries"] if entry["name"] == "vendored-sample")
    assert vendored["pyproject_version"] is None
    assert vendored["pyproject_license"] is None


def test_benchmark_manifest_validates_parameters_and_summarizes_runs(tmp_path):
    project = _write_sample_project(tmp_path)
    manifest = tmp_path / "corpus.toml"
    manifest.write_text(
        "[[projects]]\n"
        'name = "sample"\n'
        f'path = "{project.name}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="at least 1"):
        corpus.benchmark_manifest(manifest, iterations=0)
    with pytest.raises(ValueError, match="0 or greater"):
        corpus.benchmark_manifest(manifest, warmup=-1)

    calls: list[int] = []

    def fake_runner(entry: corpus.CorpusEntry, manifest_root: Path) -> corpus.CorpusRun:
        calls.append(len(calls))
        duration = float(len(calls))
        assert entry.name == "sample"
        assert manifest_root == manifest.parent.resolve()
        return corpus.CorpusRun(
            name=entry.name,
            path=entry.path,
            languages=("python",),
            files=2,
            functions=3,
            classes=0,
            edges=2,
            chains=1,
            parse_errors=0,
            build_seconds=duration,
            chain_seconds=duration + 0.1,
            analysis_seconds=duration + 0.2,
            total_seconds=duration + 0.3,
        )

    report = corpus.benchmark_manifest(manifest, iterations=3, warmup=1, runner=fake_runner)

    assert calls == [0, 1, 2, 3]
    assert report["iterations"] == 3
    assert report["warmup"] == 1
    case = report["cases"][0]
    assert case["summary"] == {
        "files": 2,
        "functions": 3,
        "classes": 0,
        "edges": 2,
        "chains": 1,
        "parse_errors": 0,
    }
    assert case["timings"]["build_seconds"] == {"min": 2.0, "median": 3.0, "mean": 3.0, "max": 4.0}
    assert case["timings"]["total_seconds"] == {"min": 2.3, "median": 3.3, "mean": 3.3, "max": 4.3}
    rendered = corpus.format_benchmark_report(report)
    assert "Iterations=3, Warmup=1" in rendered
    assert "sample: total median=3.300s" in rendered


def test_main_check_and_benchmark_output_paths(tmp_path, capsys):
    project = _write_sample_project(tmp_path)
    manifest = tmp_path / "corpus.toml"
    manifest.write_text(
        "[[projects]]\n"
        'name = "sample"\n'
        f'path = "{project.name}"\n'
        'restrict_dir = "src"\n'
        "min_files = 2\n"
        "min_functions = 3\n"
        "min_edges = 2\n"
        "min_chains = 1\n",
        encoding="utf-8",
    )

    assert corpus.main(["check", "--manifest", str(manifest)]) == 0
    stdout = capsys.readouterr().out
    assert "Corpus regression check passed" in stdout
    assert "sample: files=2" in stdout

    output = tmp_path / "reports" / "benchmark.json"
    assert corpus.main(
        [
            "benchmark",
            "--manifest",
            str(manifest),
            "--iterations",
            "1",
            "--warmup",
            "0",
            "--json",
            "--output",
            str(output),
        ]
    ) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["iterations"] == 1
    assert payload["cases"][0]["name"] == "sample"


def test_sources_main_and_wrapper_script(tmp_path):
    manifest, registry = _write_corpus_source_fixture(tmp_path)
    output = tmp_path / "reports" / "sources.json"

    assert corpus.main(
        [
            "sources",
            "--manifest",
            str(manifest),
            "--registry",
            str(registry),
            "--json",
            "--output",
            str(output),
        ]
    ) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["entries"][1]["name"] == "vendored-sample"

    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/check_corpus_sources.py"),
            "--manifest",
            str(manifest),
            "--registry",
            str(registry),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Corpus source inventory passed" in proc.stdout


def test_verify_source_archive_succeeds_and_wrapper_script(tmp_path):
    manifest, registry = _write_corpus_source_fixture(tmp_path)
    output = tmp_path / "reports" / "verify-archive.json"

    report = corpus.verify_source_archive(manifest, registry, source_name="vendored-sample")
    assert report["verified"] is True
    assert report["archive_url"].startswith("file://")
    assert report["archive_bytes"] > 0
    assert report["expected_archive_sha256"] == report["downloaded_archive_sha256"]
    assert "Vendored corpus archive verified" in corpus.format_archive_verification(report)

    assert corpus.main(
        [
            "verify-archive",
            "vendored-sample",
            "--manifest",
            str(manifest),
            "--registry",
            str(registry),
            "--json",
            "--output",
            str(output),
        ]
    ) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["verified"] is True

    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/verify_corpus_source_archive.py"),
            "vendored-sample",
            "--manifest",
            str(manifest),
            "--registry",
            str(registry),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Vendored corpus archive verified" in proc.stdout


def test_verify_source_archive_rejects_checksum_and_scheme_errors(tmp_path):
    manifest, registry = _write_corpus_source_fixture(tmp_path)
    registry_text = registry.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="does not contain source"):
        corpus.verify_source_archive(manifest, registry, source_name="missing-source")

    with pytest.raises(ValueError, match="is not vendored"):
        corpus.verify_source_archive(manifest, registry, source_name="local-sample")

    registry.write_text(
        registry_text.replace('archive_sha256 = "', 'archive_sha256 = "bogus-', 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not match downloaded archive checksum"):
        corpus.verify_source_archive(manifest, registry, source_name="vendored-sample")

    registry.write_text(
        registry_text.replace(
            'archive_url = "',
            'archive_url = "ssh://example.com/',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unsupported archive URL scheme"):
        corpus.verify_source_archive(manifest, registry, source_name="vendored-sample")


def test_sync_source_registry_updates_checksum_version_and_ref(tmp_path):
    manifest, registry = _write_corpus_source_fixture(tmp_path)
    vendored_git = tmp_path / "vendored_repo" / ".git"
    vendored_git.mkdir()
    (vendored_git / "HEAD").write_text("cafefeed\n", encoding="utf-8")
    vendored_hash = corpus._compute_tree_sha256(tmp_path / "vendored_repo")
    registry.write_text(
        registry.read_text(encoding="utf-8")
        .replace('version = "1.2.3"', 'version = "0.0.1"')
        .replace('source_ref = "fixture-ref"', 'source_ref = "stale-ref"')
        .replace(f'content_sha256 = "{vendored_hash}"', 'content_sha256 = "stale-hash"'),
        encoding="utf-8",
    )

    report = corpus.sync_source_registry(manifest, registry)

    assert report["written"] is True
    assert report["changed"] is True
    changes = {item["name"]: item["fields"] for item in report["changes"]}
    assert changes["vendored-sample"] == ["version", "source_ref", "content_sha256"]
    synced_text = registry.read_text(encoding="utf-8")
    assert 'version = "1.2.3"' in synced_text
    assert 'source_ref = "cafefeed"' in synced_text
    assert f'content_sha256 = "{vendored_hash}"' in synced_text


def test_sync_source_registry_supports_dry_run_and_main(tmp_path):
    manifest, registry = _write_corpus_source_fixture(tmp_path)
    original = registry.read_text(encoding="utf-8")
    output = tmp_path / "reports" / "sync.json"

    report = corpus.sync_source_registry(manifest, registry, dry_run=True)
    assert report["dry_run"] is True
    assert report["written"] is False
    assert registry.read_text(encoding="utf-8") == original
    rendered = corpus.format_sync_report(report)
    assert "Corpus source registry previewed" in rendered

    assert corpus.main(
        [
            "sync-sources",
            "--manifest",
            str(manifest),
            "--registry",
            str(registry),
            "--dry-run",
            "--json",
            "--output",
            str(output),
        ]
    ) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["dry_run"] is True

    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/sync_corpus_sources.py"),
            "--manifest",
            str(manifest),
            "--registry",
            str(registry),
            "--dry-run",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Corpus source registry previewed" in proc.stdout


def test_refresh_vendored_source_updates_checkout_and_registry(tmp_path):
    fixture = _write_git_refresh_fixture(tmp_path)

    report = corpus.refresh_vendored_source(
        fixture["manifest"],
        fixture["registry"],
        source_name="vendored-sample",
        ref=fixture["updated_commit"],
    )

    assert report["name"] == "vendored-sample"
    assert report["previous_ref"] == fixture["initial_commit"]
    assert report["resolved_ref"] == fixture["updated_commit"]
    assert report["fetched"] is True
    assert report["changed"] is True
    assert report["changes"] == ["version", "source_ref", "content_sha256"]
    assert corpus._run_git(fixture["checkout"], "rev-parse", "HEAD") == fixture["updated_commit"]

    registry_text = fixture["registry"].read_text(encoding="utf-8")
    assert 'version = "1.1.0"' in registry_text
    assert f'source_ref = "{fixture["updated_commit"]}"' in registry_text
    assert report["entry"]["version"] == "1.1.0"
    assert report["entry"]["source_ref"] == fixture["updated_commit"]
    assert report["entry"]["content_sha256"] == corpus._compute_tree_sha256(fixture["checkout"])


def test_refresh_vendored_source_can_update_archive_sha256(tmp_path):
    fixture = _write_git_refresh_fixture(tmp_path)

    report = corpus.refresh_vendored_source(
        fixture["manifest"],
        fixture["registry"],
        source_name="vendored-sample",
        ref=fixture["updated_commit"],
        verify_archive=True,
    )

    assert report["archive_verification"]["verified"] is True
    assert report["changes"] == ["version", "source_ref", "archive_sha256", "content_sha256"]
    rendered = corpus.format_refresh_report(report)
    assert "verified archive" in rendered
    assert report["entry"]["archive_sha256"] == corpus._compute_file_sha256(fixture["updated_archive"])


def test_materialize_vendored_source_downloads_archive_and_replaces_tree(tmp_path):
    fixture = _write_archive_materialize_fixture(tmp_path)

    report = corpus.materialize_vendored_source(
        fixture["manifest"],
        fixture["registry"],
        source_name="vendored-sample",
        ref=fixture["ref"],
    )

    assert report["mode"] == "archive"
    assert report["resolved_ref"] == fixture["ref"]
    assert report["archive_verification"]["verified"] is True
    assert report["changes"] == ["version", "source_ref", "archive_sha256", "content_sha256"]
    assert not (fixture["root"] / "STALE.txt").exists()
    assert 'return 2' in (fixture["root"] / "src" / "vendor.py").read_text(encoding="utf-8")
    assert report["entry"]["version"] == "2.0.0"
    assert report["entry"]["source_ref"] == fixture["ref"]
    assert report["entry"]["archive_sha256"] == corpus._compute_file_sha256(fixture["archive_path"])
    assert report["entry"]["content_sha256"] == corpus._compute_tree_sha256(fixture["root"])


def test_materialize_vendored_source_uses_git_refresh_when_checkout_exists(tmp_path):
    fixture = _write_git_refresh_fixture(tmp_path)

    report = corpus.materialize_vendored_source(
        fixture["manifest"],
        fixture["registry"],
        source_name="vendored-sample",
        ref=fixture["updated_commit"],
    )

    assert report["mode"] == "git"
    assert report["resolved_ref"] == fixture["updated_commit"]
    assert report["archive_verification"]["verified"] is True


def test_refresh_vendored_source_rejects_missing_non_vendored_and_dirty_states(tmp_path):
    manifest, registry = _write_corpus_source_fixture(tmp_path)

    with pytest.raises(ValueError, match="does not contain source"):
        corpus.refresh_vendored_source(manifest, registry, source_name="missing-source", ref="HEAD")

    with pytest.raises(ValueError, match="is not vendored"):
        corpus.refresh_vendored_source(manifest, registry, source_name="local-sample", ref="HEAD")

    registry.write_text(
        registry.read_text(encoding="utf-8").replace('root_path = "vendored_repo"', 'root_path = "missing-root"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="root path does not exist"):
        corpus.refresh_vendored_source(manifest, registry, source_name="vendored-sample", ref="HEAD")

    manifest, registry = _write_corpus_source_fixture(tmp_path / "non-git")
    with pytest.raises(ValueError, match="not backed by a local git checkout"):
        corpus.refresh_vendored_source(manifest, registry, source_name="vendored-sample", ref="HEAD")

    fixture = _write_git_refresh_fixture(tmp_path / "git-case")
    (fixture["checkout"] / "src" / "vendor.py").write_text("def vendor_entry():\n    return 99\n", encoding="utf-8")

    with pytest.raises(ValueError, match="has local changes"):
        corpus.refresh_vendored_source(
            fixture["manifest"],
            fixture["registry"],
            source_name="vendored-sample",
            ref=fixture["updated_commit"],
            fetch=False,
        )

    clean_fixture = _write_git_refresh_fixture(tmp_path / "missing-archive-url")
    registry_text = clean_fixture["registry"].read_text(encoding="utf-8")
    clean_fixture["registry"].write_text(
        registry_text.replace('archive_url = "', '# archive_url = "', 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must define an archive_url"):
        corpus.refresh_vendored_source(
            clean_fixture["manifest"],
            clean_fixture["registry"],
            source_name="vendored-sample",
            ref=clean_fixture["updated_commit"],
            verify_archive=True,
        )

    archive_fixture = _write_archive_materialize_fixture(tmp_path / "archive-case")
    with pytest.raises(ValueError, match="does not contain source"):
        corpus.materialize_vendored_source(
            archive_fixture["manifest"],
            archive_fixture["registry"],
            source_name="missing-source",
            ref=archive_fixture["ref"],
        )

    manifest, registry = _write_corpus_source_fixture(tmp_path / "local-materialize")
    with pytest.raises(ValueError, match="is not vendored and cannot be materialized"):
        corpus.materialize_vendored_source(
            manifest,
            registry,
            source_name="local-sample",
            ref="whatever",
        )

    archive_registry_text = archive_fixture["registry"].read_text(encoding="utf-8")
    archive_fixture["registry"].write_text(
        archive_registry_text.replace('archive_url = "', '# archive_url = "', 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must define an archive_url"):
        corpus.materialize_vendored_source(
            archive_fixture["manifest"],
            archive_fixture["registry"],
            source_name="vendored-sample",
            ref=archive_fixture["ref"],
        )


def test_refresh_source_main_and_wrapper_script(tmp_path):
    fixture = _write_git_refresh_fixture(tmp_path)
    output = tmp_path / "reports" / "refresh.json"

    assert corpus.main(
        [
            "refresh-source",
            "vendored-sample",
            "--manifest",
            str(fixture["manifest"]),
            "--registry",
            str(fixture["registry"]),
            "--ref",
            fixture["updated_commit"],
            "--json",
            "--output",
            str(output),
        ]
    ) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["name"] == "vendored-sample"
    assert payload["resolved_ref"] == fixture["updated_commit"]

    rendered = corpus.format_refresh_report(payload)
    assert "Vendored corpus source refreshed" in rendered
    assert "vendored-sample:" in rendered

    repeat = corpus.refresh_vendored_source(
        fixture["manifest"],
        fixture["registry"],
        source_name="vendored-sample",
        ref=fixture["updated_commit"],
        fetch=False,
    )
    assert repeat["changed"] is False
    assert "No source metadata changes detected." in corpus.format_refresh_report(repeat)

    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/refresh_corpus_source.py"),
            "vendored-sample",
            "--manifest",
            str(fixture["manifest"]),
            "--registry",
            str(fixture["registry"]),
            "--ref",
            fixture["updated_commit"],
            "--no-fetch",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Vendored corpus source refreshed" in proc.stdout


def test_materialize_source_main_and_wrapper_script(tmp_path):
    fixture = _write_archive_materialize_fixture(tmp_path)
    output = tmp_path / "reports" / "materialize.json"

    assert corpus.main(
        [
            "materialize-source",
            "vendored-sample",
            "--manifest",
            str(fixture["manifest"]),
            "--registry",
            str(fixture["registry"]),
            "--ref",
            fixture["ref"],
            "--json",
            "--output",
            str(output),
        ]
    ) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mode"] == "archive"
    assert payload["resolved_ref"] == fixture["ref"]
    assert "Vendored corpus source materialized" in corpus.format_materialize_report(payload)

    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/materialize_corpus_source.py"),
            "vendored-sample",
            "--manifest",
            str(fixture["manifest"]),
            "--registry",
            str(fixture["registry"]),
            "--ref",
            fixture["ref"],
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Vendored corpus source materialized" in proc.stdout

    repeat = corpus.materialize_vendored_source(
        fixture["manifest"],
        fixture["registry"],
        source_name="vendored-sample",
        ref=fixture["ref"],
    )
    assert repeat["changed"] is False
    assert "No source metadata changes detected." in corpus.format_materialize_report(repeat)


def test_sync_helpers_cover_changed_report_and_error_paths(tmp_path):
    report = corpus.format_sync_report(
        {
            "dry_run": False,
            "registry": "sources.toml",
            "manifest": "corpus.toml",
            "changes": [{"name": "sample", "fields": ["version", "content_sha256"]}],
        }
    )
    assert "Corpus source registry synced" in report
    assert "sample: updated version, content_sha256" in report

    manifest, registry = _write_corpus_source_fixture(tmp_path)
    manifest_root = manifest.parent
    sources = {source.name: source for source in corpus.load_source_registry(registry)}
    entries = {entry.name: entry for entry in corpus.load_manifest(manifest)}
    vendored_source = sources["vendored-sample"]
    vendored_entry = entries["vendored-sample"]

    with pytest.raises(ValueError, match="does not match manifest path"):
        corpus._sync_source_entry(replace(vendored_source, analyzed_path="wrong/src"), vendored_entry, manifest_root)

    with pytest.raises(ValueError, match="analyzed path does not exist"):
        corpus._sync_source_entry(
            replace(vendored_source, analyzed_path="missing/src"),
            replace(vendored_entry, path="missing/src"),
            manifest_root,
        )

    with pytest.raises(ValueError, match="root path does not exist"):
        corpus._sync_source_entry(replace(vendored_source, root_path="missing-root"), vendored_entry, manifest_root)

    with pytest.raises(ValueError, match="license file does not exist"):
        corpus._sync_source_entry(replace(vendored_source, license_file="missing-license"), vendored_entry, manifest_root)

    with pytest.raises(ValueError, match="must live within its root_path"):
        corpus._sync_source_entry(replace(vendored_source, root_path="local_repo"), vendored_entry, manifest_root)

    with pytest.raises(ValueError, match="must define an upstream_url"):
        corpus._sync_source_entry(replace(vendored_source, upstream_url=None), vendored_entry, manifest_root)

    (tmp_path / "vendored_repo" / "pyproject.toml").write_text('[tool.example]\nvalue = 1\n', encoding="utf-8")
    with pytest.raises(ValueError, match="must define a version"):
        corpus._sync_source_entry(replace(vendored_source, version=None), vendored_entry, manifest_root)

    with pytest.raises(ValueError, match="must define a source_ref or expose local git metadata"):
        corpus._sync_source_entry(replace(vendored_source, version="1.2.3", source_ref=None), vendored_entry, manifest_root)


def test_detect_git_source_ref_supports_packed_refs_and_gitdir_files(tmp_path):
    packed_repo = tmp_path / "packed"
    packed_git = packed_repo / ".git"
    packed_git.mkdir(parents=True)
    (packed_git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (packed_git / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        "^ignored\n"
        "abc123 refs/heads/main\n",
        encoding="utf-8",
    )
    assert corpus._detect_git_source_ref(packed_repo) == "abc123"

    linked_repo = tmp_path / "linked"
    linked_repo.mkdir()
    linked_git = tmp_path / "linked.git"
    linked_git.mkdir()
    (linked_repo / ".git").write_text("gitdir: ../linked.git\n", encoding="utf-8")
    (linked_git / "HEAD").write_text("deadbeef\n", encoding="utf-8")
    assert corpus._detect_git_source_ref(linked_repo) == "deadbeef"

    missing_ref_repo = tmp_path / "missing-ref"
    missing_ref_git = missing_ref_repo / ".git"
    missing_ref_git.mkdir(parents=True)
    (missing_ref_git / "HEAD").write_text("ref: refs/heads/missing\n", encoding="utf-8")
    (missing_ref_git / "packed-refs").write_text("# no matching refs here\n", encoding="utf-8")
    assert corpus._detect_git_source_ref(missing_ref_repo) is None

    with pytest.raises(ValueError, match="git rev-parse --verify missing\\^\\{commit\\} failed"):
        corpus._resolve_git_ref(missing_ref_repo, "missing")


def test_run_git_wraps_missing_binary(monkeypatch, tmp_path):
    def raising_run(*args, **kwargs):
        raise FileNotFoundError("git missing")

    monkeypatch.setattr(subprocess, "run", raising_run)

    with pytest.raises(ValueError, match="git is required"):
        corpus._run_git(tmp_path, "status")


def test_download_archive_sha256_supports_local_paths_and_wraps_errors(tmp_path):
    archive = tmp_path / "sample.tar.gz"
    archive.write_bytes(b"archive-bytes\n")

    digest, total = corpus._download_archive_sha256(str(archive))
    assert digest == corpus._compute_file_sha256(archive)
    assert total == archive.stat().st_size

    with pytest.raises(ValueError, match="Could not open archive location"):
        corpus._download_archive_sha256(str(tmp_path / "missing.tar.gz"))

    class _TextHandle:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return "text-payload"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(corpus, "_open_archive_handle", lambda location, timeout=30.0: _TextHandle())
    try:
        with pytest.raises(ValueError, match="did not produce binary content"):
            corpus._download_archive_bytes("dummy://archive")
    finally:
        monkeypatch.undo()

    with pytest.raises(ValueError, match="must define a source_ref to render archive_url"):
        corpus._render_archive_url(
            corpus.CorpusSource(
                name="sample",
                kind="vendored",
                analyzed_path="repo/src",
                root_path="repo",
                license_spdx="BSD-3-Clause",
                license_file="LICENSE",
                upstream_url="https://example.com/sample",
                version="1.0.0",
                source_ref=None,
                archive_url="https://example.com/archive/{ref}.tar.gz",
                archive_sha256="abc123",
                content_sha256="def456",
            )
        )
    source = corpus.CorpusSource(
        name="sample",
        kind="vendored",
        analyzed_path="repo/src",
        root_path="repo",
        license_spdx="BSD-3-Clause",
        license_file="LICENSE",
        upstream_url="https://example.com/sample",
        version="1.0.0",
        source_ref="old-ref",
        archive_url="https://example.com/archive/{ref}.tar.gz",
        archive_sha256="abc123",
        content_sha256="def456",
    )
    assert corpus._render_archive_url_for_ref(source, "new-ref") == "https://example.com/archive/new-ref.tar.gz"
    assert corpus._render_archive_url_for_ref(replace(source, archive_url="https://example.com/archive.tar.gz"), "new-ref") == "https://example.com/archive.tar.gz"


def test_archive_extraction_helpers_support_zip_and_block_escape(tmp_path):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("sample-root/src/vendor.py", "print('ok')\n")
    extracted_root = corpus._extract_archive_bytes(zip_buffer.getvalue(), tmp_path / "zip-extract")
    assert extracted_root.name == "sample-root"
    assert (extracted_root / "src" / "vendor.py").exists()

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as archive:
        payload = b"escape\n"
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(ValueError, match="would extract outside"):
        corpus._extract_archive_bytes(tar_buffer.getvalue(), tmp_path / "bad-extract")

    source_root = tmp_path / "source-root"
    source_root.mkdir()
    (source_root / "fresh.txt").write_text("fresh\n", encoding="utf-8")
    destination_root = tmp_path / "dest-root"
    destination_root.mkdir()
    (destination_root / "stale.txt").write_text("stale\n", encoding="utf-8")
    corpus._replace_tree(destination_root, source_root)
    assert not (destination_root / "stale.txt").exists()
    assert (destination_root / "fresh.txt").read_text(encoding="utf-8") == "fresh\n"

    plain_bytes = b"not an archive"
    with pytest.raises(ValueError, match="Unsupported archive format"):
        corpus._extract_archive_bytes(plain_bytes, tmp_path / "plain-extract")

    multi_root = tmp_path / "multi-root"
    multi_root.mkdir()
    (multi_root / "a.txt").write_text("a\n", encoding="utf-8")
    (multi_root / "b.txt").write_text("b\n", encoding="utf-8")
    assert corpus._normalize_extracted_root(multi_root) == multi_root


def test_compare_reports_supports_benchmark_and_snapshot_shapes(tmp_path):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    baseline.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "sample",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {
                            "build_seconds": {"median": 1.0},
                            "chain_seconds": {"median": 0.2},
                            "analysis_seconds": {"median": 0.1},
                            "total_seconds": {"median": 1.3},
                        },
                    },
                    {
                        "name": "improved",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {
                            "build_seconds": {"median": 1.0},
                            "chain_seconds": {"median": 0.2},
                            "analysis_seconds": {"median": 0.1},
                            "total_seconds": {"median": 1.5},
                        },
                    },
                    {
                        "name": "unchanged",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {
                            "build_seconds": {"median": 1.0},
                            "chain_seconds": {"median": 0.2},
                            "analysis_seconds": {"median": 0.1},
                            "total_seconds": {"median": 1.0},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    candidate.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "sample",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 4,
                            "classes": 0,
                            "edges": 3,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {
                            "build_seconds": 1.01,
                            "chain_seconds": 0.21,
                            "analysis_seconds": 0.11,
                            "total_seconds": 1.35,
                        },
                    },
                    {
                        "name": "improved",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {
                            "build_seconds": 0.9,
                            "chain_seconds": 0.19,
                            "analysis_seconds": 0.1,
                            "total_seconds": 1.2,
                        },
                    },
                    {
                        "name": "unchanged",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {
                            "build_seconds": 1.0,
                            "chain_seconds": 0.2,
                            "analysis_seconds": 0.1,
                            "total_seconds": 1.0,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = corpus.compare_reports(baseline, candidate, max_regression_pct=5.0)

    by_name = {item["name"]: item for item in report["comparisons"]}
    comparison = by_name["sample"]
    assert comparison["status"] == "within_threshold"
    assert comparison["summary_delta"] == {
        "files": 0,
        "functions": 1,
        "classes": 0,
        "edges": 1,
        "chains": 0,
        "parse_errors": 0,
    }
    assert by_name["improved"]["status"] == "improvement"
    assert by_name["unchanged"]["status"] == "unchanged"
    assert report["summary_drift_cases"] == ["sample"]
    assert report["has_summary_drift"] is True
    assert report["has_changed_files_context"] is False
    assert report["changed_files"] == []
    assert report["review_hints"] == [
        {
            "key": "symbol_extraction",
            "label": "symbol-extraction",
            "cases": ["sample"],
            "paths": ["src/callchain/languages/*.py"],
            "reason": "Review language parsers that extract functions, classes, methods, imports, and variables.",
            "matched_changed_files": [],
        },
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["sample"],
            "paths": ["src/callchain/core/callgraph.py", "src/callchain/languages/*.py"],
            "reason": "Review raw call extraction and cross-file edge resolution.",
            "matched_changed_files": [],
        },
        {
            "key": "non_structural",
            "label": "non-structural",
            "cases": ["improved"],
            "paths": ["src/callchain/devtools/corpus.py", ".github/workflows/corpus-baseline-compare.yml"],
            "reason": "Review corpus thresholds, compare rendering, and workflow gating rather than structural analysis.",
            "matched_changed_files": [],
        },
    ]
    assert report["owner_hints"] == []
    assert report["owner_focus"] == []
    rendered = corpus.format_compare_report(report)
    assert "Metric=total_seconds, Max regression=5.0%" in rendered
    assert "Review hints:" in rendered
    assert "symbol-extraction: review src/callchain/languages/*.py, cases: sample" in rendered
    assert (
        "non-structural: review src/callchain/devtools/corpus.py, .github/workflows/corpus-baseline-compare.yml, "
        "cases: improved"
    ) in rendered
    assert "functions +1" in rendered
    assert "edges +1" in rendered
    markdown = corpus.format_compare_markdown(report)
    assert "# Corpus Baseline Compare" in markdown
    assert "- Review hints:" in markdown
    assert (
        "  - `symbol-extraction`: review `src/callchain/languages/*.py`; cases: `sample`"
    ) in markdown
    assert (
        "  - `non-structural`: review `src/callchain/devtools/corpus.py`, "
        "`.github/workflows/corpus-baseline-compare.yml`; cases: `improved`"
    ) in markdown
    assert "| `sample` | `1.300` | `1.350` | `+3.8%` | `within_threshold` |" in markdown


def test_compare_reports_support_summary_only_mode_and_drift_gating(tmp_path):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    baseline.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "changed",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {"total_seconds": 1.0},
                    },
                    {
                        "name": "stable",
                        "path": "sample_repo",
                        "summary": {
                            "files": 1,
                            "functions": 1,
                            "classes": 0,
                            "edges": 0,
                            "chains": 0,
                            "parse_errors": 0,
                        },
                        "timings": {"total_seconds": 0.5},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    candidate.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "changed",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 4,
                            "classes": 1,
                            "edges": 3,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {"total_seconds": 99.0},
                    },
                    {
                        "name": "stable",
                        "path": "sample_repo",
                        "summary": {
                            "files": 1,
                            "functions": 1,
                            "classes": 0,
                            "edges": 0,
                            "chains": 0,
                            "parse_errors": 0,
                        },
                        "timings": {"total_seconds": 0.2},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    report = corpus.compare_reports(baseline, candidate, metric="summary")

    assert report["metric"] == "summary"
    assert report["fail_on_summary_drift"] is False
    assert report["summary_drift_cases"] == ["changed"]
    assert report["has_summary_drift"] is True
    by_name = {item["name"]: item for item in report["comparisons"]}
    assert by_name["changed"]["status"] == "changed"
    assert by_name["changed"]["baseline"] is None
    assert by_name["changed"]["candidate"] is None
    assert by_name["changed"]["delta"] is None
    assert by_name["changed"]["delta_pct"] is None
    assert by_name["changed"]["summary_delta"] == {
        "files": 0,
        "functions": 1,
        "classes": 1,
        "edges": 1,
        "chains": 0,
        "parse_errors": 0,
    }
    assert by_name["stable"]["status"] == "unchanged"
    rendered = corpus.format_compare_report(report)
    assert "Metric=summary, Max regression=15.0%" in rendered
    assert "changed: summary changed" in rendered
    assert "stable: summary unchanged" in rendered
    assert "functions +1" in rendered
    assert "classes +1" in rendered
    markdown = corpus.format_compare_markdown(report)
    assert "- Summary drift: `1 case(s)`" in markdown
    assert "| `changed` | `changed` | functions +1, classes +1, edges +1 |" in markdown
    assert "| `stable` | `unchanged` | No summary drift |" in markdown

    with pytest.raises(ValueError, match="summary drift detected"):
        corpus.compare_reports(baseline, candidate, metric="summary", fail_on_summary_drift=True)


def test_compare_reports_add_review_hint_overlap_for_changed_files(tmp_path):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    codeowners = tmp_path / "CODEOWNERS"
    baseline.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "changed",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {"total_seconds": 1.0},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    candidate.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "changed",
                        "path": "sample_repo",
                        "summary": {
                            "files": 3,
                            "functions": 4,
                            "classes": 1,
                            "edges": 3,
                            "chains": 2,
                            "parse_errors": 1,
                        },
                        "timings": {"total_seconds": 1.0},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    codeowners.write_text(
        "\n".join(
            [
                "# Example CODEOWNERS",
                "src/callchain/core/* @callchain-core",
                "src/callchain/core/callgraph.py @callchain-graph",
                "src/callchain/languages/*.py @callchain-languages",
                ".github/workflows/* @callchain-infra",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = corpus.compare_reports(
        baseline,
        candidate,
        metric="summary",
        changed_files=[
            "src/callchain/languages/python_lang.py",
            "src/callchain/core/callgraph.py",
            "src/callchain/core/chain_enum.py",
            ".github/workflows/corpus-baseline-compare.yml",
        ],
        codeowners_path=codeowners,
    )

    assert report["has_changed_files_context"] is True
    assert report["changed_files"] == [
        "src/callchain/languages/python_lang.py",
        "src/callchain/core/callgraph.py",
        "src/callchain/core/chain_enum.py",
        ".github/workflows/corpus-baseline-compare.yml",
    ]
    assert report["review_hints"] == [
        {
            "key": "discovery",
            "label": "discovery",
            "cases": ["changed"],
            "paths": ["src/callchain/languages/base.py", "src/callchain/core/callgraph.py"],
            "reason": "Review file discovery, skip-dir rules, path filtering, and language auto-detection.",
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
        },
        {
            "key": "symbol_extraction",
            "label": "symbol-extraction",
            "cases": ["changed"],
            "paths": ["src/callchain/languages/*.py"],
            "reason": "Review language parsers that extract functions, classes, methods, imports, and variables.",
            "matched_changed_files": ["src/callchain/languages/python_lang.py"],
        },
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["changed"],
            "paths": ["src/callchain/core/callgraph.py", "src/callchain/languages/*.py"],
            "reason": "Review raw call extraction and cross-file edge resolution.",
            "matched_changed_files": [
                "src/callchain/languages/python_lang.py",
                "src/callchain/core/callgraph.py",
            ],
        },
        {
            "key": "chain_enumeration",
            "label": "chain-enumeration",
            "cases": ["changed"],
            "paths": ["src/callchain/core/chain_enum.py"],
            "reason": "Review chain traversal, depth/count limits, and cross-file filtering.",
            "matched_changed_files": ["src/callchain/core/chain_enum.py"],
        },
        {
            "key": "parse_health",
            "label": "parse-health",
            "cases": ["changed"],
            "paths": ["src/callchain/languages/*.py", "src/callchain/core/callgraph.py"],
            "reason": "Review parser failures, parse-error collection, and file-level error handling.",
            "matched_changed_files": [
                "src/callchain/languages/python_lang.py",
                "src/callchain/core/callgraph.py",
            ],
        },
    ]
    assert report["owner_hints"] == [
        {
            "key": "discovery",
            "label": "discovery",
            "cases": ["changed"],
            "paths": ["src/callchain/languages/base.py", "src/callchain/core/callgraph.py"],
            "owners": ["@callchain-graph"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
            "ownerless_changed_files": [],
        },
        {
            "key": "symbol_extraction",
            "label": "symbol-extraction",
            "cases": ["changed"],
            "paths": ["src/callchain/languages/*.py"],
            "owners": ["@callchain-languages"],
            "matched_changed_files": ["src/callchain/languages/python_lang.py"],
            "ownerless_changed_files": [],
        },
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["changed"],
            "paths": ["src/callchain/core/callgraph.py", "src/callchain/languages/*.py"],
            "owners": ["@callchain-languages", "@callchain-graph"],
            "matched_changed_files": [
                "src/callchain/languages/python_lang.py",
                "src/callchain/core/callgraph.py",
            ],
            "ownerless_changed_files": [],
        },
        {
            "key": "chain_enumeration",
            "label": "chain-enumeration",
            "cases": ["changed"],
            "paths": ["src/callchain/core/chain_enum.py"],
            "owners": ["@callchain-core"],
            "matched_changed_files": ["src/callchain/core/chain_enum.py"],
            "ownerless_changed_files": [],
        },
        {
            "key": "parse_health",
            "label": "parse-health",
            "cases": ["changed"],
            "paths": ["src/callchain/languages/*.py", "src/callchain/core/callgraph.py"],
            "owners": ["@callchain-languages", "@callchain-graph"],
            "matched_changed_files": [
                "src/callchain/languages/python_lang.py",
                "src/callchain/core/callgraph.py",
            ],
            "ownerless_changed_files": [],
        },
    ]
    assert report["owner_focus"] == [
        {
            "owner": "@callchain-graph",
            "labels": ["discovery", "call-resolution", "parse-health"],
            "cases": ["changed"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
            "priority": "critical",
            "score": 18,
        },
        {
            "owner": "@callchain-core",
            "labels": ["chain-enumeration"],
            "cases": ["changed"],
            "matched_changed_files": ["src/callchain/core/chain_enum.py"],
            "priority": "critical",
            "score": 13,
        },
        {
            "owner": "@callchain-languages",
            "labels": ["symbol-extraction", "call-resolution", "parse-health"],
            "cases": ["changed"],
            "matched_changed_files": ["src/callchain/languages/python_lang.py"],
            "priority": "medium",
            "score": 14,
        },
    ]
    assert report["reviewer_candidates"] == [
        {
            "owner": "@callchain-graph",
            "kind": "user",
            "priority": "critical",
            "score": 18,
            "labels": ["discovery", "call-resolution", "parse-health"],
            "cases": ["changed"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
        },
        {
            "owner": "@callchain-core",
            "kind": "user",
            "priority": "critical",
            "score": 13,
            "labels": ["chain-enumeration"],
            "cases": ["changed"],
            "matched_changed_files": ["src/callchain/core/chain_enum.py"],
        },
        {
            "owner": "@callchain-languages",
            "kind": "user",
            "priority": "medium",
            "score": 14,
            "labels": ["symbol-extraction", "call-resolution", "parse-health"],
            "cases": ["changed"],
            "matched_changed_files": ["src/callchain/languages/python_lang.py"],
        },
    ]
    assert report["review_request_plan"] == {
        "users": ["@callchain-graph", "@callchain-core", "@callchain-languages"],
        "teams": [],
        "unsupported": [],
    }
    rendered = corpus.format_compare_report(report)
    assert "Changed files context=4 file(s)" in rendered
    assert "Review-request dry-run: users @callchain-graph, @callchain-core, @callchain-languages" in rendered
    assert "Owner focus:" in rendered
    assert (
        "critical @callchain-graph: review discovery, call-resolution, parse-health, cases: changed, "
        "touched: src/callchain/core/callgraph.py"
    ) in rendered
    assert (
        "call-resolution: review src/callchain/core/callgraph.py, src/callchain/languages/*.py, "
        "cases: changed, touched: src/callchain/languages/python_lang.py, src/callchain/core/callgraph.py"
    ) in rendered
    assert "Owner hints:" in rendered
    assert (
        "call-resolution: owners @callchain-languages, @callchain-graph, cases: changed, "
        "touched: src/callchain/languages/python_lang.py, src/callchain/core/callgraph.py"
    ) in rendered
    markdown = corpus.format_compare_markdown(report)
    assert "- Changed files context: `4 file(s)`" in markdown
    assert "- Review-request dry-run: users `@callchain-graph`, `@callchain-core`, `@callchain-languages`" in markdown
    assert "- Owner focus:" in markdown
    assert (
        "  - `critical` `@callchain-graph`: review `discovery`, `call-resolution`, `parse-health`; "
        "cases: `changed`; touched changed files: `src/callchain/core/callgraph.py`"
    ) in markdown
    assert (
        "  - `call-resolution`: review `src/callchain/core/callgraph.py`, `src/callchain/languages/*.py`; "
        "cases: `changed`; touched changed files: `src/callchain/languages/python_lang.py`, "
        "`src/callchain/core/callgraph.py`"
    ) in markdown
    assert "- Owner hints:" in markdown
    assert (
        "  - `call-resolution`: owners `@callchain-languages`, `@callchain-graph`; cases: `changed`; "
        "touched changed files: `src/callchain/languages/python_lang.py`, `src/callchain/core/callgraph.py`"
    ) in markdown


def test_compare_changed_files_loader_supports_json_object_and_text(tmp_path):
    missing_path = tmp_path / "missing-changed-files.json"
    list_path = tmp_path / "changed-files-list.json"
    list_path.write_text(json.dumps(["src/callchain/core/callgraph.py"]), encoding="utf-8")
    json_path = tmp_path / "changed-files.json"
    json_path.write_text(
        json.dumps({"files": ["src/callchain/core/callgraph.py", "src/callchain/core/callgraph.py", "./README.md"]}),
        encoding="utf-8",
    )
    text_path = tmp_path / "changed-files.txt"
    text_path.write_text("src/callchain/core/chain_enum.py\n\n./src/callchain/core/chain_enum.py\n", encoding="utf-8")
    bad_path = tmp_path / "changed-files-bad.json"
    bad_path.write_text(json.dumps({"files": [1]}), encoding="utf-8")
    wrong_root_path = tmp_path / "changed-files-wrong-root.json"
    wrong_root_path.write_text(json.dumps({"paths": ["src/callchain/core/callgraph.py"]}), encoding="utf-8")

    with pytest.raises(ValueError, match="does not exist"):
        corpus._load_changed_files(missing_path)
    assert corpus._load_changed_files(list_path) == ["src/callchain/core/callgraph.py"]
    assert corpus._load_changed_files(json_path) == ["src/callchain/core/callgraph.py", "README.md"]
    assert corpus._load_changed_files(text_path) == ["src/callchain/core/chain_enum.py"]
    with pytest.raises(ValueError, match="Changed-files entries must be strings"):
        corpus._load_changed_files(bad_path)
    with pytest.raises(ValueError, match="must be a JSON list"):
        corpus._load_changed_files(wrong_root_path)


def test_compare_reports_rejects_invalid_inputs_and_regressions(tmp_path):
    missing = tmp_path / "missing.json"
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-json", encoding="utf-8")
    wrong_root = tmp_path / "wrong-root.json"
    wrong_root.write_text(json.dumps(["oops"]), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    mismatch = tmp_path / "mismatch.json"
    regression = tmp_path / "regression.json"
    baseline.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "sample",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {
                            "build_seconds": {"median": 1.0},
                            "chain_seconds": {"median": 0.2},
                            "analysis_seconds": {"median": 0.1},
                            "total_seconds": {"median": 1.3},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    candidate.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "sample",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {
                            "build_seconds": {"median": 1.5},
                            "chain_seconds": {"median": 0.2},
                            "analysis_seconds": {"median": 0.1},
                            "total_seconds": {"median": 2.0},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    mismatch.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "other",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {"total_seconds": {"median": 1.0}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    regression.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported compare metric"):
        corpus.compare_reports(baseline, candidate, metric="bogus")
    with pytest.raises(ValueError, match="0 or greater"):
        corpus.compare_reports(baseline, candidate, max_regression_pct=-1)
    with pytest.raises(ValueError, match="does not exist"):
        corpus.compare_reports(missing, candidate)
    with pytest.raises(ValueError, match="not valid JSON"):
        corpus.compare_reports(invalid, candidate)
    with pytest.raises(ValueError, match="JSON object"):
        corpus.compare_reports(wrong_root, candidate)
    with pytest.raises(ValueError, match="case mismatch"):
        corpus.compare_reports(baseline, mismatch)
    with pytest.raises(ValueError, match="regressed by"):
        corpus.compare_reports(baseline, regression, max_regression_pct=10.0)


def test_compare_internal_helpers_cover_defensive_paths(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({}), encoding="utf-8")
    codeowners = tmp_path / "CODEOWNERS"
    codeowners.write_text(
        "\n".join(
            [
                "# comment",
                "src/callchain/core/* @callchain-core",
                "src/callchain/core/callgraph.py @callchain-graph",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    bad_codeowners = tmp_path / "BAD_CODEOWNERS"
    bad_codeowners.write_text("src/callchain/core/*\n", encoding="utf-8")
    duplicate = {
        "cases": [
            {"name": "dup", "path": "a", "summary": {}, "timings": {}},
            {"name": "dup", "path": "b", "summary": {}, "timings": {}},
        ]
    }

    with pytest.raises(ValueError, match="non-empty 'cases' or 'projects'"):
        corpus._normalize_report_cases({}, label="baseline")
    with pytest.raises(ValueError, match="entries must be objects"):
        corpus._normalize_report_cases({"cases": [1]}, label="baseline")
    with pytest.raises(ValueError, match="non-empty 'name'"):
        corpus._normalize_report_cases({"cases": [{"name": "", "path": "a", "summary": {}, "timings": {}}]}, label="baseline")
    with pytest.raises(ValueError, match="duplicate case name"):
        corpus._normalize_report_cases(duplicate, label="baseline")
    with pytest.raises(ValueError, match="missing a summary object"):
        corpus._normalize_report_cases({"cases": [{"name": "a", "path": "a", "timings": {}}]}, label="baseline")
    with pytest.raises(ValueError, match="missing a timings object"):
        corpus._normalize_report_cases({"cases": [{"name": "a", "path": "a", "summary": {}}]}, label="baseline")
    with pytest.raises(ValueError, match="missing a non-empty path"):
        corpus._normalize_report_cases({"cases": [{"name": "a", "path": "", "summary": {}, "timings": {}}]}, label="baseline")
    with pytest.raises(ValueError, match="missing median timing"):
        corpus._extract_metric_value({"timings": {"total_seconds": {}}}, "total_seconds", label="baseline", case_name="a")
    with pytest.raises(ValueError, match="missing numeric metric"):
        corpus._extract_metric_value({"timings": {}}, "total_seconds", label="baseline", case_name="a")
    with pytest.raises(ValueError, match="must be an integer"):
        corpus._compute_summary_delta({"files": "two"}, {"files": 2})
    assert corpus._summary_has_drift({"files": 1, "functions": 0}) is True
    assert corpus._summary_has_drift({"files": 0, "functions": 0}) is False
    assert corpus._format_summary_delta({"files": 1, "functions": 0, "edges": -2}) == ["files +1", "edges -2"]
    assert corpus._percent_change(0.0, 0.0) == 0.0
    assert corpus._percent_change(0.0, 1.0) == float("inf")
    assert corpus._load_report_json(empty) == {}
    assert corpus._load_codeowners_rules(codeowners) == [
        {"pattern": "src/callchain/core/*", "owners": ["@callchain-core"], "line": 2},
        {"pattern": "src/callchain/core/callgraph.py", "owners": ["@callchain-graph"], "line": 3},
    ]
    assert corpus._match_codeowners(
        "src/callchain/core/callgraph.py",
        corpus._load_codeowners_rules(codeowners),
    ) == ["@callchain-graph"]
    assert corpus._match_codeowners(
        "src/callchain/core/chain_enum.py",
        corpus._load_codeowners_rules(codeowners),
    ) == ["@callchain-core"]
    assert corpus._match_codeowners("README.md", corpus._load_codeowners_rules(codeowners)) == []
    assert corpus._build_compare_owner_hints(
        [
            {
                "key": "call_resolution",
                "label": "call-resolution",
                "cases": ["sample"],
                "paths": ["src/callchain/core/*.py"],
                "matched_changed_files": [
                    "src/callchain/core/callgraph.py",
                    "src/callchain/core/chain_enum.py",
                    "docs/release-notes.md",
                ],
            }
        ],
        corpus._load_codeowners_rules(codeowners),
    ) == [
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["sample"],
            "paths": ["src/callchain/core/*.py"],
            "owners": ["@callchain-graph", "@callchain-core"],
            "matched_changed_files": [
                "src/callchain/core/callgraph.py",
                "src/callchain/core/chain_enum.py",
                "docs/release-notes.md",
            ],
            "ownerless_changed_files": ["docs/release-notes.md"],
        }
    ]
    assert corpus._build_compare_owner_focus(
        [
            {
                "key": "symbol_extraction",
                "label": "symbol-extraction",
                "cases": ["sample"],
                "paths": ["src/callchain/languages/*.py"],
                "matched_changed_files": ["src/callchain/languages/python_lang.py"],
            },
            {
                "key": "call_resolution",
                "label": "call-resolution",
                "cases": ["sample"],
                "paths": ["src/callchain/core/callgraph.py", "src/callchain/languages/*.py"],
                "matched_changed_files": [
                    "src/callchain/languages/python_lang.py",
                    "src/callchain/core/callgraph.py",
                ],
            },
        ],
        corpus._load_codeowners_rules(codeowners),
    ) == [
        {
            "owner": "@callchain-graph",
            "labels": ["call-resolution"],
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
            "priority": "critical",
            "score": 14,
        },
    ]
    assert corpus._build_compare_owner_hints(
        [
            {
                "key": "chain_enumeration",
                "label": "chain-enumeration",
                "cases": ["sample"],
                "paths": ["src/callchain/core/*.py"],
                "matched_changed_files": [
                    "src/callchain/core/callgraph.py",
                    "src/callchain/core/chain_enum.py",
                ],
            }
        ],
        [{"pattern": "src/callchain/core/*", "owners": ["@callchain-core"], "line": 1}],
    ) == [
        {
            "key": "chain_enumeration",
            "label": "chain-enumeration",
            "cases": ["sample"],
            "paths": ["src/callchain/core/*.py"],
            "owners": ["@callchain-core"],
            "matched_changed_files": [
                "src/callchain/core/callgraph.py",
                "src/callchain/core/chain_enum.py",
            ],
            "ownerless_changed_files": [],
        }
    ]
    assert corpus._build_compare_owner_hints([], corpus._load_codeowners_rules(codeowners)) == []
    assert corpus._build_compare_owner_hints(
        [{"key": "call_resolution", "label": "call-resolution", "cases": ["sample"], "paths": [], "matched_changed_files": []}],
        [],
    ) == []
    assert corpus._build_compare_owner_focus([], corpus._load_codeowners_rules(codeowners)) == []
    assert corpus._build_compare_owner_focus(
        [{"key": "call_resolution", "label": "call-resolution", "cases": ["sample"], "paths": [], "matched_changed_files": []}],
        [],
    ) == []
    with pytest.raises(ValueError, match="CODEOWNERS file .* does not exist"):
        corpus._load_codeowners_rules(tmp_path / "MISSING_CODEOWNERS")
    assert corpus._codeowners_pattern_matches("src/callchain/core/callgraph.py", "/src/callchain/core/*") is True
    assert corpus._codeowners_pattern_matches("src/callchain/core/callgraph.py", "src/callchain/core/") is True
    assert corpus._codeowners_pattern_matches("src/callchain/core/callgraph.py", "callgraph.py") is True
    assert corpus._codeowners_pattern_matches("./src/callchain/core/callgraph.py", "src/callchain/core/*.py") is True
    assert corpus._codeowners_pattern_matches("src/callchain/core/callgraph.py", "core") is True
    assert corpus._codeowners_pattern_matches("vendor/src/callchain/core/callgraph.py", "src/callchain/core/callgraph.py") is True
    assert corpus._codeowners_pattern_matches("src/callchain/core/callgraph.py", "/") is False
    assert corpus._codeowners_pattern_matches("src/callchain/core/callgraph.py", "/docs/*.md") is False
    with pytest.raises(ValueError, match="CODEOWNERS line 1 must include a pattern and at least one owner"):
        corpus._load_codeowners_rules(bad_codeowners)

    owner_report = {
        "baseline": "build/base.json",
        "candidate": "build/head.json",
        "metric": "summary",
        "max_regression_pct": 15.0,
        "has_changed_files_context": True,
        "changed_files": ["src/callchain/core/callgraph.py", "docs/release-notes.md"],
        "review_hints": [],
        "owner_hints": [
            {
                "key": "call_resolution",
                "label": "call-resolution",
                "cases": ["sample"],
                "paths": ["src/callchain/core/*.py"],
                "owners": ["@callchain-graph"],
                "matched_changed_files": [
                    "src/callchain/core/callgraph.py",
                    "docs/release-notes.md",
                ],
                "ownerless_changed_files": ["docs/release-notes.md"],
            }
        ],
        "summary_drift_cases": ["sample"],
        "comparisons": [{"name": "sample", "status": "changed", "summary_bits": ["edges +1"]}],
    }
    assert "ownerless: docs/release-notes.md" in corpus.format_compare_report(owner_report)
    assert "ownerless changed files: `docs/release-notes.md`" in corpus.format_compare_markdown(owner_report)
    assert corpus._build_compare_reviewer_candidates(
        [
            {
                "owner": "@callchain-graph",
                "labels": ["call-resolution"],
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/core/callgraph.py"],
                "priority": "critical",
                "score": 12,
            },
            {
                "owner": "@callchain/core-reviewers",
                "labels": ["chain-enumeration"],
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/core/chain_enum.py"],
                "priority": "critical",
                "score": 11,
            },
            {
                "owner": "docs-team",
                "labels": ["non-structural"],
                "cases": ["sample"],
                "matched_changed_files": ["README.md"],
                "priority": "low",
                "score": 2,
            },
        ]
    ) == [
        {
            "owner": "@callchain-graph",
            "kind": "user",
            "priority": "critical",
            "score": 12,
            "labels": ["call-resolution"],
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
        },
        {
            "owner": "@callchain/core-reviewers",
            "kind": "team",
            "priority": "critical",
            "score": 11,
            "labels": ["chain-enumeration"],
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/core/chain_enum.py"],
        },
        {
            "owner": "docs-team",
            "kind": "unsupported",
            "priority": "low",
            "score": 2,
            "labels": ["non-structural"],
            "cases": ["sample"],
            "matched_changed_files": ["README.md"],
        },
    ]
    assert corpus._build_compare_review_request_plan(
        [
            {
                "owner": "@callchain-graph",
                "kind": "user",
                "priority": "critical",
                "score": 12,
                "labels": ["call-resolution"],
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/core/callgraph.py"],
            },
            {
                "owner": "@callchain/core-reviewers",
                "kind": "team",
                "priority": "critical",
                "score": 11,
                "labels": ["chain-enumeration"],
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/core/chain_enum.py"],
            },
            {
                "owner": "docs-team",
                "kind": "unsupported",
                "priority": "low",
                "score": 2,
                "labels": ["non-structural"],
                "cases": ["sample"],
                "matched_changed_files": ["README.md"],
            },
            {
                "owner": "@callchain-graph",
                "kind": "user",
                "priority": "critical",
                "score": 12,
                "labels": ["call-resolution"],
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/core/callgraph.py"],
            },
        ]
    ) == {
        "users": ["@callchain-graph"],
        "teams": ["@callchain/core-reviewers"],
        "unsupported": ["docs-team"],
    }
    assert corpus._classify_review_owner("@callchain-graph") == "user"
    assert corpus._classify_review_owner("@callchain/core-reviewers") == "team"
    assert corpus._classify_review_owner("docs-team") == "unsupported"
    assert corpus._format_compare_review_request_plan_line(
        {
            "users": ["@callchain-graph"],
            "teams": ["@callchain/core-reviewers"],
            "unsupported": ["docs-team"],
        }
    ) == "users @callchain-graph; teams @callchain/core-reviewers; unsupported docs-team"
    assert corpus._format_compare_review_request_plan_markdown(
        {
            "users": ["@callchain-graph"],
            "teams": ["@callchain/core-reviewers"],
            "unsupported": ["docs-team"],
        }
    ) == "users `@callchain-graph`; teams `@callchain/core-reviewers`; unsupported `docs-team`"
    assert corpus._format_compare_review_request_plan_line({"users": [], "teams": [], "unsupported": []}) == "none"
    assert (
        corpus._format_compare_review_request_plan_markdown({"users": [], "teams": [], "unsupported": []}) == "`none`"
    )
    assert corpus._compare_owner_focus_priority(["src/callchain/core/callgraph.py"]) == "critical"
    assert corpus._compare_owner_focus_priority(["src/callchain/core/cache.py"]) == "high"
    assert corpus._compare_owner_focus_priority(["src/callchain/languages/python_lang.py"]) == "medium"
    assert corpus._compare_owner_focus_priority(["README.md"]) == "low"
    assert corpus._compare_owner_focus_score(
        ["src/callchain/core/callgraph.py"],
        ["call-resolution", "parse-health"],
        ["sample"],
    ) == 16
    assert corpus._changed_file_weight("src/callchain/core/chain_enum.py") == 8
    assert corpus._changed_file_weight("src/callchain/core/cache.py") == 7
    assert corpus._changed_file_weight("src/callchain/languages/base.py") == 6
    assert corpus._changed_file_weight("src/callchain/devtools/corpus.py") == 3
    assert corpus._changed_file_weight(".github/workflows/corpus-baseline-compare.yml") == 2
    items = ["symbol-extraction"]
    corpus._append_unique(items, "symbol-extraction")
    corpus._append_unique(items, "call-resolution")
    assert items == ["symbol-extraction", "call-resolution"]


def test_compare_main_and_wrapper_script(tmp_path):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    output = tmp_path / "compare.json"
    text_output = tmp_path / "compare.txt"
    changed_files = tmp_path / "changed-files.json"
    codeowners = tmp_path / "CODEOWNERS"
    baseline.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "sample",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {
                            "build_seconds": 1.0,
                            "chain_seconds": 0.2,
                            "analysis_seconds": 0.1,
                            "total_seconds": 1.3,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    candidate.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "sample",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {
                            "build_seconds": 1.0,
                            "chain_seconds": 0.2,
                            "analysis_seconds": 0.1,
                            "total_seconds": 1.31,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    changed_files.write_text(json.dumps(["src/callchain/core/callgraph.py"]), encoding="utf-8")
    codeowners.write_text("src/callchain/core/* @callchain-core\n", encoding="utf-8")

    assert corpus.main(
        [
            "compare",
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--changed-files",
            str(changed_files),
            "--codeowners",
            str(codeowners),
            "--json",
            "--output",
            str(output),
        ]
    ) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["metric"] == "total_seconds"
    assert payload["comparisons"][0]["status"] == "within_threshold"
    assert payload["owner_hints"] == []
    assert payload["reviewer_candidates"] == []
    assert payload["review_request_plan"] == {"users": [], "teams": [], "unsupported": []}

    assert corpus.main(
        [
            "compare",
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--output",
            str(text_output),
        ]
    ) == 0
    assert "Corpus report comparison passed" in text_output.read_text(encoding="utf-8")

    repo_root = Path(__file__).resolve().parents[1]
    compare_proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/compare_corpus_reports.py"),
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert compare_proc.returncode == 0
    assert "Corpus report comparison passed" in compare_proc.stdout


def test_compare_main_supports_markdown_output(tmp_path):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    output = tmp_path / "compare-summary.md"
    baseline.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "sample",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {"total_seconds": 1.0},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    candidate.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "sample",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 4,
                            "classes": 0,
                            "edges": 3,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {"total_seconds": 9.0},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert corpus.main(
        [
            "compare",
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--metric",
            "summary",
            "--markdown",
            "--output",
            str(output),
        ]
    ) == 0
    rendered = output.read_text(encoding="utf-8")
    assert rendered.startswith("# Corpus Baseline Compare")
    assert "| `sample` | `changed` | functions +1, edges +1 |" in rendered


def test_compare_main_supports_summary_only_mode(tmp_path):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    output = tmp_path / "compare-summary.json"
    baseline.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "sample",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 3,
                            "classes": 0,
                            "edges": 2,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {"total_seconds": 1.0},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    candidate.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "sample",
                        "path": "sample_repo",
                        "summary": {
                            "files": 2,
                            "functions": 4,
                            "classes": 0,
                            "edges": 3,
                            "chains": 1,
                            "parse_errors": 0,
                        },
                        "timings": {"total_seconds": 9.0},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert corpus.main(
        [
            "compare",
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--metric",
            "summary",
            "--json",
            "--output",
            str(output),
        ]
    ) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["metric"] == "summary"
    assert payload["comparisons"][0]["status"] == "changed"
    assert payload["comparisons"][0]["baseline"] is None

    with pytest.raises(ValueError, match="summary comparison failed"):
        corpus.main(
            [
                "compare",
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--metric",
                "summary",
                "--fail-on-summary-drift",
            ]
        )


def test_corpus_wrapper_scripts_and_module_execution(tmp_path):
    project = _write_sample_project(tmp_path)
    manifest = tmp_path / "corpus.toml"
    manifest.write_text(
        "[[projects]]\n"
        'name = "sample"\n'
        f'path = "{project.name}"\n'
        "min_files = 2\n"
        "min_functions = 3\n"
        "min_edges = 2\n"
        "min_chains = 1\n",
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[1]

    check_proc = subprocess.run(
        [sys.executable, str(repo_root / "scripts/check_corpus.py"), "--manifest", str(manifest)],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert check_proc.returncode == 0
    assert "Corpus regression check passed" in check_proc.stdout

    benchmark_proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/benchmark_corpus.py"),
            "--manifest",
            str(manifest),
            "--iterations",
            "1",
            "--warmup",
            "0",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert benchmark_proc.returncode == 0
    assert "Corpus benchmark report" in benchmark_proc.stdout

    argv = sys.argv[:]
    sys.argv = ["callchain.devtools.corpus", "check", "--manifest", str(manifest)]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with pytest.raises(SystemExit, match="0"):
                runpy.run_module("callchain.devtools.corpus", run_name="__main__")
    finally:
        sys.argv = argv


def test_repository_corpus_manifest_passes_on_local_samples():
    runs = corpus.check_manifest(Path("test_repos/corpus.toml"))

    assert [run.name for run in runs] == [
        "examples-smoke-repo",
        "test-smoke-repo",
        "click-src",
    ]


def test_repository_source_registry_passes_on_local_samples():
    report = corpus.source_inventory(Path("test_repos/corpus.toml"), Path("test_repos/sources.toml"))

    assert [entry["name"] for entry in report["entries"]] == [
        "click-src",
        "examples-smoke-repo",
        "test-smoke-repo",
    ]


def test_repository_source_registry_sync_is_idempotent(tmp_path):
    registry_copy = tmp_path / "sources.toml"
    registry_copy.write_text(Path("test_repos/sources.toml").read_text(encoding="utf-8"), encoding="utf-8")

    report = corpus.sync_source_registry(Path("test_repos/corpus.toml"), registry_copy, dry_run=True)

    assert report["changed"] is False


def _write_sample_project(tmp_path: Path) -> Path:
    project = tmp_path / "sample_repo"
    src = project / "src"
    tests = project / "tests"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    (src / "helpers.py").write_text(
        "def helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    (src / "app.py").write_text(
        "from helpers import helper\n\n"
        "def helper_alias():\n"
        "    return helper()\n\n"
        "def entry():\n"
        "    return helper_alias()\n",
        encoding="utf-8",
    )
    (tests / "test_app.py").write_text(
        "from src.app import entry\n\n"
        "def test_entry():\n"
        "    assert entry() == 1\n",
        encoding="utf-8",
    )
    return project


def _write_corpus_source_fixture(tmp_path: Path) -> tuple[Path, Path]:
    local_repo = tmp_path / "local_repo"
    vendored_root = tmp_path / "vendored_repo"
    vendored_src = vendored_root / "src"
    vendored_archive = tmp_path / "vendored-sample.tar.gz"
    local_repo.mkdir(parents=True, exist_ok=True)
    vendored_src.mkdir(parents=True, exist_ok=True)
    (tmp_path / "LICENSE").write_text("Apache-2.0\n", encoding="utf-8")
    (local_repo / "app.py").write_text("def local_entry():\n    return 1\n", encoding="utf-8")
    (vendored_root / "LICENSE.txt").write_text("BSD-3-Clause\n", encoding="utf-8")
    (vendored_root / "pyproject.toml").write_text(
        '[project]\nname = "vendored-sample"\nversion = "1.2.3"\nlicense = "BSD-3-Clause"\n',
        encoding="utf-8",
    )
    (vendored_src / "vendor.py").write_text("def vendor_entry():\n    return 2\n", encoding="utf-8")
    vendored_archive.write_bytes(b"vendored fixture archive\n")

    manifest = tmp_path / "corpus.toml"
    manifest.write_text(
        '[[projects]]\nname = "local-sample"\npath = "local_repo"\nmin_files = 1\n\n'
        '[[projects]]\nname = "vendored-sample"\npath = "vendored_repo/src"\nmin_files = 1\n',
        encoding="utf-8",
    )
    registry = tmp_path / "sources.toml"
    registry.write_text(
        '[[sources]]\n'
        'name = "local-sample"\n'
        'kind = "local"\n'
        'analyzed_path = "local_repo"\n'
        'root_path = "local_repo"\n'
        'license_spdx = "Apache-2.0"\n'
        'license_file = "LICENSE"\n'
        'content_sha256 = "local-hash"\n'
        'notes = "local fixture"\n\n'
        '[[sources]]\n'
        'name = "vendored-sample"\n'
        'kind = "vendored"\n'
        'analyzed_path = "vendored_repo/src"\n'
        'root_path = "vendored_repo"\n'
        'license_spdx = "BSD-3-Clause"\n'
        'license_file = "vendored_repo/LICENSE.txt"\n'
        'upstream_url = "https://example.com/vendor/sample"\n'
        'version = "1.2.3"\n'
        'source_ref = "fixture-ref"\n'
        f'archive_url = "{vendored_archive.as_uri()}"\n'
        'archive_sha256 = "vendored-archive-hash"\n'
        'content_sha256 = "vendored-hash"\n'
        'notes = "vendored fixture"\n',
        encoding="utf-8",
    )
    local_hash = corpus._compute_tree_sha256(local_repo)
    vendored_hash = corpus._compute_tree_sha256(vendored_root)
    vendored_archive_hash = corpus._compute_file_sha256(vendored_archive)
    registry.write_text(
        registry.read_text(encoding="utf-8")
        .replace("local-hash", local_hash)
        .replace("vendored-archive-hash", vendored_archive_hash)
        .replace("vendored-hash", vendored_hash),
        encoding="utf-8",
    )
    return manifest, registry


def _write_archive_materialize_fixture(tmp_path: Path) -> dict[str, Path | str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    vendored_root = tmp_path / "vendored_snapshot"
    vendored_src = vendored_root / "src"
    vendored_src.mkdir(parents=True, exist_ok=True)
    (vendored_root / "LICENSE.txt").write_text("BSD-3-Clause\n", encoding="utf-8")
    (vendored_root / "pyproject.toml").write_text(
        '[project]\nname = "vendored-sample"\nversion = "1.0.0"\nlicense = "BSD-3-Clause"\n',
        encoding="utf-8",
    )
    (vendored_root / "STALE.txt").write_text("stale\n", encoding="utf-8")
    (vendored_src / "vendor.py").write_text("def vendor_entry():\n    return 0\n", encoding="utf-8")

    ref = "release-2"
    archives_dir = tmp_path / "archives"
    archives_dir.mkdir()
    archive_path = archives_dir / f"{ref}.tar.gz"
    archive_path.write_bytes(
        _build_tar_archive(
            {
                f"vendored-sample-{ref}/LICENSE.txt": "BSD-3-Clause\n",
                f"vendored-sample-{ref}/pyproject.toml": '[project]\nname = "vendored-sample"\nversion = "2.0.0"\nlicense = "BSD-3-Clause"\n',
                f"vendored-sample-{ref}/src/vendor.py": "def vendor_entry():\n    return 2\n",
            }
        )
    )

    manifest = tmp_path / "corpus.toml"
    manifest.write_text(
        '[[projects]]\nname = "vendored-sample"\npath = "vendored_snapshot/src"\nmin_files = 1\n',
        encoding="utf-8",
    )
    registry = tmp_path / "sources.toml"
    registry.write_text(
        '[[sources]]\n'
        'name = "vendored-sample"\n'
        'kind = "vendored"\n'
        'analyzed_path = "vendored_snapshot/src"\n'
        'root_path = "vendored_snapshot"\n'
        'license_spdx = "BSD-3-Clause"\n'
        'license_file = "vendored_snapshot/LICENSE.txt"\n'
        'upstream_url = "https://example.com/vendor/sample"\n'
        'version = "1.0.0"\n'
        'source_ref = "release-1"\n'
        f'archive_url = "{archives_dir.as_uri()}/{{ref}}.tar.gz"\n'
        'archive_sha256 = "old-archive-sha"\n'
        'content_sha256 = "old-content-sha"\n'
        'notes = "archive-backed vendored fixture"\n',
        encoding="utf-8",
    )
    registry.write_text(
        registry.read_text(encoding="utf-8")
        .replace("old-content-sha", corpus._compute_tree_sha256(vendored_root)),
        encoding="utf-8",
    )
    return {
        "manifest": manifest,
        "registry": registry,
        "root": vendored_root,
        "archive_path": archive_path,
        "ref": ref,
    }


def _write_git_refresh_fixture(tmp_path: Path) -> dict[str, Path | str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "vendored_checkout"
    src_dir = checkout / "src"
    archives = tmp_path / "archives"
    archives.mkdir()

    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.name", "CallChain Tests")
    _git(seed, "config", "user.email", "tests@example.com")
    (seed / "LICENSE.txt").write_text("BSD-3-Clause\n", encoding="utf-8")
    (seed / "pyproject.toml").write_text(
        '[project]\nname = "vendored-sample"\nversion = "1.0.0"\nlicense = "BSD-3-Clause"\n',
        encoding="utf-8",
    )
    (seed / "src").mkdir()
    (seed / "src" / "vendor.py").write_text("def vendor_entry():\n    return 1\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "initial fixture")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "main")
    initial_commit = _git(seed, "rev-parse", "HEAD")
    initial_archive = archives / f"{initial_commit}.tar.gz"
    _git(seed, "archive", "--format=tar.gz", f"--output={initial_archive}", initial_commit)

    _git(tmp_path, "clone", str(remote), str(checkout))

    manifest = tmp_path / "corpus.toml"
    manifest.write_text(
        '[[projects]]\nname = "vendored-sample"\npath = "vendored_checkout/src"\nmin_files = 1\n',
        encoding="utf-8",
    )
    registry = tmp_path / "sources.toml"
    checkout_hash = corpus._compute_tree_sha256(checkout)
    registry.write_text(
        '[[sources]]\n'
        'name = "vendored-sample"\n'
        'kind = "vendored"\n'
        'analyzed_path = "vendored_checkout/src"\n'
        'root_path = "vendored_checkout"\n'
        'license_spdx = "BSD-3-Clause"\n'
        'license_file = "vendored_checkout/LICENSE.txt"\n'
        'upstream_url = "https://example.com/vendor/sample"\n'
        'version = "1.0.0"\n'
        f'source_ref = "{initial_commit}"\n'
        f'archive_url = "{archives.as_uri()}/{{ref}}.tar.gz"\n'
        f'archive_sha256 = "{corpus._compute_file_sha256(initial_archive)}"\n'
        f'content_sha256 = "{checkout_hash}"\n'
        'notes = "git-backed vendored fixture"\n',
        encoding="utf-8",
    )

    (seed / "pyproject.toml").write_text(
        '[project]\nname = "vendored-sample"\nversion = "1.1.0"\nlicense = "BSD-3-Clause"\n',
        encoding="utf-8",
    )
    (seed / "src" / "vendor.py").write_text("def vendor_entry():\n    return 2\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "update fixture")
    _git(seed, "push", "origin", "main")
    updated_commit = _git(seed, "rev-parse", "HEAD")
    updated_archive = archives / f"{updated_commit}.tar.gz"
    _git(seed, "archive", "--format=tar.gz", f"--output={updated_archive}", updated_commit)

    return {
        "manifest": manifest,
        "registry": registry,
        "remote": remote,
        "checkout": checkout,
        "src_dir": src_dir,
        "archive_template": f"{archives.as_uri()}/{{ref}}.tar.gz",
        "initial_archive": initial_archive,
        "updated_archive": updated_archive,
        "initial_commit": initial_commit,
        "updated_commit": updated_commit,
    }


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def _build_tar_archive(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            payload = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()
