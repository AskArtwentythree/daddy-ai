from loom_common.files import as_text, describe_size, from_payload, to_payload


def test_text_and_binary_round_trip():
    for data in ("# Plan\n\nПривет 👋".encode(), b"%PDF-1.7\n\x00\xff\xfe binary", b"\x89PNG\r\n\x1a\n\x00\x01"):
        payload = to_payload(data)
        assert from_payload(payload) == data
        assert ("content" in payload) == (as_text(data) is not None)
    assert to_payload(b"hello") == {"content": "hello"}
    assert as_text(b"\xff\xd8\xff\xe0 jpeg") is None


def test_sizes_read_naturally():
    assert [describe_size(n) for n in (12, 4_200, 25_000_000)] == ["12 B", "4 KB", "25.0 MB"]


def test_versions():
    from loom_common.version import cli_version_of, is_older

    assert is_older("0.1", "0.2.0") and is_older("0.2.0", "0.10.0") and not is_older("0.2.0", "0.2.0")
    assert cli_version_of("loom-cli/0.1") == "0.1"
    assert cli_version_of("Mozilla/5.0 (Macintosh)") is None
