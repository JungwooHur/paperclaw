"""What counts as a question worth filing as a Q&A.

A paper page ended up with a callout reading "Q: 정리해" — the message that asked
for the paper to be PROCESSED, filed as though it were a question about it. Two
things had to go wrong together: the writer validated nothing at all, and the
judgement it should have used was fooled into saying yes.
"""
import question_shape as qs


ATTACHMENT = "\n\n[첨부 문서: /workspace/group/attachments/3EB067D6D1E8602C28438E.pdf]"


class TestTheAttachmentLineIsNotThePessage:

    def test_it_is_stripped_before_judging(self):
        assert qs.message_body("정리해" + ATTACHMENT) == "정리해"

    def test_a_message_without_one_is_untouched(self):
        assert qs.message_body("이 논문 설명해줘") == "이 논문 설명해줘"

    def test_the_english_form_is_stripped_too(self):
        assert qs.message_body("summarise\n\n[attached: /tmp/a.pdf]") == "summarise"


class TestRequestsAreNotQuestions:

    def test_the_bare_request_is_refused(self):
        assert not qs.is_question_like("정리해")

    def test_a_request_carrying_an_attachment_is_still_refused(self):
        # This is the one that got through: the path pushed it over the length
        # that the fallback treats as "long enough to be a question".
        assert not qs.is_question_like("정리해" + ATTACHMENT)

    def test_a_long_path_cannot_make_a_request_look_long(self):
        long_path = "\n\n[첨부 문서: /workspace/group/attachments/" + "a" * 200 + ".pdf]"
        assert not qs.is_question_like("정리해" + long_path)

    def test_other_processing_requests_are_refused(self):
        for req in ("번역해줘", "추가해", "이 논문 정리해줘"):
            assert not qs.is_question_like(req), req


class TestRealQuestionsStillPass:

    def test_a_direct_question(self):
        assert qs.is_question_like("이 논문의 value network는 어떻게 학습해?")

    def test_a_question_with_an_attachment(self):
        assert qs.is_question_like("이 논문에서 rollout policy가 왜 필요해?" + ATTACHMENT)

    def test_a_long_substantive_message_without_a_question_mark(self):
        assert qs.is_question_like(
            "policy network와 value network를 따로 학습시키는 이유가 궁금한데, "
            "두 네트워크가 같은 특징을 공유하면 안 되는 건지 설명 부탁해")


class TestTheWriterRefusesANonQuestion:
    """Every Q&A goes through one writer, by rule. It validated nothing, so the
    rule protected nothing: whatever the agent passed was written."""

    def test_a_request_is_rejected(self):
        import save_qa_callout as sq
        assert not sq.acceptable_question("Q: 정리해" + ATTACHMENT)

    def test_a_question_is_accepted(self):
        import save_qa_callout as sq
        assert sq.acceptable_question("Q: 이 논문의 value network는 어떻게 학습해?")

    def test_the_q_prefix_is_not_what_makes_it_a_question(self):
        import save_qa_callout as sq
        assert not sq.acceptable_question("Q: 번역해줘")

    def test_an_empty_question_is_rejected(self):
        import save_qa_callout as sq
        assert not sq.acceptable_question("Q:")
