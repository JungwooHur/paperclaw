"""A repair that reports a change it did not make never stops running.

The source-URL cleaner decided it had changed a run the moment the run LOOKED
like it needed cleaning, not when the cleaning actually altered anything. Where
the two disagreed it rewrote the block with identical content — and did it again
five minutes later, forever. The writes were invisible (the text never moved)
but they kept bumping the page's last-edited time, which kept the page inside
the healer's window, which kept every other healer re-running on it too.
"""
import clean_source_urls as cs


def run(text):
    return {"type": "text", "text": {"content": text},
            "plain_text": text, "annotations": {"bold": False}}


class TestReportingOnlyRealChanges:

    def test_a_run_it_actually_cleans_is_reported(self):
        dirty = run("본문입니다 https://arxiv.org/html/1234.5678v1 계속")
        _, changed = cs._clean_runs([dirty])
        assert changed

    def test_the_cleaning_is_applied(self):
        dirty = run("본문입니다 https://arxiv.org/html/1234.5678v1 계속")
        out, _ = cs._clean_runs([dirty])
        assert "arxiv.org" not in out[0]["text"]["content"]

    def test_a_run_it_cannot_change_is_not_reported(self):
        # The case that looped, and it is ordinary prose: a paper listing its
        # training data names arXiv.org in a sentence. The detector matches the
        # bare words; the cleaner only strips real URLs, so there is nothing to
        # do — and saying otherwise rewrote the block every five minutes.
        text = "arXiv – arXiv.org의 물리학 및 수학 논문 LaTeX 소스."
        assert cs._has_junk(text)                     # the detector fires…
        assert cs._clean_text(text) == text           # …and there is nothing to do
        _, changed = cs._clean_runs([run(text)])
        assert not changed

    def test_an_ordinary_run_is_not_reported(self):
        _, changed = cs._clean_runs([run("평범한 본문 문단입니다.")])
        assert not changed

    def test_a_clean_run_is_returned_untouched(self):
        original = run("평범한 본문 문단입니다.")
        out, _ = cs._clean_runs([original])
        assert out[0] is original


class TestTheRepairIsIdempotent:
    """Running it twice must be the same as running it once — otherwise the
    five-minute healer never converges."""

    def test_a_second_pass_finds_nothing(self):
        dirty = run("본문 https://arxiv.org/html/1234.5678v1 계속")
        once, _ = cs._clean_runs([dirty])
        _, changed_again = cs._clean_runs(once)
        assert not changed_again

    def test_a_run_that_becomes_empty_is_dropped_once(self):
        out, changed = cs._clean_runs([run("https://arxiv.org/html/1234.5678v1")])
        assert changed and out == []
        _, changed_again = cs._clean_runs(out)
        assert not changed_again


class TestTheOtherRepairsConverge:
    """The same shape — "it matched, therefore I changed it" — exists in the
    furniture stripper. There the pattern always replaces a phrase with a single
    space, so it cannot currently claim a change it did not make. This pins that
    down, because the day an alternative matches a single space the page starts
    being rewritten every five minutes and nothing else would notice."""

    def test_stripping_furniture_is_idempotent(self):
        import strip_furniture as sf
        dirty = [run("본문입니다 Report an issue with the previous element 계속")]
        once, changed = sf._strip_inline_runs(dirty)
        assert changed
        _, again = sf._strip_inline_runs(once)
        assert not again

    def test_it_reports_nothing_on_ordinary_prose(self):
        import strip_furniture as sf
        _, changed = sf._strip_inline_runs([run("평범한 본문입니다.")])
        assert not changed
