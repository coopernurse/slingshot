from __future__ import annotations

import inspect
import re
from datetime import datetime
from unittest import mock

from slingshot import gh, git_utils
from slingshot.logging import log, log_cmd_output, log_lines


class TestLogTimestamp:
    def test_timestamp_format(self, capsys):
        log("test-message")
        captured = capsys.readouterr().out
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ", captured)

    def test_timestamp_is_local_time(self, capsys):
        log("test-message")
        captured = capsys.readouterr().out
        ts_str = captured[:19]
        logged_time = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        assert abs((now - logged_time).total_seconds()) < 2


class TestLogCmdOutput:
    def test_emits_one_line_per_nonempty_line(self, capsys):
        log_cmd_output(["git", "worktree"], "line1\n\nline2\n", "")
        captured = capsys.readouterr().out
        lines = captured.strip().split("\n")
        assert len(lines) == 2
        assert "line1" in lines[0]
        assert "line2" in lines[1]
        assert "event=cmd-output" in lines[0]

    def test_skips_empty_lines(self, capsys):
        log_cmd_output(["git", "status"], "\n\n\n", "\n")
        captured = capsys.readouterr().out
        assert captured.strip() == ""

    def test_tags_stream_correctly(self, capsys):
        log_cmd_output(["gh", "issue"], "stdout-line", "stderr-line")
        captured = capsys.readouterr().out
        assert "stream=stdout" in captured
        assert "stream=stderr" in captured
        assert "stdout-line" in captured
        assert "stderr-line" in captured

    def test_json_quotes_line_values(self, capsys):
        log_cmd_output(["cmd"], 'he said "hello"', "")
        captured = capsys.readouterr().out
        assert 'he said \\"hello\\"' in captured

    def test_no_output_produces_no_log(self, capsys):
        log_cmd_output(["cmd"], "", "")
        captured = capsys.readouterr().out
        assert captured == ""


class TestLogLines:
    def test_splits_multiline_text(self, capsys):
        log_lines("repo=x issue=1 event=agent-output", "line-a\nline-b\nline-c")
        captured = capsys.readouterr().out
        lines = captured.strip().split("\n")
        assert len(lines) == 3
        for line, expected in zip(lines, ["line-a", "line-b", "line-c"], strict=True):
            assert expected in line
            assert "event=agent-output" in line

    def test_skips_empty_lines(self, capsys):
        log_lines("repo=x event=test", "\n\ncontent\n\n")
        captured = capsys.readouterr().out
        lines = captured.strip().split("\n")
        assert len(lines) == 1
        assert "content" in lines[0]

    def test_json_quotes_line_values(self, capsys):
        log_lines("event=test", 'value with "quotes"')
        captured = capsys.readouterr().out
        assert '\\"quotes\\"' in captured

    def test_empty_text_produces_no_log(self, capsys):
        log_lines("event=test", "")
        captured = capsys.readouterr().out
        assert captured == ""


class TestCaptureParameterRemoved:
    def test_git_utils_run_no_capture_param(self):
        sig = inspect.signature(git_utils._run)
        assert "capture" not in sig.parameters

    def test_gh_run_no_capture_param(self):
        sig = inspect.signature(gh._run)
        assert "capture" not in sig.parameters


class TestRunRoutesOutputThroughLogger:
    def test_git_utils_run_logs_stdout(self, capsys):
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = "hello from git\n"
        mock_result.stderr = ""
        with mock.patch("subprocess.run", return_value=mock_result):
            git_utils._run(["git", "fetch", "origin"], cwd="/tmp")
        captured = capsys.readouterr().out
        assert "event=cmd-output" in captured
        assert "hello from git" in captured
        assert "stream=stdout" in captured

    def test_git_utils_run_logs_stderr(self, capsys):
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = "warning from git\n"
        with mock.patch("subprocess.run", return_value=mock_result):
            git_utils._run(["git", "status"])
        captured = capsys.readouterr().out
        assert "event=cmd-output" in captured
        assert "warning from git" in captured
        assert "stream=stderr" in captured

    def test_git_utils_run_no_raw_output(self, capsys):
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = "output\n"
        mock_result.stderr = ""
        with mock.patch("subprocess.run", return_value=mock_result):
            git_utils._run(["git", "log"])
        captured = capsys.readouterr().out
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ", captured)

    def test_gh_run_logs_stdout(self, capsys):
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/x/y/issues/1\n"
        mock_result.stderr = ""
        with mock.patch("subprocess.run", return_value=mock_result):
            gh._run(["gh", "issue", "edit", "--repo", "x/y", "1"])
        captured = capsys.readouterr().out
        assert "event=cmd-output" in captured
        assert "stream=stdout" in captured

    def test_gh_run_no_raw_output(self, capsys):
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = "output\n"
        mock_result.stderr = ""
        with mock.patch("subprocess.run", return_value=mock_result):
            gh._run(["gh", "label", "create", "--repo", "x/y", "test"])
        captured = capsys.readouterr().out
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ", captured)
