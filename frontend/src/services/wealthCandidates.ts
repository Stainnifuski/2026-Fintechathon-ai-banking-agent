import type {CandidateGroup,Product} from './wealth';
import type {PortfolioPlan} from './wealth';
import type {ActionResult} from './wealthActions';

export function actionCandidateGroups(result:ActionResult|undefined):CandidateGroup[]{
  if(result?.status!=='ok')return [];
  if(result.action==='plan_portfolio')return (result.data as PortfolioPlan).candidate_groups;
  if(result.action==='recommend_products')return [result.data as CandidateGroup];
  return [];
}

// Consumer checks for fields read by ProductPicker. A remains responsible for
// model, risk eligibility and canonical DTO validation; this creates no advice.
type ObjectValue=Record<string,unknown>;
const object=(v:unknown):v is ObjectValue=>typeof v==='object'&&v!==null&&!Array.isArray(v);
const text=(v:unknown)=>typeof v==='string'&&v.length>0;
const optionalText=(v:unknown)=>v===null||typeof v==='string';
const integer=(v:unknown,min:number)=>Number.isSafeInteger(v)&&Number(v)>=min;
const decimal=(v:unknown)=>v===null||(typeof v==='string'&&/^-?\d+(\.\d+)?$/.test(v));
const buckets=['cash_management','stable','growth'];
const statuses=['ok','no_theme_match','no_category_match','no_eligible_products','risk_mismatch','unavailable'];
function metric(v:unknown,year:boolean):boolean{
  return object(v)&&text(v.metric)&&text(v.status)&&decimal(v.value_percent)&&optionalText(v.reason)
    &&(year?optionalText(v.start_date)&&optionalText(v.end_date)&&optionalText(v.source):optionalText(v.nav_date)&&optionalText(v.previous_nav_date));
}
export function readCandidateGroup(value:unknown):CandidateGroup{
  const invalid=()=>{throw new Error('推荐资料格式不完整，请刷新或与配置服务核对；不能据此选择产品。');};
  if(!object(value)||!statuses.includes(String(value.status))||!object(value.filter)||!Array.isArray(value.items)
    ||!(value.bucket===null||buckets.includes(String(value.bucket)))||!(value.next_cursor===undefined||optionalText(value.next_cursor))||!(value.risk_explanation==null||object(value.risk_explanation)))return invalid();
  const filter=value.filter;
  if(!['general','category','name','theme'].includes(String(filter.mode))
    ||(filter.mode==='name'&&!text(filter.name_query))
    ||(filter.mode==='category'&&!text(filter.category))||(filter.mode==='theme'&&!text(filter.theme_key)&&!(Array.isArray(filter.theme_keys)&&filter.theme_keys.length&&['AND','OR'].includes(String(filter.theme_operator)))))return invalid();
  if(value.status==='risk_mismatch'&&(!object(value.risk_explanation)||!integer(value.risk_explanation.user_level,1)||!Array.isArray(value.risk_explanation.product_levels)||!value.risk_explanation.product_levels.every(n=>integer(n,1))))return invalid();
  const families=new Set<string>(),products=new Set<string>();
  for(const c of value.items){
    if(!object(c)||!text(c.family_id)||families.has(String(c.family_id))||!text(c.name)||!buckets.includes(String(c.bucket))||!text(c.reason)
      ||!Array.isArray(c.reason_codes)||!c.reason_codes.every(text)||!(object(c.evidence)||Array.isArray(c.evidence))||!Array.isArray(c.share_classes)||!c.share_classes.length)return invalid();
    families.add(String(c.family_id));
    for(const s of c.share_classes){
      if(!object(s)||!text(s.product_id)||!integer(s.product_version,1)||!integer(s.disclosure_version,1)||![null,'A','C'].includes(s.share_class as null|string)
        ||!(s.product_risk_level===null||integer(s.product_risk_level,1)&&Number(s.product_risk_level)<=5)||!text(s.status)||typeof s.tradable!=='boolean'
        ||!(s.min_amount_fen===null||integer(s.min_amount_fen,0))||!object(s.fees)||!decimal(s.fees.purchase_fee_rate)||!decimal(s.fees.sales_service_fee_rate)||!(optionalText(s.fees.redemption_fee_rules)||Array.isArray(s.fees.redemption_fee_rules))
        ||!object(s.performance)||!metric(s.performance.one_year,true)||!metric(s.performance.latest_day,false)||!text(s.detail_ref)||!optionalText(s.data_source)||!optionalText(s.data_as_of)||(s.data_as_of!==null&&Number.isNaN(Date.parse(String(s.data_as_of)))))return invalid();
      const ref=`${s.product_id}:${s.product_version}`;if(products.has(ref))return invalid();products.add(ref);
    }
  }
  return {...value,next_cursor:value.next_cursor??null,risk_explanation:value.risk_explanation??null} as unknown as CandidateGroup;
}
export function assertCurrentCandidates(group:CandidateGroup,products:Product[]):void{
  for(const c of group.items)for(const s of c.share_classes){
    if(!products.some(p=>p.product_id===s.product_id&&p.version===s.product_version))throw new Error('候选产品版本与当前目录不一致，请刷新推荐资料。');
  }
}
export function sameCandidateFilter(a:unknown,b:unknown):boolean{
  if(!object(a)||!object(b))return false;
  return ['mode','category','name_query','theme_key','bucket','theme_operator','theme_keys'].every(k=>
    JSON.stringify(a[k]??(k==='theme_keys'?[]:null))===JSON.stringify(b[k]??(k==='theme_keys'?[]:null)));
}
export function replaceCandidateGroup(groups:CandidateGroup[],next:CandidateGroup):CandidateGroup[]{
  const index=groups.findIndex(g=>g.bucket===next.bucket&&sameCandidateFilter(g.filter,next.filter));
  return index<0?[...groups,next]:groups.map((g,i)=>i===index?next:g);
}
