"""Unit tests for transcript find & replace + annotation offset migration."""

from app.services.transcript_edit import apply_find_replace


def _seg(text, speaker='A', words=None):
    return {'text': text, 'speaker': speaker, 'start': 0.0, 'end': 1.0,
            'words': words or []}


def _quote(segments, span):
    """Reconstruct the text a single-segment span points at (trimmed-text offsets)."""
    seg_text = (segments[span['segment_idx']]['text'] or '').strip()
    return seg_text[span['start_char']:span['end_char']]


def test_no_match_returns_zero_and_no_change():
    segments = [_seg('The cat sat on the mat')]
    annotations = {'tag_spans': [], 'comments': []}
    count = apply_find_replace(segments, annotations, 'dog', 'cat')
    assert count == 0
    assert segments[0]['text'] == 'The cat sat on the mat'


def test_empty_find_is_noop():
    segments = [_seg('hello world')]
    assert apply_find_replace(segments, {}, '', 'x') == 0
    assert segments[0]['text'] == 'hello world'


def test_basic_replacement_and_count():
    segments = [_seg('foo bar foo baz foo')]
    count = apply_find_replace(segments, {}, 'foo', 'qux')
    assert count == 3
    assert segments[0]['text'] == 'qux bar qux baz qux'


def test_span_after_edit_stays_on_same_word_when_shorter():
    # "great" sits after the replaced token; a length-shrinking replace must
    # shift its offsets so it still quotes "great".
    text = 'The kubernetis cluster is great'
    start = text.index('great')
    segments = [_seg(text)]
    annotations = {'tag_spans': [
        {'id': 't1', 'tag_id': 'pain', 'segment_idx': 0,
         'start_char': start, 'end_char': start + len('great')}
    ]}
    count = apply_find_replace(segments, annotations, 'kubernetis', 'k8s')
    assert count == 1
    assert segments[0]['text'] == 'The k8s cluster is great'
    assert _quote(segments, annotations['tag_spans'][0]) == 'great'


def test_span_after_edit_stays_on_same_word_when_longer():
    text = 'The k8s cluster is great'
    start = text.index('great')
    segments = [_seg(text)]
    annotations = {'tag_spans': [
        {'id': 't1', 'tag_id': 'ins', 'segment_idx': 0,
         'start_char': start, 'end_char': start + len('great')}
    ]}
    apply_find_replace(segments, annotations, 'k8s', 'kubernetes')
    assert segments[0]['text'] == 'The kubernetes cluster is great'
    assert _quote(segments, annotations['tag_spans'][0]) == 'great'


def test_span_before_edit_is_untouched():
    text = 'The great kubernetis cluster'
    start = text.index('great')
    segments = [_seg(text)]
    annotations = {'tag_spans': [
        {'id': 't1', 'tag_id': 'del', 'segment_idx': 0,
         'start_char': start, 'end_char': start + len('great')}
    ]}
    apply_find_replace(segments, annotations, 'kubernetis', 'k8s')
    assert _quote(segments, annotations['tag_spans'][0]) == 'great'


def test_span_covering_match_grows_with_replacement():
    text = 'I love kubernetis a lot'
    start = text.index('kubernetis')
    segments = [_seg(text)]
    annotations = {'tag_spans': [
        {'id': 't1', 'tag_id': 'ins', 'segment_idx': 0,
         'start_char': start, 'end_char': start + len('kubernetis')}
    ]}
    apply_find_replace(segments, annotations, 'kubernetis', 'kubernetes')
    assert _quote(segments, annotations['tag_spans'][0]) == 'kubernetes'


def test_match_case_option():
    segments = [_seg('Cat cat CAT')]
    count = apply_find_replace(segments, {}, 'cat', 'dog', match_case=True)
    assert count == 1
    assert segments[0]['text'] == 'Cat dog CAT'


def test_whole_word_option():
    segments = [_seg('cat category cats cat')]
    count = apply_find_replace(segments, {}, 'cat', 'dog', whole_word=True)
    assert count == 2
    assert segments[0]['text'] == 'dog category cats dog'


def test_word_tokens_updated_for_click_to_seek():
    words = [{'word': 'kubernetis', 'start': 1.0}, {'word': 'rocks', 'start': 2.0}]
    segments = [_seg('kubernetis rocks', words=words)]
    apply_find_replace(segments, {}, 'kubernetis', 'k8s')
    assert segments[0]['words'][0]['word'] == 'k8s'
    assert segments[0]['words'][1]['word'] == 'rocks'


def test_literal_replacement_not_regex_backref():
    segments = [_seg('say foo here')]
    apply_find_replace(segments, {}, 'foo', r'\1 & $0')
    assert segments[0]['text'] == r'say \1 & $0 here'


def test_cross_segment_merged_offsets_recomputed():
    # Span runs from "beta" (seg0) to "gamma" (seg1), both same speaker.
    seg0 = 'alpha kubernetis beta'
    seg1 = 'gamma delta'
    segments = [_seg(seg0, speaker='A'), _seg(seg1, speaker='A')]
    span = {
        'id': 't1', 'tag_id': 'pain',
        'segment_idx': 0, 'start_char': seg0.index('beta'),
        'end_segment_idx': 1, 'end_seg_end_char': len('gamma'),
        'merged_start': seg0.index('beta'),
        'end_merged_end': len(seg0) + 1 + len('gamma'),
    }
    annotations = {'tag_spans': [span], 'comments': []}
    apply_find_replace(segments, annotations, 'kubernetis', 'k8s')

    new0 = segments[0]['text']  # "alpha k8s beta"
    merged = new0 + ' ' + segments[1]['text']  # block text
    assert merged[span['merged_start']:span['end_merged_end']] == 'beta gamma'
    # Per-segment offsets also stay correct.
    assert new0[span['start_char']:] .startswith('beta')


def test_comments_are_migrated_too():
    text = 'The kubernetis is here now'
    start = text.index('now')
    segments = [_seg(text)]
    annotations = {'tag_spans': [], 'comments': [
        {'id': 'c1', 'segment_idx': 0, 'start_char': start,
         'end_char': start + len('now'), 'text': 'note'}
    ]}
    apply_find_replace(segments, annotations, 'kubernetis', 'k8s')
    c = annotations['comments'][0]
    assert segments[0]['text'][c['start_char']:c['end_char']] == 'now'
