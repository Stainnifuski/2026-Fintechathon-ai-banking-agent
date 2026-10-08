"""Test-only recorded provider; never registered with the customer application.

This replays agreed sample outputs instead of inventing A's configuration algorithm.
New recommendation/revision cases must be added as recorded fixtures with A.
"""
from copy import deepcopy
import json
from pathlib import Path


class RecordedPortfolioProvider:
    def __init__(self):
        root = Path(__file__).parent
        self._cases = {
            name: json.loads((root / f'{name.lower()}.json').read_text(encoding='utf-8'))
            for name in ('F01', 'F18', 'F10')
        }

    def snapshot(self, case='F01'):
        return deepcopy(self._cases[case]['snapshot'])

    def plan(self, case='F01'):
        if 'plan' not in self._cases[case]:
            raise LookupError(f'{case}: no recorded plan; do not reuse a different snapshot')
        return deepcopy(self._cases[case]['plan'])

    def recommend(self, case='F01'):
        # F01 deliberately has no candidates. Empty results are not a successful model.
        return deepcopy(self.plan(case)['candidate_groups'])

    def revise(self, selection, *, case='F01'):
        # B0 only replays the recorded no-op input. New ratios require A's recorded
        # response/validation; B must not normalize or approve the ratios itself.
        plan = self.plan(case)
        expected = {
            'portfolio_id': plan['portfolio_id'], 'expected_version': plan['version'],
            'snapshot_id': plan['snapshot_id'], 'context_id': plan['context_id'],
            'items': [],
        }
        if selection != expected:
            raise LookupError('No recorded revision for this exact selection')
        return plan
