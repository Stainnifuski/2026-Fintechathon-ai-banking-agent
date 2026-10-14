"""Production display ordering, not return/quality ranking; text DPP is research only."""
ORDER_VERSION = 'portfolio-code-category-round-robin-v1'
ORDER = ('money_market', 'bond', 'mixed', 'equity')

class CodeCategorySelector:
    version = ORDER_VERSION
    fixture_only = False

    def __init__(self):
        self.last_result = {}

    def select_batch(self, pool, excluded, limit=3, position=0):
        queues = {category: [] for category in ORDER}
        for item in sorted(pool, key=lambda x: x.default_share.product_id):
            category = 'money_market' if item.substantive_category == 'money' else item.substantive_category
            if item.family_id not in excluded and category in queues:
                queues[category].append(item.family_id)
        selected = []
        while len(selected) < min(3, limit) and any(queues.values()):
            for _ in ORDER:
                category = ORDER[position % len(ORDER)]
                position = (position + 1) % len(ORDER)
                if queues[category]:
                    selected.append(queues[category].pop(0))
                    break
        self.last_result = {'items': selected, 'algorithm': self.version,
            'n': len(pool), 'next_position': position, 'financial_rank': False,
            'stop_reason': 'cardinality_limit' if len(selected) == min(3, limit) else 'pool_exhausted'}
        return selected, position

    def select(self, pool, excluded, limit):
        return self.select_batch(pool, excluded, limit)[0]
