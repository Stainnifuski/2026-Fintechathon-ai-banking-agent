"""Development-only pure bank template composer; runtime loads fixed final values."""
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from .wealth_synthetic import generate
CONFIG=Path(__file__).resolve().parents[3]/"config/wealth"


def _generate_bank_products(config_dir=CONFIG, source_payload=None):
    manifest = json.loads((config_dir / 'bank_rules.json').read_text(encoding='utf-8'))
    source_products = [p for p in (source_payload or generate())['products'] if p['kind']=='bank_wealth']
    result = []
    offsets = {}
    for mapping in manifest['products']:
        template = manifest['templates'][mapping['template_id']]
        compatible = [p for p in source_products if p['category'] == template['category']]
        index = offsets.get(template['category'], 0)
        base = compatible[index % len(compatible)]
        offsets[template['category']] = index + 1
        p = deepcopy(base); pid = mapping['product_id']; rules = template['simulation_rules']
        p.update(product_id=pid, name=mapping['name'], fund_family_id='BANK-TEMPLATE-'+mapping['template_id'],
            share_class=None, manager='虚构青穗模拟理财机构', source_product_code=None, identity='模拟银行理财／官方规则参考',
            official_reference=template['source'], rules_adaptations=template['adaptations'],
            source_rules=template['source_rules'], rules_version=manifest['version'], product_version=1, disclosure_version=1,
            category=template['category'], risk_level=template['simulated_risk'], rules={**rules, 'currency':'CNY',
            'dataset_id':'mixed-preload-v12', 'fund_family_id':'BANK-TEMPLATE-'+mapping['template_id'],
            'rules_source':'official_reference_simulation_v1', 'source_template_id':mapping['template_id']},
            quality_flags=['simulated_nav', 'official_rules_reference_not_real_sale', 'shared_template_not_independent_risk'],
            benchmark='模拟因子基准；不采用说明书比较基准作为收益', simulated=True)
        # Reuse factor history with explicit fee difference, not source product NAV.
        old_rate=sum((Decimal(base['rules'][k]) for k in ('annual_management_rate','annual_custody_rate','annual_service_rate')),Decimal(0))
        new_rate=sum((Decimal(p['rules'][k]) for k in ('annual_management_rate','annual_custody_rate','annual_service_rate')),Decimal(0))
        adjustment=(Decimal(1)+(old_rate-new_rate)/252)
        for i, point in enumerate(p['history'],1):
            point['nav']=format((Decimal(point['nav'])*adjustment**i).quantize(Decimal('0.00000001')),'f')
            point.pop('income_per_10000',None); point.pop('seven_day_annualized',None)
        if mapping.get('quick_contract'):
            p['subtype'] = 'cash_management'
            p['rules_version'] = mapping['quick_contract']['version']
            p['rules'] = {**p['rules'], 'min_amount_fen': 100, 'quick_redemption': mapping['quick_contract'],
                'nav_publication_time': '18:00', 'ordinary_cancellation': {'policy': 'full_before_pricing_cutoff',
                'cutoff': '15:30', 'end_inclusive': False, 'timezone': 'Asia/Shanghai'},
                'allow_nontrading_queue': True}
            p['rules_adaptations'] = [*p['rules_adaptations'],
                '新增模拟现金管理快赎：1元起购、模拟窗口和1万元自然日限额；同事务成交到账，无垫资/后续清算，成功不可撤',
                '快赎净值仅用当时已发布版本；普通申赎仍按T+1模拟日历']
        result.append(p)
    return result

