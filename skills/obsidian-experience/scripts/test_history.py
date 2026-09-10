"""Isolated CLI checks; never reads real histories or writes a personal vault."""

import base64
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import quote

from images import local_image_path
from reading import context_prefix, message_card


SCRIPT = Path(__file__).with_name("history.py")


def unquote_cards(text):
    return re.sub(r"(?m)^> ?", "", text)


class HistoryExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="obsidian-history-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.home = self.base / "home"
        self.project = self.base / "project with spaces"
        self.vault = self.base / "模拟笔记库"
        self.project.mkdir()
        self.vault.mkdir()
        self.text = ["UNSELECTED-CANARY-928", "  中文🙂答案\r\n\n```ts\nconst v = '<script>example</script>';\n```\n  ",
                     "重复摘录 / 重复摘录", "最后的回答\u2028保留分隔符"]
        self.paths = {"codex": self.home / ".codex/sessions/2026/09/09/rollout-fixture.jsonl",
                      "claude": self.home / ".claude/projects/fixture/session-fixture.jsonl"}
        for client, path in self.paths.items():
            path.parent.mkdir(parents=True)
            self.write_rows(path, self.fixture_rows(client))

    def fixture_rows(self, client, project=None, texts=None):
        project = str(project or self.project)
        timestamp = "2026-09-09T10:00:00-07:00"
        rows = [{"type": "session_meta", "timestamp": timestamp, "payload": {"id": "codex-fixture", "cwd": project}}] if client == "codex" else []
        for number, text in enumerate(self.text if texts is None else texts):
            role = "assistant" if number % 2 else "user"
            message = {"role": role, "content": [{"type": "output_text" if client == "codex" else "text", "text": text}]}
            if client == "codex":
                rows.append({"type": "response_item", "timestamp": timestamp, "payload": {"type": "message", **message}})
            else:
                rows.append({"type": role, "timestamp": timestamp, "cwd": project, "sessionId": "claude-fixture", "message": message})
        return rows

    @staticmethod
    def write_rows(path, rows):
        path.write_bytes(("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode("utf-8"))

    def run_cli(self, command, client=None, *extra, ok=True):
        args = [sys.executable, "-B", str(SCRIPT), command, "--home", str(self.home), "--project", str(self.project)]
        if client:
            args += ["--client", client]
            if command != "list":
                args += ["--source", str(self.paths[client])]
        result = subprocess.run(args + list(extra), capture_output=True, text=True, encoding="utf-8")
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        return result.stderr

    def export(self, client, *selection, version=None, ok=True, folder="Conversations"):
        version = version or self.run_cli("read", client)["version"]
        return self.run_cli("export", client, "--expect-version", version, "--vault", str(self.vault),
                            "--folder", folder, *selection, ok=ok)

    def test_both_clients_preserve_text_and_paginate_without_storage(self):
        listed = self.run_cli("list")
        self.assertEqual({item["client"] for item in listed["sessions"]}, {"codex", "claude"})
        for client in self.paths:
            first = self.run_cli("read", client, "--limit", "2")
            second = self.run_cli("read", client, "--offset", "2", "--limit", "2")
            self.assertTrue(first["has_more"])
            self.assertFalse(second["has_more"])
            self.assertEqual(first["version"], second["version"])
            messages = first["messages"] + second["messages"]
            self.assertEqual([message["text"] for message in messages], self.text)
            self.assertEqual([message["number"] for message in messages], [1, 2, 3, 4])
        self.assertEqual(list(self.vault.iterdir()), [])
        self.assertFalse((self.project / ".codetrap").exists())

    def test_full_and_partial_exports_preserve_exact_bodies_and_order(self):
        for client in self.paths:
            full = self.export(client, "--all")
            body = unquote_cards(Path(full["path"]).read_bytes().decode("utf-8"))
            for original in self.text:
                self.assertIn(original, body)
            selected = self.export(client, "--messages", "4,2")
            saved = Path(selected["path"]).read_bytes().decode("utf-8")
            readable = unquote_cards(saved)
            self.assertIn(self.text[1], readable)
            self.assertIn(self.text[3], readable)
            self.assertNotIn(self.text[0], saved)
            self.assertNotIn(self.text[2], saved)
            self.assertLess(readable.index(self.text[1]), readable.index(self.text[3]))
            self.paths[client].unlink()
            self.assertEqual(Path(selected["path"]).read_bytes().decode("utf-8"), saved)
        self.assertEqual(len(list(self.vault.rglob("*.*"))), 4)

    def test_unicode_excerpt_saves_only_the_chosen_occurrence(self):
        for client in self.paths:
            saved = self.export(client, "--excerpt-message", "2", "--start", "2", "--end", "7")
            body = Path(saved["path"]).read_text(encoding="utf-8")
            self.assertIn("中文🙂答案", body)
            self.assertNotIn("const v", body)
            self.assertNotIn("UNSELECTED-CANARY", body)
            self.assertEqual(saved["selection"]["offset_unit"], "unicode_code_points")
            repeated = self.export(client, "--excerpt-message", "3", "--start", "7", "--end", "11")
            repeated_body = Path(repeated["path"]).read_text(encoding="utf-8")
            self.assertEqual(repeated_body.count("重复摘录"), 1)

    def test_retry_is_idempotent_and_manual_edits_are_preserved(self):
        first = self.export("codex", "--messages", "2,4")
        path = Path(first["path"])
        original = path.read_bytes()
        second = self.export("codex", "--messages", "4,2,2")
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["path"], second["path"])
        self.assertEqual(path.read_bytes(), original)
        path.write_bytes(original + "\n我的补充".encode())
        self.assertIn("left untouched", self.export("codex", "--messages", "2,4", ok=False))
        self.assertEqual(path.read_bytes(), original + "\n我的补充".encode())
        self.assertEqual(len(list(path.parent.iterdir())), 1)

    def test_changed_or_incomplete_sources_fail_before_writing(self):
        preview = self.run_cli("read", "codex")
        with self.paths["codex"].open("ab") as handle:
            handle.write(b'{"type":"event_msg","payload":{"type":"task_complete"}}\n')
        self.assertIn("changed since preview", self.export("codex", "--messages", "2", version=preview["version"], ok=False))
        with self.paths["codex"].open("ab") as handle:
            handle.write(b'{"unfinished":')
        self.assertIn("Invalid/incomplete", self.run_cli("read", "codex", ok=False))
        self.assertEqual(list(self.vault.iterdir()), [])

    def test_readable_titles_are_safe_and_same_snapshot_cannot_fork_on_title_change(self):
        title = 'SQLite / 并发写入: "参数" [说明] ' + "长标题" * 60
        first = self.export("codex", "--messages", "2", "--title", title)
        path = Path(first["path"])
        self.assertIn("SQLite", path.name)
        self.assertIn("并发写入", path.name)
        self.assertNotRegex(path.name, r'[<>:"/\\|?*\[\]#^]')
        self.assertLess(len(path.name.encode("utf-8")), 240)
        self.assertIn("# " + title, path.read_text(encoding="utf-8"))
        self.assertNotIn("UNSELECTED-CANARY", path.read_text(encoding="utf-8"))
        original = path.read_bytes()
        self.assertTrue(self.export("codex", "--messages", "2", "--title", title)["duplicate"])
        self.assertIn("left untouched", self.export("codex", "--messages", "2", "--title", "另一个标题", ok=False))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.iterdir()), [path])
        other = self.export("codex", "--messages", "4", "--title", title)
        self.assertNotEqual(other["path"], first["path"])

    def test_readable_export_reuses_legacy_path_and_detects_multiple_copies(self):
        first = self.export("codex", "--messages", "2", "--title", "中文主题")
        path = Path(first["path"])
        suffix = path.stem.rsplit(" -- codex-", 1)[1]
        legacy = path.with_name(f"codex-codex-fixture-{suffix}.md")
        path.rename(legacy)
        retried = self.export("codex", "--messages", "2", "--title", "中文主题")
        self.assertTrue(retried["duplicate"])
        self.assertEqual(retried["path"], str(legacy))
        path.write_bytes(legacy.read_bytes())
        self.assertIn("Multiple exports", self.export("codex", "--messages", "2", "--title", "中文主题", ok=False))
        self.assertEqual(len(list(path.parent.iterdir())), 2)

    def test_invalid_selections_never_write(self):
        for selection in [("--messages", "0"), ("--messages", "2,99"), ("--messages", "3-2"),
                          ("--excerpt-message", "2", "--start", "-1", "--end", "4"),
                          ("--excerpt-message", "2", "--start", "3"),
                          ("--all", "--start", "0")]:
            self.export("claude", *selection, ok=False)
        self.assertEqual(list(self.vault.iterdir()), [])

    def test_source_and_vault_boundaries(self):
        sibling = self.paths["codex"].with_name("sibling.jsonl")
        self.write_rows(sibling, self.fixture_rows("codex", Path(str(self.project) + "-other")))
        nested = self.paths["claude"].with_name("nested.jsonl")
        self.write_rows(nested, self.fixture_rows("claude", self.project / "subdir"))
        self.assertEqual(self.run_cli("list")["total"], 3)
        self.run_cli("read", "codex", "--source", str(sibling), ok=False)
        self.run_cli("read", "codex", "--source", str(self.paths["claude"]), ok=False)
        outside = self.base / "outside"
        outside.mkdir()
        for folder in ("../outside", ".obsidian", str(outside)):
            self.export("codex", "--all", folder=folder, ok=False)
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlinks_cannot_bypass_source_or_vault_boundaries(self):
        outside = self.base / "outside"
        outside.mkdir()
        link = self.paths["codex"].with_name("link.jsonl")
        try:
            link.symlink_to(self.paths["codex"])
            (self.vault / "escape").symlink_to(outside, target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows symlink creation needs Developer Mode or the symlink privilege")
            raise
        self.assertEqual(self.run_cli("list")["total"], 2)
        self.run_cli("read", "codex", "--source", str(link), ok=False)
        self.export("codex", "--all", folder="escape", ok=False)
        self.assertEqual(list(outside.iterdir()), [])

    def test_excluded_content_and_multiple_text_blocks(self):
        for client in self.paths:
            rows = self.fixture_rows(client, texts=["Visible"])
            message = rows[-1]["payload"] if client == "codex" else rows[-1]["message"]
            message["content"] += [{"type": "text", "text": "  extra  "}, {"type": "image", "source": "SECRET-IMAGE"},
                                   {"type": "tool_use", "input": "SECRET-TOOL"}, {"type": "thinking", "thinking": "SECRET-THINKING"}]
            if client == "codex":
                rows += [{"type": "event_msg", "payload": {"type": "user_message", "message": "Visible"}},
                         {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": "SECRET-SYSTEM"}}]
            self.write_rows(self.paths[client], rows)
            read = self.run_cli("read", client)
            self.assertEqual(read["messages"][0]["text"], "Visible\n  extra  ")
            self.assertEqual(read["total_messages"], 1)
            self.assertEqual(read["messages"][0]["images"], 1)
            saved = self.export(client, "--all")
            self.assertEqual(saved["unavailable_images"], 1)
            body = Path(saved["path"]).read_text(encoding="utf-8")
            self.assertNotIn("SECRET-", body)

    def test_long_history_is_not_cut_off_by_preview_pagination(self):
        texts = [f"{index}: " + "长正文" * 1000 for index in range(75)]
        self.write_rows(self.paths["codex"], self.fixture_rows("codex", texts=texts))
        preview = self.run_cli("read", "codex", "--limit", "1")
        self.assertTrue(preview["has_more"])
        result = self.export("codex", "--all", version=preview["version"])
        self.assertEqual(result["saved_messages"], 75)
        self.assertIn(texts[-1], Path(result["path"]).read_text(encoding="utf-8"))

    def test_string_messages_and_unknown_blocks_do_not_silently_crash(self):
        for client in self.paths:
            rows = self.fixture_rows(client, texts=["ignored", "ignored"])
            field = "payload" if client == "codex" else "message"
            rows[-2][field]["content"] = "  原始字符串\n"
            rows[-1][field]["content"] = [{"type": ["unexpected"], "text": "UNSUPPORTED-TEXT"},
                                         {"type": "text", "text": "Known text"}]
            self.write_rows(self.paths[client], rows)
            data = self.run_cli("read", client)
            self.assertEqual([message["text"] for message in data["messages"]], ["  原始字符串\n", "Known text"])

    def test_concurrent_retries_publish_one_complete_note(self):
        version = self.run_cli("read", "codex")["version"]
        command = [sys.executable, "-B", str(SCRIPT), "export", "--home", str(self.home),
                   "--project", str(self.project), "--client", "codex", "--source", str(self.paths["codex"]),
                   "--vault", str(self.vault), "--expect-version", version, "--all", "--title", "并发保存"]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8") for _ in range(3)]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout))
        self.assertEqual(sum(not result["duplicate"] for result in results), 1)
        self.assertEqual(len({result["path"] for result in results}), 1)
        self.assertEqual(len(list((self.vault / "Conversations").iterdir())), 1)
        body = unquote_cards(Path(results[0]["path"]).read_bytes().decode("utf-8"))
        for original in self.text:
            self.assertIn(original, body)

    def test_unknown_and_empty_text_sources_are_explicit_failures(self):
        self.write_rows(self.paths["codex"], [{"type": "unknown"}])
        self.assertIn("Recorded project", self.run_cli("read", "codex", ok=False))
        self.write_rows(self.paths["codex"], self.fixture_rows("codex", texts=[]))
        self.assertIn("No readable", self.export("codex", "--all", ok=False))
        self.assertEqual(list(self.vault.iterdir()), [])

    def test_concurrent_different_titles_do_not_create_duplicate_snapshots(self):
        version = self.run_cli("read", "codex")["version"]
        command = [sys.executable, "-B", str(SCRIPT), "export", "--home", str(self.home),
                   "--project", str(self.project), "--client", "codex", "--source", str(self.paths["codex"]),
                   "--vault", str(self.vault), "--expect-version", version, "--all"]
        processes = [subprocess.Popen(command + ["--title", title], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8")
                     for title in ("第一个标题", "第二个标题")]
        completed = [(process, process.communicate(timeout=15)) for process in processes]
        self.assertEqual(sorted(process.returncode for process, _ in completed), [0, 1])
        for process, (stdout, stderr) in completed:
            if process.returncode:
                self.assertIn("left untouched", stderr)
            else:
                saved = json.loads(stdout)
                self.assertFalse(saved["duplicate"])
        self.assertEqual(len(list((self.vault / "Conversations").iterdir())), 1)

    def test_registered_vault_lookup_reads_only_registry_and_reports_choices(self):
        config = self.base / "obsidian.json"
        config.write_text(json.dumps({"vaults": {"one": {"path": str(self.vault), "open": True},
                                                 "gone": {"path": str(self.base / "missing-vault")}}}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "vaults", "--config", str(config)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        vaults = json.loads(result.stdout)["vaults"]
        self.assertTrue(vaults[0]["exists"])
        self.assertFalse(vaults[1]["exists"])
        self.assertEqual(list(self.vault.iterdir()), [])

    def test_inline_images_keep_message_order_without_tool_or_reasoning_text(self):
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=")
        encoded = base64.b64encode(png).decode()
        for client in self.paths:
            rows = self.fixture_rows(client, texts=["before image", "normal answer"])
            field = "payload" if client == "codex" else "message"
            image = {"type": "input_image", "image_url": "data:image/png;base64," + encoded} if client == "codex" else {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encoded}}
            first = rows[-2][field]
            first["content"] += [image, {"type": "text", "text": "after image"}]
            rows[-1][field]["content"] += [{"type": "thinking", "thinking": "REASONING-SECRET"}, {"type": "tool_use", "input": "TOOL-SECRET"}]
            if client == "codex":
                rows += [{"type": "response_item", "payload": {"type": "function_call_output", "output": json.dumps({"content": [{"type": "text", "text": "TOOL-SECRET"}, image]})}},
                         {"type": "response_item", "payload": {"type": "reasoning", "summary": [{"type": "summary_text", "text": "REASONING-SECRET"}]}},
                         {"type": "response_item", "payload": {"type": "message", "role": "assistant", "channel": "analysis", "content": [{"type": "output_text", "text": "ANALYSIS-SECRET"}]}}]
            else:
                rows += [{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": [{"type": "text", "text": "TOOL-SECRET"}, image]}]}}]
            self.write_rows(self.paths[client], rows)
            preview = self.run_cli("read", client)
            self.assertEqual(preview["total_messages"], 3)
            self.assertNotIn("omitted_from_source", preview)
            self.assertNotIn(encoded, json.dumps(preview))
            result = self.export(client, "--all", "--title", "图文保存 — 中文标题")
            note = Path(result["path"])
            body = note.read_text(encoding="utf-8")
            self.assertEqual(result["image_files"], 1)
            self.assertEqual(result["unavailable_images"], 0)
            self.assertNotIn("omitted_from_source", result)
            self.assertNotIn("omitted_from_source:", body)
            self.assertLess(body.index("before image"), body.index("![图片]"))
            self.assertLess(body.index("![图片]"), body.index("after image"))
            self.assertIn("normal answer", body)
            self.assertNotIn(encoded, body)
            assets = list(note.with_suffix(".assets").iterdir())
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0].read_bytes(), png)
            self.assertTrue(self.export(client, "--all", "--title", "图文保存 — 中文标题")["duplicate"])
        files = [path for path in self.vault.rglob('*') if path.is_file()]
        self.assertEqual({path.suffix for path in files}, {".md", ".png"})
        for path in files:
            for secret in (b"TOOL-SECRET", b"REASONING-SECRET", b"ANALYSIS-SECRET"):
                self.assertNotIn(secret, path.read_bytes())

    def test_partial_saves_never_copy_unselected_images_and_excerpts_stay_text(self):
        rows = self.fixture_rows("claude", texts=["UNSELECTED-BODY", "chosen text"])
        for index, row in enumerate(rows):
            row["message"]["content"].append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(f"image-{index}".encode()).decode()}})
        self.write_rows(self.paths["claude"], rows)
        selected = self.export("claude", "--messages", "2")
        note = Path(selected["path"])
        self.assertNotIn("UNSELECTED-BODY", note.read_text(encoding="utf-8"))
        self.assertEqual(next(note.with_suffix(".assets").iterdir()).read_bytes(), b"image-1")
        excerpt = self.export("claude", "--excerpt-message", "2", "--start", "0", "--end", "6")
        self.assertEqual(excerpt["image_files"], 0)
        self.assertFalse(Path(excerpt["path"]).with_suffix(".assets").exists())
        self.assertIn("chosen", Path(excerpt["path"]).read_text(encoding="utf-8"))

    def test_image_only_message_and_edited_image_protection(self):
        rows = self.fixture_rows("codex", texts=[""])
        rows[-1]["payload"]["content"] = [{"type": "input_image", "image_url": "data:image/png;base64,aW1hZ2U="}]
        self.write_rows(self.paths["codex"], rows)
        preview = self.run_cli("read", "codex")
        self.assertEqual(preview["total_messages"], 1)
        self.assertEqual(preview["messages"][0]["images"], 1)
        result = self.export("codex", "--all")
        image = next(Path(result["path"]).with_suffix(".assets").iterdir())
        image.write_bytes(b"my edited image")
        self.assertIn("left untouched", self.export("codex", "--all", ok=False))
        self.assertEqual(image.read_bytes(), b"my edited image")

    def test_markdown_images_localize_but_code_examples_and_remote_urls_are_preserved(self):
        local = self.project / "photo.png"
        local.write_bytes(b"local image")
        example = f"```md\n![example](<{local}>)\n```\n`![inline](<{local}>)`"
        text = f"![real](<{local}>)\n{example}\n![remote](https://example.invalid/photo.png)"
        rows = self.fixture_rows("codex", texts=[text])
        self.write_rows(self.paths["codex"], rows)
        result = self.export("codex", "--all")
        body = Path(result["path"]).read_text(encoding="utf-8")
        self.assertEqual(result["image_files"], 1)
        self.assertEqual(result["remote_images"], 1)
        self.assertIn(example, unquote_cards(body))
        self.assertIn("![real](<codex-", body)
        self.assertIn("![remote](<https://example.invalid/photo.png>)", body)

    def test_windows_drive_paths_and_file_uris_decode_without_losing_characters(self):
        expected = PureWindowsPath(r"C:\Users\Alice\图片 space #100%.png")
        uri = "file:///C:/Users/Alice/%E5%9B%BE%E7%89%87%20space%20%23100%25.png"
        for reference in (str(expected), expected.as_posix(), uri,
                          uri.replace("file:///", "file://localhost/")):
            with self.subTest(reference=reference):
                self.assertEqual(PureWindowsPath(local_image_path(reference, windows=True)), expected)
        # A literal percent escape in a raw filesystem path must stay literal.
        raw = r"D:\Pictures\literal%20name.png"
        self.assertEqual(local_image_path(raw, windows=True), raw)

    def test_default_cards_label_roles_and_keep_images_inside_their_messages(self):
        for client in self.paths:
            rows = self.fixture_rows(client, texts=["question", "answer"])
            field = "payload" if client == "codex" else "message"
            rows[-1][field]["content"].append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aW1hZ2U="}})
            self.write_rows(self.paths[client], rows)
            saved = self.export(client, "--all")
            body = Path(saved["path"]).read_text(encoding="utf-8")
            self.assertIn('cssclasses: ["conversation-reading"]', body)
            self.assertIn("> [!question] 你 · 01 · ", body)
            label = "Codex" if client == "codex" else "Claude"
            self.assertIn(f"> [!success] {label} · 02 · ", body)
            self.assertNotIn("Timestamp:", body)
            self.assertNotIn("## 1. user", body)
            self.assertIn("> ![图片](<", body)
            self.assertLess(body.index("answer"), body.index("> ![图片]"))
            self.assertLess(body.index("> ![图片]"), body.index("^message-02"))
            self.assertTrue(self.export(client, "--all")["duplicate"])

    def test_context_folds_without_hiding_a_request_in_the_same_message(self):
        prefix = '<recommended_plugins>plugins</recommended_plugins>\n# AGENTS.md instructions\n<INSTRUCTIONS>rules</INSTRUCTIONS>\n<environment_context>```code```</environment_context>\n'
        request = "请保存这张图\n\n> 原文引用\n```python\nprint('hello')\n```"
        context, body = context_prefix(prefix + request)
        self.assertEqual(context, prefix)
        self.assertEqual(body, request)
        message = {"number": 1, "role": "user", "timestamp": "2026-09-09T10:00:00-07:00"}
        rendered = message_card(message, prefix + request, "codex")
        self.assertIn("> [!info]- 环境 / Skill 原文", rendered)
        self.assertIn("> ````text", rendered)
        self.assertIn(request, unquote_cards(rendered))
        self.assertIn(prefix, unquote_cards(rendered))
        self.assertLess(rendered.index("> ````\n"), rendered.index("> [!question] 你"))
        self.assertLess(rendered.index("> [!question] 你"), rendered.index("请保存这张图"))
        self.assertNotIn("[!info]", message_card(message, prefix, "codex", collapse_context=False))

    def test_style_setup_is_scoped_idempotent_and_preserves_user_settings(self):
        config = self.vault / ".obsidian" / "appearance.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"theme": "moonstone", "enabledCssSnippets": ["my-notes"]}), encoding="utf-8")
        command = [sys.executable, "-B", str(SCRIPT), "style", "--vault", str(self.vault)]
        first = subprocess.run(command, capture_output=True, encoding="utf-8")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(json.loads(first.stdout)["matches_bundled_style"])
        settings = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(settings["theme"], "moonstone")
        self.assertEqual(settings["enabledCssSnippets"], ["my-notes", "conversation-reading"])
        snippet = Path(json.loads(first.stdout)["snippet"])
        custom = snippet.read_bytes() + b"\n/* personal customization */\n"
        snippet.write_bytes(custom)
        config_before = config.read_bytes()
        second = subprocess.run(command, capture_output=True, encoding="utf-8")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse(json.loads(second.stdout)["matches_bundled_style"])
        self.assertEqual(snippet.read_bytes(), custom)
        self.assertEqual(config.read_bytes(), config_before)
        config.write_text("{invalid", encoding="utf-8")
        failed = subprocess.run(command, capture_output=True, encoding="utf-8")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(config.read_text(encoding="utf-8"), "{invalid")
        self.assertEqual(snippet.read_bytes(), custom)

    def test_image_path_decoding_rejects_ambiguous_drives_and_foreign_paths(self):
        for reference in (r"C:relative.png", "file://server/share/image.png", "file:////server/share/image.png", "ssh://host/image.png"):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                local_image_path(reference, windows=True)
        for reference in (r"C:\Images\photo.png", "C:/Images/photo.png", "file:///C:/Images/photo.png"):
            with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, "WSL"):
                local_image_path(reference, windows=False)
        self.assertEqual(local_image_path("file:///tmp/photo%20%E5%9B%BE.png", windows=False), "/tmp/photo 图.png")

    def test_native_paths_relative_paths_and_file_uris_export_the_same_image(self):
        local = self.project / "图片 space #100%.png"
        original = b"test image bytes"
        local.write_bytes(original)
        references = (str(local), local.as_uri(), quote(local.name))
        for client in self.paths:
            rows = self.fixture_rows(client, texts=["\n".join(f"![image](<{ref}>)" for ref in references)])
            self.write_rows(self.paths[client], rows)
            result = self.export(client, "--all")
            self.assertEqual(result["image_files"], 1)
            self.assertEqual(result["unavailable_images"], 0)
            note = Path(result["path"])
            self.assertEqual(note.read_text(encoding="utf-8").count("![image](<"), 3)
            self.assertEqual(next(note.with_suffix(".assets").iterdir()).read_bytes(), original)

    def test_cli_json_stays_utf8_under_an_ascii_stdio_environment(self):
        command = [sys.executable, "-B", str(SCRIPT), "read", "--home", str(self.home),
                   "--project", str(self.project), "--client", "codex", "--source", str(self.paths["codex"])]
        result = subprocess.run(command, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "ascii"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.decode("utf-8"))["messages"][1]["text"], self.text[1])

    def test_missing_images_are_visible_and_local_roots_remain_scoped(self):
        local = self.base / "elsewhere.png"
        local.write_bytes(b"separate image")
        self.write_rows(self.paths["claude"], self.fixture_rows("claude", texts=[f"![photo](<{local}>)"]))
        result = self.export("claude", "--all")
        self.assertEqual(result["unavailable_images"], 1)
        self.assertIn("图片暂不可用", Path(result["path"]).read_text(encoding="utf-8"))
        allowed = self.export("claude", "--all", "--asset-root", str(self.base))
        self.assertEqual(allowed["image_files"], 1)
        self.assertEqual(allowed["unavailable_images"], 0)

    def test_live_prefix_and_eighty_mib_source_export_one_small_note(self):
        path = self.paths["codex"]
        self.write_rows(path, self.fixture_rows("codex", texts=["Visible beginning", "Visible end"]))
        output = "TOOL-SECRET " * 22000
        with path.open("ab") as handle:
            while handle.tell() < 80 * 1024 * 1024:
                handle.write((json.dumps({"type": "response_item", "payload": {"type": "function_call_output", "output": output}}) + "\n").encode())
        preview = self.run_cli("read", "codex", "--limit", "1")
        self.assertGreater(preview["source_bytes"], 78 * 1024 * 1024)
        with path.open("ab") as handle:
            handle.write(b'{"type":"response_item","payload":{"type":"message","role":"user","content":"LATER"}}\n')
        wrapper = "import runpy,sys,pathlib; p=sys.argv.pop(1); sys.path.insert(0,str(pathlib.Path(p).parent)); runpy.run_path(p,run_name='__main__')"
        if os.name != "nt":
            wrapper = "import resource; " + wrapper + "; print('RSS='+str(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),file=sys.stderr)"
        command = [sys.executable, "-B", "-c", wrapper, str(SCRIPT), "export", "--client", "codex", "--home", str(self.home),
                   "--project", str(self.project), "--source", str(path), "--vault", str(self.vault), "--all",
                   "--source-bytes", str(preview["source_bytes"]), "--expect-version", preview["version"]]
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        note = Path(result["path"])
        self.assertLess(note.stat().st_size, 4096)
        self.assertEqual(result["saved_messages"], 2)
        self.assertIn("Visible beginning", note.read_text(encoding="utf-8"))
        self.assertIn("Visible end", note.read_text(encoding="utf-8"))
        self.assertNotIn("TOOL-SECRET", note.read_text(encoding="utf-8"))
        self.assertNotIn("LATER", note.read_text(encoding="utf-8"))
        self.assertEqual([p for p in self.vault.rglob('*') if p.is_file()], [note])
        if os.name != "nt":
            peak = int(process.stderr.strip().split("RSS=")[-1])
            peak_bytes = peak if sys.platform == "darwin" else peak * 1024
            self.assertLess(peak_bytes, 80 * 1024 * 1024)
            print(f"80 MiB source -> one {note.stat().st_size}-byte note; peak RSS {peak_bytes / 1024 / 1024:.1f} MiB", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
