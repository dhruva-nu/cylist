"""Turning a stored skill into the folder Claude Code loads it from."""

from __future__ import annotations

import stat
import zipfile
from io import BytesIO

import pytest

from app.core.errors import UnprocessableRequestError
from app.services import skill_folders
from app.services.skill_folders import unpack


def zipped(entries: dict[str, bytes], *, modes: dict[str, int] | None = None) -> bytes:
    """A zip holding these entries, with a unix mode on any that ``modes`` names."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = ((modes or {}).get(name, 0o100644)) << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def text_of(folder: skill_folders.SkillFolder, path: str) -> str:
    return next(one for one in folder.files if one.path == path).data.decode()


class TestADocument:
    def test_becomes_a_skill_md_named_after_the_file(self) -> None:
        folder = unpack("Board Tidy.md", "Move stale cards back.", b"# Tidy\n\nDo it.\n")

        assert folder.name == "board-tidy"
        assert [one.path for one in folder.files] == ["SKILL.md"]
        assert text_of(folder, "SKILL.md") == (
            '---\nname: "board-tidy"\ndescription: "Move stale cards back."\n---\n\n'
            "# Tidy\n\nDo it.\n"
        )

    def test_keeps_the_frontmatter_the_author_wrote(self) -> None:
        text = "---\nname: triage\ndescription: Sort the inbox.\nallowed-tools: Bash\n---\n\nBody\n"

        folder = unpack("whatever.md", "A different description.", text.encode())

        assert folder.name == "triage", "the skill's own name wins over its file's"
        assert text_of(folder, "SKILL.md") == text

    def test_fills_in_only_what_is_missing(self) -> None:
        text = "---\nname: triage\n---\nBody\n"

        folder = unpack("triage.md", "Sort the inbox: oldest first.", text.encode())

        assert text_of(folder, "SKILL.md") == (
            '---\ndescription: "Sort the inbox: oldest first."\nname: triage\n---\nBody\n'
        )

    def test_replaces_a_name_claude_code_would_not_accept(self) -> None:
        text = "---\nname: Day Report\ndescription: Write it.\n---\nBody\n"

        folder = unpack("day.md", None, text.encode())

        assert folder.name == "day-report"
        assert text_of(folder, "SKILL.md") == (
            '---\nname: "day-report"\ndescription: Write it.\n---\nBody\n'
        )

    def test_adds_no_description_it_does_not_have(self) -> None:
        folder = unpack("notes.md", None, b"Body\n")

        assert text_of(folder, "SKILL.md") == '---\nname: "notes"\n---\n\nBody\n'

    def test_refuses_something_that_is_not_text(self) -> None:
        with pytest.raises(UnprocessableRequestError, match="neither text nor a zip"):
            unpack("logo.png", None, b"\x89PNG\r\n\x1a\n\xff\xfe")


class TestAZip:
    def test_looks_through_the_folder_it_was_zipped_from(self) -> None:
        content = zipped(
            {
                "cylist/SKILL.md": b"---\nname: cylist\ndescription: Set up.\n---\nRun setup.sh\n",
                "cylist/setup.sh": b"#!/bin/sh\necho hi\n",
                "__MACOSX/cylist/._SKILL.md": b"junk",
            },
            modes={"cylist/setup.sh": 0o100755},
        )

        folder = unpack("cylist.zip", None, content)

        assert folder.name == "cylist"
        assert [(one.path, one.executable) for one in folder.files] == [
            ("SKILL.md", False),
            ("setup.sh", True),
        ]

    def test_takes_a_skill_zipped_from_inside_its_folder(self) -> None:
        content = zipped({"SKILL.md": b"Do it.\n", "reference/api.md": b"# API\n"})

        folder = unpack("Deploy Guide.zip", "Ship to staging.", content)

        assert folder.name == "deploy-guide"
        assert [one.path for one in folder.files] == ["SKILL.md", "reference/api.md"]
        assert 'description: "Ship to staging."' in text_of(folder, "SKILL.md")

    def test_names_it_after_its_folder_when_the_skill_does_not_say(self) -> None:
        content = zipped({"release-notes/SKILL.md": b"Write them.\n"})

        assert unpack("bundle.zip", None, content).name == "release-notes"

    def test_keeps_a_file_that_is_not_text_byte_for_byte(self) -> None:
        image = b"\x89PNG\r\n\x1a\n\x00\xff"
        content = zipped({"SKILL.md": b"See logo.png\n", "logo.png": image})

        folder = unpack("brand.zip", None, content)

        assert next(one for one in folder.files if one.path == "logo.png").data == image

    @pytest.mark.parametrize(
        "entry",
        ["../evil.sh", "skill/../../evil.sh", "/etc/cron.d/evil", "C:/Windows/evil.bat"],
    )
    def test_refuses_an_entry_that_leaves_the_folder(self, entry: str) -> None:
        content = zipped({"SKILL.md": b"Do it.\n", entry: b"rm -rf ~\n"})

        with pytest.raises(UnprocessableRequestError, match="points outside"):
            unpack("evil.zip", None, content)

    def test_refuses_a_symlink(self) -> None:
        content = zipped(
            {"SKILL.md": b"Do it.\n", "key": b"/home/someone/.ssh/id_ed25519"},
            modes={"key": stat.S_IFLNK | 0o777},
        )

        with pytest.raises(UnprocessableRequestError, match="symlink"):
            unpack("links.zip", None, content)

    def test_refuses_a_zip_without_a_skill_md(self) -> None:
        content = zipped({"one/README.md": b"a", "two/README.md": b"b"})

        with pytest.raises(UnprocessableRequestError, match=r"no SKILL\.md at its root"):
            unpack("loose.zip", None, content)

    def test_refuses_one_that_unpacks_too_large(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(skill_folders, "MAX_UNPACKED_BYTES", 1024)
        content = zipped({"SKILL.md": b"Do it.\n", "blob.txt": b"0" * 4096})

        with pytest.raises(UnprocessableRequestError, match="unpacks to more than"):
            unpack("bomb.zip", None, content)

    def test_refuses_one_with_too_many_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(skill_folders, "MAX_FILES", 2)
        content = zipped({"SKILL.md": b"x", "a": b"a", "b": b"b"})

        with pytest.raises(UnprocessableRequestError, match="more than 2 files"):
            unpack("many.zip", None, content)

    def test_refuses_a_zip_that_is_not_one(self) -> None:
        with pytest.raises(UnprocessableRequestError, match="not a readable zip"):
            unpack("broken.zip", None, b"PK\x03\x04 but then nothing")


class TestSlug:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("board-tidy", "board-tidy"),
            ("Day Report!", "day-report"),
            ("__", ""),
            ("a" * 80, "a" * 64),
        ],
    )
    def test_is_what_claude_code_accepts(self, given: str, expected: str) -> None:
        assert skill_folders.slug(given) == expected
