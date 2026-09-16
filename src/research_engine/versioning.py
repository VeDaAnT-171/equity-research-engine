"""Version identifiers recorded on every artifact the engine produces."""

ENGINE_VERSION = "0.1.0"
SCHEMA_VERSION = "1"
PARSER_VERSION = "0.0.0"  # no parsers exist yet


def version_stamp() -> dict[str, str]:
    return {
        "engine_version": ENGINE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
    }
