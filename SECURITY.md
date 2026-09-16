# Security

## Threat model (current scope)
The engine ingests documents and URLs supplied by users. Those inputs are untrusted.

## Controls implemented (Phase 1)
- **URL validation at config load**: only `https`; no embedded credentials; `localhost`, `.local`/`.internal` hosts and non-public IP literals are rejected (basic SSRF guard).
- **No code execution from data**: framework formulas are parsed with a whitelisted AST (`+ - * /`, parentheses, identifiers, numeric literals). `eval` is never used. Attribute access, calls, subscripts, and exponentiation are rejected.
- **Immutable raw store**: raw documents are content-addressed by SHA-256, written once, and set read-only. Changed content at the same source is refused, not overwritten.
- **Strict schemas**: unknown config keys are rejected (typos cannot silently disable a setting).
- **Secrets** come from environment variables only; see `.env.example`. `.env` is git-ignored.

## Known gaps (to be closed in Phase 4, ingestion)
- DNS-rebinding: hostnames are not resolved at validation time; the downloader must re-check the resolved IP.
- Download size limits, content-type sniffing, and PDF sandboxing are not yet implemented.

## Reporting
Open a private security advisory on the repository rather than a public issue.
