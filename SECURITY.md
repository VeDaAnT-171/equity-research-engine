# Security

## Threat model (current scope)
The engine ingests documents and URLs supplied by users. Those inputs are untrusted.

## Controls implemented (Phase 1)
- **URL validation at config load**: only `https`; no embedded credentials; `localhost`, `.local`/`.internal` hosts and non-public IP literals are rejected (basic SSRF guard).
- **No code execution from data**: framework formulas are parsed with a whitelisted AST (`+ - * /`, parentheses, identifiers, numeric literals). `eval` is never used. Attribute access, calls, subscripts, and exponentiation are rejected.
- **Immutable raw store**: raw documents are content-addressed by SHA-256, written once, and set read-only. Changed content at the same source is refused, not overwritten.
- **Strict schemas**: unknown config keys are rejected (typos cannot silently disable a setting).
- **Secrets** come from environment variables only; see `.env.example`. `.env` is git-ignored.

## Controls implemented (Phase 2)
- **DNS rebinding guard**: every resolved address of a host must be public before connecting; redirect targets are re-validated.
- **Size limits**: raw responses and gzip-decompressed bodies are capped (default 200 MB), defeating decompression bombs.
- **Throttling**: at most one request per 150 ms per host (SEC fair-access limit is 10/s).
- **Identification**: SEC requests refuse to run without `SEC_USER_AGENT` containing a contact email.
- **Untrusted JSON**: parsed with Decimal; malformed observations are dropped and counted, never coerced.

## Known gaps
- Content-type sniffing and PDF sandboxing arrive with document parsing.
- The fetcher resolves then connects; a resolver that answers differently between the two calls is not fully excluded.

## Reporting
Open a private security advisory on the repository rather than a public issue.
