"""The predicate that keeps the Q&A backstop off the learning loop's own turns.

A layer explanation is a long, substantive answer about a paper, so the backstop
would file it as a Q&A callout — leaving the same material on the page twice, in
the wrong shape. What separates the two is the fixed set of field labels the
layer format carries, so that is what the predicate reads.
"""

import layer_format


LAYER = """방금 말씀하신 '펜을 쥔 사람만 쓴다'는 규칙 — 그게 실제로는 이렇게 됩니다.

**한 문장:** 토큰을 하나씩 내놓는 대신, 한 덩어리를 통째로 내놓습니다.
**그림:** 앞 층의 우체국에, 이번에는 소포를 묶는 컨베이어가 하나 붙습니다.
**어떻게:** 직접 해 보시면 — 같은 문장을 두 번 넣고 걸리는 시간을 재면, 두 번째가
짧습니다. 이게 어디까지 통하냐면 덩어리 길이가 고정된 경우까지입니다. 아무도 모르는
것은 덩어리 경계를 어떻게 정해야 최적인지입니다. 이걸 부르는 이름이 chunking입니다.
**그림이 깨지는 곳:** 소포는 순서를 바꿔 부칠 수 있지만 이쪽은 못 바꿉니다.
**새로 나온 말:** chunking — 여러 개를 한 번에 묶어 내보내는 것.
**근거:** 3절, 그리고 기억에서.
"""

ORDINARY = """질문 주신 부분을 정리하면 이렇습니다.

먼저 학습 단계에서는 데이터가 두 갈래로 들어갑니다. 하나는 이미지 인코더를 지나고,
다른 하나는 텍스트 쪽으로 갑니다. 둘이 만나는 지점이 중요한데, 여기서 차원을 맞추는
투영이 한 번 일어납니다.

- 첫째, 배치 크기가 성능에 꽤 크게 영향을 줍니다.
- 둘째, 학습률 스케줄이 논문에서 말한 것과 조금 다릅니다.
- 셋째, 평가 지표가 두 종류로 나뉘어 있습니다.

평가 쪽은 조금 더 복잡합니다. 보고된 수치가 두 벌인데, 하나는 단일 시점에서 측정한
것이고 다른 하나는 여러 시점을 평균낸 것입니다. 표에 붙은 각주를 보면 어느 쪽인지
구분할 수 있지만, 본문만 읽으면 같은 지표처럼 보입니다. 재현하실 때는 이 구분을 먼저
확인하시는 편이 좋습니다.

## 정리

그래서 결론만 말씀드리면, 이 논문에서 한 문장: 으로 요약할 만한 핵심은 투영 층이고,
나머지는 그 층을 어떻게 학습시키느냐의 문제입니다. 추가로 궁금하신 부분 있으면
말씀해 주세요.
"""


class TestRecognisingALayer:

    def test_a_message_in_the_layer_format_is_recognised(self):
        assert layer_format.is_layer(LAYER)

    def test_a_long_substantive_answer_is_not_a_layer(self):
        assert not layer_format.is_layer(ORDINARY)

    def test_an_empty_message_is_not_a_layer(self):
        assert not layer_format.is_layer('')

    def test_a_single_label_is_not_enough(self):
        assert not layer_format.is_layer('**한 문장:** 짧게 답하면 그렇습니다.')

    def test_a_label_mentioned_mid_sentence_does_not_count(self):
        prose = ('이 논문을 **한 문장:** 으로 줄이면, **근거:** 를 어디서 찾을지가 '
                 '문제이고 **그림:** 도 마찬가지입니다.')
        assert not layer_format.is_layer(prose)

    def test_the_format_is_recognised_without_bold_markers(self):
        assert layer_format.is_layer(LAYER.replace('**', ''))

    def test_a_layer_missing_one_field_is_still_a_layer(self):
        assert layer_format.is_layer(
            LAYER.replace('**그림이 깨지는 곳:** 소포는 순서를 바꿔 부칠 수 '
                          '있지만 이쪽은 못 바꿉니다.\n', ''))


class TestTheBackstopSkipsThem:

    def test_the_backstop_declines_a_layer(self):
        import auto_save_qa
        assert not auto_save_qa.is_substantive_answer(LAYER, paper_context=True)

    def test_the_backstop_still_files_an_ordinary_answer(self):
        import auto_save_qa
        assert auto_save_qa.is_substantive_answer(ORDINARY, paper_context=True)
