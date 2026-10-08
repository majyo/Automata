import codecs
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from automata_api.infrastructure.workspace import browser as local_browser


def test_directory_navigation_returns_sorted_relative_paths(client, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "empty").mkdir()
    (tmp_path / "z.txt").write_text("last", encoding="utf-8")
    (tmp_path / ".hidden").write_text("hidden", encoding="utf-8")
    folder = tmp_path / "资料 #"
    folder.mkdir()
    (folder / "说明 & #.txt").write_text("中文说明", encoding="utf-8")

    result = client.get("/workspace/directory", params={"workspace": str(tmp_path)})
    assert result.status_code == 200
    entries = result.json()["entries"]
    # The TestClient data directory also contains the application's database;
    # verify order independently of those application-owned files.
    order = [
        (entry["kind"] != "directory", entry["name"].casefold(), entry["name"])
        for entry in entries
    ]
    assert order == sorted(order)
    assert any(entry["path"] == ".hidden" for entry in entries)
    assert all(entry["accessible"] for entry in entries)
    nested = client.get(
        "/workspace/directory", params={"workspace": str(tmp_path), "path": "资料 #"}
    )
    assert nested.json()["entries"][0]["path"] == "资料 #/说明 & #.txt"
    text = client.get(
        "/workspace/text",
        params={"workspace": str(tmp_path), "path": "资料 #/说明 & #.txt"},
    )
    assert text.json()["content"] == "中文说明"
    empty = client.get(
        "/workspace/directory", params={"workspace": str(tmp_path), "path": "empty"}
    )
    assert empty.json()["entries"] == []


@pytest.mark.parametrize(
    "encoding,label",
    [
        ("utf-8", "UTF-8"),
        ("utf-8-sig", "UTF-8 BOM"),
        ("utf-16", "UTF-16 LE"),
        ("utf-32", "UTF-32 LE"),
        ("gb18030", "GB18030"),
    ],
)
def test_text_preview_detects_encodings_and_normalizes_newlines(
    client, tmp_path, encoding, label
):
    content = "中文阅读\r\n第二行\r第三行"
    (tmp_path / "text.txt").write_bytes(content.encode(encoding))
    response = client.get(
        "/workspace/text", params={"workspace": str(tmp_path), "path": "text.txt"}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["content"] == "中文阅读\n第二行\n第三行"
    assert result["encoding"] == label
    assert result["size"] == len(content.encode(encoding))
    assert result["truncated"] is False


def test_text_preview_supports_big_endian_bom(client, tmp_path):
    (tmp_path / "text.txt").write_bytes(
        codecs.BOM_UTF16_BE + "中文".encode("utf-16-be")
    )
    response = client.get(
        "/workspace/text", params={"workspace": str(tmp_path), "path": "text.txt"}
    )
    assert response.json()["content"] == "中文"
    assert response.json()["encoding"] == "UTF-16 BE"


def test_empty_and_binary_files_have_distinct_results(client, tmp_path):
    (tmp_path / "empty.txt").write_bytes(b"")
    (tmp_path / "image.bin").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff")
    empty = client.get(
        "/workspace/text", params={"workspace": str(tmp_path), "path": "empty.txt"}
    )
    assert empty.status_code == 200
    assert empty.json()["content"] == ""
    assert empty.json()["size"] == 0
    binary = client.get(
        "/workspace/text", params={"workspace": str(tmp_path), "path": "image.bin"}
    )
    assert binary.status_code == 415
    assert binary.json()["detail"]["code"] == "unsupported_text"


def test_preview_byte_limit_does_not_corrupt_a_partial_character(client, tmp_path):
    prefix = "a" * (local_browser.MAX_PREVIEW_BYTES - 1)
    (tmp_path / "large.txt").write_bytes((prefix + "中文").encode("utf-8"))
    response = client.get(
        "/workspace/text", params={"workspace": str(tmp_path), "path": "large.txt"}
    )
    assert response.status_code == 200
    assert response.json()["content"] == prefix
    assert response.json()["truncated"] is True
    assert response.json()["encoding"] == "UTF-8"


def test_preview_bounds_line_count(client, tmp_path):
    (tmp_path / "lines.txt").write_text("line\n" * 10000, encoding="utf-8")
    response = client.get(
        "/workspace/text", params={"workspace": str(tmp_path), "path": "lines.txt"}
    )
    assert response.json()["truncated"] is True
    assert (
        len(response.json()["content"].split("\n")) == local_browser.MAX_PREVIEW_LINES
    )


def test_directory_entry_limit_is_explicit(client, tmp_path, monkeypatch):
    folder = tmp_path / "many"
    folder.mkdir()
    for name in ("c.txt", "b.txt", "a.txt"):
        (folder / name).touch()
    monkeypatch.setattr(local_browser, "MAX_DIRECTORY_ENTRIES", 2)
    response = client.get(
        "/workspace/directory", params={"workspace": str(tmp_path), "path": "many"}
    )
    assert [entry["name"] for entry in response.json()["entries"]] == ["a.txt", "b.txt"]
    assert response.json()["truncated"] is True


def test_one_unreadable_entry_does_not_hide_other_files(client, tmp_path, monkeypatch):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "readable.txt").write_text("text", encoding="utf-8")
    original_scandir = local_browser.os.scandir

    class UnreadableEntry:
        name = "unreadable"
        path = str(folder / name)

        def is_dir(self):
            raise PermissionError("denied")

    @contextmanager
    def scandir_with_unreadable(path):
        with original_scandir(path) as entries:
            yield iter([*entries, UnreadableEntry()]) if path == folder else entries

    monkeypatch.setattr(local_browser.os, "scandir", scandir_with_unreadable)
    response = client.get("/workspace/directory", params={"workspace": str(folder)})
    assert response.status_code == 200
    entries = {entry["name"]: entry for entry in response.json()["entries"]}
    assert entries["readable.txt"]["accessible"] is True
    assert entries["unreadable"]["accessible"] is False


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "..\\outside.txt",
        "/outside.txt",
        "C:/outside.txt",
        "C:outside.txt",
        "text.txt:stream",
        "\x00",
    ],
)
@pytest.mark.parametrize("endpoint", ["directory", "text"])
def test_workspace_paths_cannot_escape_or_access_streams(
    client, tmp_path, path, endpoint
):
    response = client.get(
        f"/workspace/{endpoint}", params={"workspace": str(tmp_path), "path": path}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "outside_workspace"


def test_external_symlinks_are_disabled_and_cannot_be_opened(client, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("outside content", encoding="utf-8")
    try:
        (root / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires OS permission")
    result = client.get("/workspace/directory", params={"workspace": str(root)})
    assert result.json()["entries"][0]["accessible"] is False
    response = client.get(
        "/workspace/text", params={"workspace": str(root), "path": "link.txt"}
    )
    assert response.status_code == 403
    assert "outside content" not in response.text


@pytest.mark.parametrize("endpoint", ["directory", "text"])
def test_workspace_endpoints_require_authentication(client, tmp_path, endpoint):
    with TestClient(client.app) as anonymous:
        response = anonymous.get(
            f"/workspace/{endpoint}", params={"workspace": str(tmp_path), "path": "."}
        )
    assert response.status_code == 401


def test_missing_paths_and_wrong_types_are_reported(client, tmp_path):
    (tmp_path / "text.txt").write_text("text", encoding="utf-8")
    for endpoint in ("directory", "text"):
        missing = client.get(
            f"/workspace/{endpoint}",
            params={"workspace": str(tmp_path), "path": "missing"},
        )
        assert missing.status_code == 404
        invalid_root = client.get(
            f"/workspace/{endpoint}",
            params={"workspace": str(tmp_path / "missing"), "path": "."},
        )
        assert invalid_root.status_code == 422
    not_directory = client.get(
        "/workspace/directory", params={"workspace": str(tmp_path), "path": "text.txt"}
    )
    assert not_directory.status_code == 422
    not_file = client.get(
        "/workspace/text", params={"workspace": str(tmp_path), "path": "."}
    )
    assert not_file.status_code == 422
