"""A one-line receipt must never be repeated wholesale as both head and tail."""

import pytest

from minicode.tooling import _smart_truncate_output


@pytest.mark.parametrize("tool", ["review_test", "review_submit", "read_file", "run_command", "grep_files"])
def test_huge_single_line_retains_disjoint_head_tail_within_limit(tool):
    original = 'HEAD_ASSERTION: expected 2, actual 1|' + "x" * 793_499 + '|TAIL: failed'
    result = _smart_truncate_output(original, tool, max_chars=8000)
    assert len(result) <= 8000
    assert result.startswith('HEAD_ASSERTION: expected 2, actual 1|')
    assert result.endswith('|TAIL: failed')
    assert result.count('HEAD_ASSERTION') == result.count('|TAIL: failed') == 1
    assert 'characters omitted' in result
    assert '-1 lines omitted' not in result


@pytest.mark.parametrize("limit", [1, 7, 50, 100])
def test_tiny_limits_never_expand_the_output(limit):
    assert len(_smart_truncate_output('x' * 1000, 'review_test', max_chars=limit)) <= limit


def test_short_output_is_byte_for_byte_unchanged():
    original = '{"status":"failed","message":"真实失败"}'
    assert _smart_truncate_output(original, 'review_test', max_chars=100) == original
