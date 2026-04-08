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
- dependency review on pull requests
- scheduled Python dependency audits with `pip-audit`
- CycloneDX SBOM export as a workflow artifact for maintainers

These automated checks help catch common issues early, but they do not replace responsible private disclosure for security-sensitive reports.
