"""Version identifiers recorded on every artifact the engine produces."""

ENGINE_VERSION = "0.2.0"
SCHEMA_VERSION = "1"
PARSER_VERSION = "0.2.0"  # SEC submissions + companyfacts adapters


def version_stamp() -> dict[str, str]:
    return {
        "engine_version": ENGINE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
    }
