"""Self-authored corpus, bounded Chinese lexical retrieval; no embedding/network dependency."""
import re
from datetime import datetime, timezone
from sqlalchemy import select
from ...domain.wealth_synthetic import fingerprint
from ...models.wealth_research import WealthKnowledgeORM

VERSION='knowledge-v12-1'
ACTIVE_PROJECT_VERSION = 'project-20261022-grounding-v1'
CURRENT_PROJECT = (
 ('适当性与本人评估', '本人独立完成风险评估并确认；服务端按风险等级、有效期和版本核验。模型不能代答、抬高等级或绕过风控。净值产品本金可能亏损。'),
 ('本人确认与统一MFA', '当前账户、资金和成交均为比赛模拟。申购、赎回须本人在可信卡片核对确认，并完成统一TOTP或短信验证码验证。短信默认本地模拟发送，不连接运营商。聊天文字不构成授权，不存在点击模拟验证替代当前MFA的入口。'),
 ('模拟即时申购赎回与到账', '当前按最近已公布有效净值，经本人确认与统一MFA后即时扣款增加持仓，或按规则赎回到账。具体可用资金、份额、费用、窗口和最终状态必须查询银行结果。草稿与预览不代表成交，结果未知不能换号重下；持有期、额度及幂等保护仍保留。'),
 ('净值分析与费用', '历史净值涨跌不代表未来回报；未核实分红时不声明分红再投资总回报，历史不足一年不年化。费用和赎回限制以当前产品已核实规则为准，不根据其他产品或旧资料补造。'),
 ('资料版本与数据范围', '真实基金公开资料、模拟银行规则和本人账户数据分别标源。公开资料有数据日期，不保证实时；账户及成交仍为模拟。当前产品资格、报价、可用现金和交易状态以对应银行查询及版本为准，历史知识片段不能代替当前查询。'),
)


def current_project_documents():
    """当前流程说明以新ID发布；旧库和历史引用保持原样，不在查询时写库。"""
    return [dict(document_id=f'KP-20261022-{index}', title=title, excerpt=content,
                 source_type='project', source_url=None, published_at='2026-10-22',
                 valid_from='2026-10-22', valid_until=None, product_ids=[], dataset_key=None,
                 version=ACTIVE_PROJECT_VERSION, content_hash=fingerprint(content),
                 license='project-original', location='项目当前流程说明')
            for index, (title, content) in enumerate(CURRENT_PROJECT)]
OFFICIAL=(
 ('PPI 工业生产者出厂价格','https://www.stats.gov.cn/zs/tjws/zytjzbqs/gyscz/202409/t20240910_1956355.html','2024-09-13','PPI观察工业产品首次出售时的出厂价格变动，属于生产环节价格指标。它与居民消费价格覆盖范围不同，不能用一个指标替代另一个，也不能直接预测单个基金净值。'),
 ('CPI 居民消费价格','https://jszd.stats.gov.cn/TrueCMS/gjtjjjsdczd/zbjs/content/0095acca-acbd-4dc4-8ecc-d10b7a59e739.html',None,'CPI反映居民日常消费商品和服务价格水平的变动。个人消费结构与统计篮子不同，因此个人感受可能不同。通胀背景可辅助理解实际购买力，但不能据此保证产品回报。'),
 ('M1 M2 货币供应量口径','https://www.pbc.gov.cn/diaochatongjisi/attachDir/2025/11/2025111913535682666.pdf','2025-11-19','2025年起M1统计口径调整；比较跨口径同比指标须标注断点。M1和M2反映不同层次的货币供应量，不等于某一产品的资金流入，更不等于投资收益。'),
 ('PMI 采购经理指数','https://www.stats.gov.cn/zs/tjws/tjzb/202301/t20230101_1903972.html','2025-03-21','PMI是月度调查汇总的经济景气指标，50为扩张与收缩的分界参考。制造业与非制造业需分别阅读。调查指标不是产品交易信号，不能单凭一个月数值决定申赎。'),
 ('LPR 贷款市场报价利率','https://www.pbc.gov.cn/zhengcehuobisi/125207/125227/125957/3830536/ed13cc6378964d7eb67a85649b922cd4/2019111521260575804.pdf','2019-11-15','LPR是贷款市场报价利率，有1年期和5年期以上两个品种。它属于贷款定价参考，并非存款或理财产品承诺收益率；分析时须区分期限与传导的不确定性。'))
PROJECT=(
 ('适当性与本人评估','本人独立完成评估并确认结果后，服务端按风险等级、有效期、画像版本过滤。拒绝本金损失则不建议申购净值产品；模型不能代答、抬高等级或绕过风控。'),
 ('模拟点击授权与未来替换','当前仅虚构账户和模拟交易。本人确认后点击高风险模拟验证按钮；证据标记simulated和replacement_required。生产化必须整体替换验证提供者，保留参数绑定、过期、审计和幂等。'),
 ('申购赎回与到账','申购受理先占款；按指定模拟计价日净值结算。赎回占份额、FIFO批次计算费用，确认与现金到账分开。未结算订单不能当作持仓；锁定份额不能强制赎回。'),
 ('净值分析与费用','年度管理、托管和服务费用已含模拟净值，不再次扣除。申赎费用独立计算。未核实分红时展示单位净值涨跌而非总回报；不足一年不年化，历史回撤不是最大未来损失。'),
 ('数据集与资金上下文','synthetic为可复现合成历史；snapshot为真实来源快照；live_sync为后台更新。真实数据不是实时保证。切换不复制余额，旧预览失效，已受理订单保留原数据版本与资金账户。'))
GROUP_TEXT={
 'money':'货币类使用万份收益与七日年化作辅助研究；本项目以净值等价模型计价，不实现真实货币基金收益结转。低波动并不等于保本。',
 'bond':'债券类仍可能因利率、信用和流动性出现回撤；短债、纯债、可转债风险来源不同。期限较短也不保证本金安全。',
 'broad_index':'宽基指数模拟股票市场共同因子。指数覆盖范围不消除市场回撤，历史相关性不能证明未来分散效果。',
 'sector':'行业主题使用较高权益因子暴露。行业集中风险高，不能仅凭近期上涨或宏观单项指标判断适合所有人。',
 'active':'主动混合类同时受权益、债券和信用因子影响。模拟经理变更日期只作背景标签，不能据此声称真实经理表现。',
 'bank_wealth':'银行理财仍为净值型模拟产品。须区分最短持有期、开放窗口、确认日和现金到账日，不能把销售机构理解为本金担保。'}


def documents(products: list[dict]) -> list[dict]:
    rows=[]
    families={p['fund_family_id']:[] for p in products}
    for p in products: families[p['fund_family_id']].append(p)
    for family,shares in families.items():
        p=shares[0]; rules=p['rules']; names='、'.join(x['name'] for x in shares)
        body=f"{names}是同一虚构家族{family}的A/C份额。{GROUP_TEXT[p['group']]} 本家族成立日{p['inception_date']}，模拟R{p['risk_level']}，类别{p['category']}，基准{p['benchmark']}，管理机构{p['manager']}。共同因子暴露{p['exposure']}；同家族份额不算两份分散投资。A份额申购费与C份额年度服务费的差异需要结合持有期限比较，不能直接比净值高低。最短持有{rules['holding_days']}天，确认{rules['confirm_business_days']}模拟营业日，赎回现金{rules['cash_business_days']}模拟营业日，开放间隔{rules['open_interval_days']}天。金额上下限是模拟单笔限制，申赎费用以银行规则版本为准。{'本家族历史不足一年，不提供年化。' if p['inception_date'].startswith('2026') else '可研究多年历史和共同观察窗口；过去表现不保证未来。'} 资料为项目原创，不是发行人披露。"
        rows.append(dict(document_id='K-'+family,title=names+' 家族说明',content=body,source_type='synthetic_product',source_url=None,published_at='2026-09-30',product_ids=[s['product_id'] for s in shares],dataset_key=p['dataset_id']+':'+p['dataset_version']))
    for i,(title,body) in enumerate(PROJECT): rows.append(dict(document_id=f'K-P{i}',title=title,content=body,source_type='project',source_url=None,published_at='2026-09-30',product_ids=[],dataset_key=None))
    for i,(title,url,published,body) in enumerate(OFFICIAL): rows.append(dict(document_id=f'K-O{i}',title=title,content=body,source_type='official_summary',source_url=url,published_at=published,product_ids=[],dataset_key=None))
    for r in rows:
        r.update(version=VERSION,content_hash=fingerprint(r['content']),valid_from=r['published_at'],valid_until=None,license='project-original-summary; linked source rights reserved',ingested_at=datetime.now(timezone.utc).isoformat())
    return rows


async def seed(db,products):
    for r in documents(products):
        if await db.get(WealthKnowledgeORM,r['document_id']) is None:
            db.add(WealthKnowledgeORM(document_id=r['document_id'],metadata_json={k:v for k,v in r.items() if k!='content'},content=r['content']))
    await db.flush()


async def seed_mixed(db, payload):
    """Version-bound source summaries; never manufacture issuer risk or transaction rules."""
    key = payload['dataset_id'] + ':' + payload['dataset_version']
    families = {}
    for product in payload['products']:
        family = product.get('fund_family_id') or product['product_id']
        families.setdefault(family, []).append(product)
    for family, shares in families.items():
        p = shares[0]
        names = '、'.join(s['name'] for s in shares)
        if p['source'] == 'akshare':
            body = f"{names}：真实基金数据，仅研究。类别{p['group']}；来源{p['source_url']}。风险等级、完整交易规则尚未审核，不可申赎。"
            body += '单位净值涨跌不是已验证分红再投资总回报。'
            body += '关联份额不作为独立风险来源。' if p.get('fund_family_id') else '家族关联尚未核实，不声称独立分散。'
            source = p['source_url']
        else:
            body = f"{names}：模拟银行理财／官方规则参考，资金、净值及成交均虚构。"
            body += '官方原始规则：' + str(p['source_rules']) + '；模拟调整：' + str(p['rules_adaptations'])
            body += '；原说明书不证明当前真实在售，交易仅按项目模拟规则。'
            source = p['official_reference']['url']
        did = 'KM-' + fingerprint([key, family])[:40]
        if await db.get(WealthKnowledgeORM, did) is None:
            db.add(WealthKnowledgeORM(document_id=did, content=body, metadata_json={
                'document_id':did, 'title':names, 'source_type':'real_fund_summary' if p['source']=='akshare' else 'official_rules_simulation',
                'source_url':source, 'dataset_key':key, 'product_ids':[s['product_id'] for s in shares],
                'location':p.get('official_reference', {}).get('pages') or '公开基金档案／来源缓存字段',
                'published_at':None, 'valid_from':payload['as_of'], 'valid_until':None,
                'version':payload['dataset_version'], 'content_hash':fingerprint(body),
                'license':'project-original-summary; linked source rights reserved'}))
    await db.flush()
    return len(families)


def tokens(text: str) -> set[str]:
    text=text.casefold()
    result=set(re.findall(r'[a-z0-9]+',text))
    for part in re.findall(r'[\u4e00-\u9fff]+',text):
        result.update(part[i:i+2] for i in range(len(part)-1))
    return result


async def retrieve(db,query: str,dataset_key: str|None=None,limit: int=4,as_of: str|None=None) -> list[dict]:
    at=as_of or datetime.now(timezone.utc).date().isoformat(); wanted=tokens(query); ranked=[]
    candidates = current_project_documents() if at >= '2026-10-22' else []
    for row in (await db.scalars(select(WealthKnowledgeORM))).all():
        m=row.metadata_json
        # 历史项目流程仅供历史引用，不再解释当前授权及成交行为。
        if m.get('source_type') == 'project': continue
        if m.get('dataset_key') and m['dataset_key']!=dataset_key: continue
        if m.get('valid_from') and m['valid_from']>at or m.get('valid_until') and m['valid_until']<at: continue
        if m.get('content_hash') != fingerprint(row.content): continue
        candidates.append({**m, 'excerpt': row.content[:1600]})
    for m in candidates:
        title=tokens(m['title']); body=tokens(m['excerpt'])
        score=len(wanted&body)+3*len(wanted&title)
        if score>=2: ranked.append((score,m['document_id'],m))
    return [r[2] for r in sorted(ranked,key=lambda r:(-r[0],r[1]))[:min(max(limit,1),6)]]


def optional_nodes(rows):
    """Explicit optional LlamaIndex core integration; lexical retrieval remains default."""
    from llama_index.core.schema import TextNode
    return [TextNode(id_=r['document_id'],text=r['excerpt'],metadata={'source_url':r.get('source_url'),'content_hash':r['content_hash']}) for r in rows]
