# Security Policy

NovelMind processes user-provided long-form text and includes an embedded Agent/Skill runtime, model integrations, authenticated APIs, retrieval infrastructure, and optional external service access. Security reports that cross trust boundaries are taken seriously.

## Supported Versions

Security fixes are applied to the current default branch. Older commits, experimental branches, local forks, and unreleased prototypes are not guaranteed to receive security updates.

## Reporting a Vulnerability

Please do **not** disclose exploitable security issues in a public GitHub issue.

Preferred reporting path:

1. Use GitHub's private vulnerability reporting / Security Advisory flow for this repository when available.
2. If private reporting is unavailable, contact the maintainer through the GitHub profile and request a private reporting channel. Do not include exploit details, secrets, tokens, private data, or a working proof of concept in a public issue.

A useful report should include:

- affected component and commit/version;
- attack prerequisites and trust boundary crossed;
- minimal reproduction steps;
- expected vs. observed behavior;
- realistic impact;
- suggested mitigation, if known.

Please avoid accessing data that is not yours, disrupting services, persisting access, or exfiltrating credentials while testing.

## Security Model

NovelMind intentionally separates deterministic application authority from agent orchestration.

Current Agent Runtime design:

- the embedded agent is an orchestrator, not a source of truth or database administrator;
- Pi's default coding capabilities such as shell execution, arbitrary commands, and general file editing are disabled in the current runtime;
- the agent is restricted to an explicit allowlist of NovelMind domain tools;
- the current domain tool surface is read-only;
- authorization, ownership checks, spoiler/cutoff rules, budgets, timeouts, and output limits are enforced server-side;
- Skill definitions are loaded fail-closed and must declare allowed tools, permissions, budgets, approval requirements, and input/output schemas;
- ambient/global Skill discovery is disabled;
- third-party packages and future MCP integrations are expected to use explicit qualification, pinning, permission manifests, and allowlists rather than ambient machine configuration;
- higher-impact write actions require explicit security review and must not silently bypass application authority.

These controls reduce risk but do not eliminate vulnerabilities.

## High-Value Vulnerability Classes

Reports are especially useful when they demonstrate a realistic boundary bypass involving one or more of the following areas.

### Agent, Prompt, and Tool Boundaries

- prompt injection from imported novel text, retrieved evidence, model output, or external content that causes unauthorized actions;
- bypass of tool allowlists, Skill permissions, approval requirements, budgets, or output constraints;
- model-controlled arguments reaching privileged application behavior without deterministic validation;
- confused-deputy behavior where an agent acts with authority broader than the requesting user;
- unsafe future write-tool or MCP behavior.

### Authentication and Authorization

- IDOR or cross-user access to novels, chapters, model configuration, narrative memory, artifacts, jobs, or agent runs;
- authentication/session bypass;
- privilege escalation;
- missing ownership checks in internal or externally reachable APIs.

### Secrets and Sensitive Data

- leakage of API keys, access tokens, session credentials, encryption keys, environment variables, or private user content;
- secrets exposed through logs, errors, model context, agent traces, generated artifacts, or external requests;
- weaknesses in encryption, key separation, rotation, or secret handling.

### Network and SSRF

- SSRF through model endpoints, custom URLs, webhooks, tools, MCP servers, redirects, DNS rebinding, or alternate IP representations;
- bypass of outbound host/IP restrictions;
- unauthorized network egress from Agent/Skill/package execution paths.

### Dependency and Supply Chain

- malicious or compromised npm/Python dependencies;
- package lifecycle scripts or install-time execution that bypass expected controls;
- dependency confusion, lockfile bypass, unpinned executable dependencies, or unexpected package resolution;
- third-party Pi packages, Skills, plugins, or MCP servers registering undeclared tools or obtaining undeclared filesystem/network/shell/environment access;
- tool-name collisions or registry manipulation that changes which implementation is invoked.

### File, Import, and Parser Safety

- path traversal, unsafe archive/file handling, arbitrary file read/write, or overwrite behavior;
- parser vulnerabilities triggered by imported user content;
- persistence of attacker-controlled content into trusted prompts, structured memory, evidence, or canonical data without required validation.

### Data Integrity and Evidence Boundaries

- untrusted model or external evidence being promoted into authoritative/canonical data without required checks;
- manipulation of evidence lineage, provenance, or spoiler/cutoff enforcement;
- race conditions or consistency bugs that cross authorization boundaries or corrupt protected state.

## Out of Scope

The following are generally not security vulnerabilities unless they can be chained into a concrete security impact:

- normal model hallucinations or low-quality generations;
- prompt wording that only changes response style/content without crossing a permission or data boundary;
- denial-of-service requiring unrealistic local resource access with no shared-service impact;
- vulnerabilities exclusively in obsolete local forks or unsupported historical commits;
- reports generated only by automated scanners without a plausible affected path or impact.

## Disclosure and Fix Process

The maintainer will attempt to reproduce valid reports, assess severity, prepare the smallest safe fix, add regression coverage when practical, and re-check the affected trust boundary before public disclosure.

Please coordinate disclosure until a fix or mitigation is available. Credit can be provided in release notes or a security advisory if the reporter wants attribution.

## Security Is an Ongoing Constraint

NovelMind is actively evolving. New Agent tools, Skills, model providers, packages, MCP integrations, write capabilities, and deployment modes can change the threat model. Security-sensitive extensions should therefore be treated as new trust-boundary changes rather than ordinary feature additions.
