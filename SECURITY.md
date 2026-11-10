# Security Policy

## Supported Versions

The latest released version is the supported security target. Older versions may not receive fixes.

## Reporting A Vulnerability

Please do not open public issues for security-sensitive reports.

When reporting a vulnerability, include:

- affected version
- impact summary
- reproduction steps or proof of concept
- any suggested remediation if available

Maintainers should acknowledge receipt promptly, confirm scope, and coordinate a fix before public disclosure.

## Security Automation

This repository is set up to run:

- CodeQL analysis for the Python codebase and GitHub Actions workflows
- dependency review on pull requests. The check enforces findings whenever the dependency graph compare API is reachable. If that API returns the repository-level refusal (dependency graph disabled), the workflow warns and continues instead of failing the job; enable Dependency graph under Settings → Code security to turn enforcement on. Other failures still fail the job.
- scheduled Python dependency audits with `pip-audit`
- CycloneDX SBOM export as a workflow artifact for maintainers

These automated checks help catch common issues early, but they do not replace responsible private disclosure for security-sensitive reports.
