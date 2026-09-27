"""Concise internal pilot report derived from the validated checkpoint only."""
import argparse
import json
from pathlib import Path

LABELS = {'orientation': 'Orientation', 'proportions': 'Relative size',
          'relationships': 'Object relationships', 'access': 'Access',
          'layoutgpt': 'LayoutGPT', 'infinigen_controlled': 'Controlled Infinigen'}


def markdown(report, evidence_name):
    if not report['complete'] or report['rubricVersion'] != 'functional-use-v2':
        raise ValueError('A complete v2 checkpoint is required')
    accepted = report['pilotAccepted']
    lines = ['# Functional-use-v2 pilot: internal checkpoint', '',
             '**Accepted for unchanged-protocol reuse.**' if accepted else '**Not accepted for continuation.**', '',
             f"All {report['responses']} judgments completed: 256 main judgments and 64 controls. "
             'Acceptance requires at least 15/16 exact repeat agreements in **every** dimension, including ties.', '',
             '## Design', '',
             'The seeded, quality-blind sample contains 32 pairs, eight per baseline × room type. '
             'The 70 development pairs were excluded; the final 480-pair sample remains unchanged. '
             'Each revised dimension has two streams of 32 main cases and eight reversed controls. '
             'Each judgment used GPT-5.6 Sol at Extra High effort in a fresh context.', '',
             'Numbered plan, oblique and bird’s-eye views preserve the original geometry and fronts. '
             'Controls exchange identical room pixels. Structured observations cover every required '
             'object or pair; only relative-size judgments receive computed volume ratios.', '',
             '## Exact-agreement gate', '',
             '| Dimension | Exact agreement | Required | Outcome |',
             '| --- | ---: | ---: | --- |']
    for key in ('orientation', 'proportions', 'relationships', 'access'):
        row = report['byDimension'][key]
        count, total = row['agreements'], row['comparisons']
        lines.append(f"| {LABELS[key]} | {count}/{total} ({100 * count / total:.1f}%) | 15/16 | "
                     f"{'Met' if count >= 15 and total == 16 else 'Below threshold'} |")
    lines += ['', '## Repeat transitions', '',
              'Exact agreement means selecting the same physical room after the swap, or tying both times. '
              'A tie/preference change is distinct from choosing opposite rooms. Decisive agreement '
              'uses only controls with a winner on both viewings; a zero denominator is unavailable, not zero.', '']

    def table(rows):
        result = ['| Group | Exact | Tie ↔ preference | Opposite winner | Same winner among decisive |',
                  '| --- | ---: | ---: | ---: | ---: |']
        for label, row in rows:
            n = row['comparisons']
            exact = row['same_tie'] + row['same_winner']
            decisive = row['decisiveComparisons']
            decisive_text = (f"{row['same_winner']}/{decisive} ({row['decisiveAgreementPct']:.1f}%)"
                             if decisive else 'Unavailable (0 decisive)')
            result.append(f"| {label} | {exact}/{n} | {row['tie_preference_change']}/{n} | "
                          f"{row['winner_reversal']}/{n} | {decisive_text} |")
        return result

    transitions = report['repeatTransitions']
    lines += table([('All', transitions['overall'])] + [(LABELS[key], row) for key, row in transitions['profile'].items()])
    reviewer_profiles = {row['reviewerId']: row['profile'] for row in report['reviewers']}
    lines += ['', '### By reviewer stream', '']
    lines += table([(key + ' · ' + LABELS[reviewer_profiles[key]], row)
                    for key, row in transitions['reviewerId'].items()])
    lines += ['', '### By baseline', '']
    lines += table([(LABELS[key], row) for key, row in transitions['baseline'].items()])
    lines += ['', '## Retention and limits', '',
              ('All 256 main judgments may be reused only while this protocol remains unchanged. '
               'No further collection is authorized by this report.' if accepted else
               'All answers remain development evidence. The pilot does not pass the agreed continuation '
               'gate; no main votes are admitted under a changed protocol.'), '',
              'Controls never contribute preference votes. Original room-function answers and their '
              'prompt/stimulus provenance are unchanged. No disagreement was removed or reanswered. '
              'This small repeat check measures stability, not accuracy or population reliability.', '',
              f'[The complete checkpoint]({evidence_name}) includes all observations, answers, exact '
              'prompts, response schema, stimulus hashes and subgroup counts. Private context identifiers '
              'are hashed. Original execution transcripts remain in the frozen local pilot directory. '
              'No full campaign, version bump, push or publication was performed.', '',
              f"Protocol SHA-256: `{report['protocolSha256']}`.", '',
              f"Retained room-function SHA-256: `{report['retainedRoomFunctionSha256']}`.", '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.checkpoint.read_text(encoding='utf-8'))
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(markdown(report, args.checkpoint.name))
