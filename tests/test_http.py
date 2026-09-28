import gzip
import io
import os
import socket

import pytest

from research_engine.errors import ConfigError, FetchError
from research_engine.ingestion import HttpFetcher, load_env_file
from research_engine.ingestion.http import (
    check_resolves_public,
    gunzip_limited,
    read_limited,
    require_contact_user_agent,
)


def resolver_for(*addresses):
    def resolve(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]
    return resolve


def test_dns_rebinding_guard():
    check_resolves_public("example.com", resolver_for("93.184.215.14"))
    with pytest.raises(FetchError, match="non-public"):
        check_resolves_public("evil.example", resolver_for("93.184.215.14", "169.254.169.254"))


def test_fetch_rejects_unsafe_urls_before_network():
    fetcher = HttpFetcher("Test test@example.com", resolver=resolver_for("10.0.0.1"))
    with pytest.raises(FetchError, match="only https"):
        fetcher.fetch("http://example.com/x")
    with pytest.raises(FetchError, match="non-public"):
        fetcher.fetch("https://example.com/x")


def test_size_limits():
    assert read_limited(io.BytesIO(b"x" * 10), 10) == b"x" * 10
    with pytest.raises(FetchError, match="exceeds"):
        read_limited(io.BytesIO(b"x" * 11), 10)
    bomb = gzip.compress(b"\0" * 5_000_000)
    assert len(bomb) < 10_000
    with pytest.raises(FetchError, match="decompressed"):
        gunzip_limited(bomb, 1_000_000)
    assert gunzip_limited(gzip.compress(b"hello"), 100) == b"hello"


def test_sec_user_agent_requirement():
    with pytest.raises(ConfigError, match="SEC_USER_AGENT"):
        require_contact_user_agent(None)
    with pytest.raises(ConfigError, match="contact email"):
        require_contact_user_agent("python-requests")
    assert require_contact_user_agent(" Jane jane@example.com ") == "Jane jane@example.com"


def test_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nexport RE_TEST_A="quoted value"\nRE_TEST_B=plain\nRE_TEST_C=keep\n')
    monkeypatch.setenv("RE_TEST_C", "existing")
    for k in ("RE_TEST_A", "RE_TEST_B"):
        monkeypatch.delenv(k, raising=False)
    assert load_env_file(env) == ["RE_TEST_A", "RE_TEST_B"]
    assert os.environ["RE_TEST_A"] == "quoted value" and os.environ["RE_TEST_C"] == "existing"
    assert load_env_file(tmp_path / "missing.env") == []
    env.write_text("NOT VALID\n")
    with pytest.raises(ValueError):
        load_env_file(env)
