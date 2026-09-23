"""Not filing a second copy of an answer the agent already filed.

The backstop exists to catch an answer the agent FORGOT to save. It decided
"already saved" by comparing wording — and the agent writes a different, shorter
answer to the page than it sends to the chat, so the two never looked alike
enough. Measured on real pages: the duplicate pair overlapped 0.19, while
genuinely different questions on one page reach 0.26. Wording cannot separate
them, so it must not be asked to.

What separates them is WHEN — combined with a much weaker question match than
wording alone could justify. Neither signal decides on its own: a callout in the
window may belong to a different question asked in the same minutes, and a loose
question match may be two questions about one subject. Together they are
decisive. Measured across real pages, different questions on one page overlap
0.14 at the 95th percentile, and the duplicate pair measured 0.67.
"""
import backstop_timing as bt


ASKED = "2026-09-23T11:29:00.000Z"
REPLIED = "2026-09-23T11:32:00.000Z"
OURS = "이 논문에서 말하는 alpha-beta search와 MCTS는 어떻게 다른가요"
THEIRS = "학습률 스케줄은 어떻게 정했나요"


def callout(created, question):
    return {"created": created, "question": question}


class TestACalloutWrittenDuringTheExchange:

    def test_one_written_between_question_and_reply(self):
        assert bt.saved_during_exchange([callout("2026-09-23T11:31:00.000Z", OURS)],
                                     ASKED, REPLIED, OURS)

    def test_one_written_just_after_the_reply(self):
        assert bt.saved_during_exchange([callout("2026-09-23T11:33:00.000Z", OURS)],
                                     ASKED, REPLIED, OURS)

    def test_one_written_at_the_very_moment_of_the_question(self):
        assert bt.saved_during_exchange([callout(ASKED, OURS)], ASKED, REPLIED, OURS)

    def test_one_written_before_the_question_is_someone_else_s(self):
        assert not bt.saved_during_exchange(
            [callout("2026-09-23T11:00:00.000Z", OURS)], ASKED, REPLIED, OURS)

    def test_one_written_long_after_the_reply_is_someone_else_s(self):
        # Beyond the grace period this is a later exchange's callout, and
        # treating it as ours would silently drop the answer we are holding.
        assert not bt.saved_during_exchange(
            [callout("2026-09-23T12:30:00.000Z", OURS)], ASKED, REPLIED, OURS)

    def test_no_callouts_at_all(self):
        assert not bt.saved_during_exchange([], ASKED, REPLIED, OURS)

    def test_any_one_in_the_window_is_enough(self):
        assert bt.saved_during_exchange(
            [callout("2026-09-23T11:00:00.000Z", OURS),
             callout("2026-09-23T11:31:00.000Z", OURS)], ASKED, REPLIED, OURS)


class TestBadInput:

    def test_an_unparseable_timestamp_is_ignored(self):
        # Never let a malformed stamp decide; the wording check still runs.
        assert not bt.saved_during_exchange([callout("not a date", OURS)],
                                            ASKED, REPLIED, OURS)

    def test_an_unparseable_question_time_refuses_to_decide(self):
        assert not bt.saved_during_exchange(
            [callout("2026-09-23T11:31:00.000Z", OURS)], "nonsense", REPLIED, OURS)


class TestTheQuestionMustMatchToo:
    """A callout in the window may belong to a DIFFERENT question asked in the
    same minutes. Skipping on timing alone would bury an answer the agent
    genuinely forgot — the one thing the backstop exists to prevent."""

    def test_a_different_question_in_the_window_does_not_count(self):
        assert not bt.saved_during_exchange(
            [callout("2026-09-23T11:31:00.000Z", THEIRS)], ASKED, REPLIED, OURS)

    def test_the_agent_s_rephrasing_still_counts(self):
        rephrased = "alpha-beta search와 MCTS는 이 논문에서 어떻게 다른가"
        assert bt.saved_during_exchange(
            [callout("2026-09-23T11:31:00.000Z", rephrased)], ASKED, REPLIED, OURS)

    def test_one_matching_among_several_is_enough(self):
        assert bt.saved_during_exchange(
            [callout("2026-09-23T11:30:00.000Z", THEIRS),
             callout("2026-09-23T11:31:00.000Z", OURS)], ASKED, REPLIED, OURS)

    def test_a_question_too_short_to_judge_is_refused(self):
        assert not bt.saved_during_exchange(
            [callout("2026-09-23T11:31:00.000Z", "왜?")], ASKED, REPLIED, "왜?")
