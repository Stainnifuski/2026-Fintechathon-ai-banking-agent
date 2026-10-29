export const stepOrder=(kind:string):string[]=>kind==='planning'
 ?['match','cash_management','stable','growth','selected','budget','allocation','details','trade']
 :['redeem','positions'].includes(kind)?['redemption_input','trade']:['match','candidates','selected','amounts','purchase_review','trade'];

// Bucket stages belong to the planning flow. Target recommendations retain a
// bucket label for display, but advance through the shared candidates stage.
export const candidateStage=(showBuckets:boolean,bucket:string|null):string=>showBuckets&&bucket?bucket:'candidates';

export const effectiveStepKind=(kind:string,workflow:'planning'|'target'|null,workflowRequestId:string|undefined,requestId:string):string=>
 workflow==='planning'&&workflowRequestId===requestId&&['recommend','purchase'].includes(kind)?'planning':kind;

// View saves update the card version, while a new Agent instruction updates
// request_id. Reapply only the latter to preserve the user's live form state.
export const agentCommandKey=(taskId:string|undefined,requestId:string|undefined):string=>`${taskId??'agent'}:${requestId??'initial'}`;

/** Same-request bank refreshes cannot roll back locally completed UI steps. */
export const reconcileCompletedSteps=(local:string[],saved:string[],reset:boolean):string[]=>
 reset?[...saved]:[...local];

export const stepLabels:Record<string,string>={match:'匹配条件',cash_management:'活钱候选',stable:'稳健候选',growth:'进取候选',candidates:'产品候选',selected:'已选产品',budget:'规划资金',amounts:'申购金额',purchase_review:'申购核对',allocation:'配置建议',details:'交易明细',redemption_input:'赎回输入',trade:'本人确认与验证'};

export function activeStep(order:string[],registered:string[],completed:string[]):string|undefined{
 return order.find(s=>registered.includes(s)&&!completed.includes(s));
}

export function affectedSteps(kind:string,stage:string):string[]{
 const order=stepOrder(kind);const index=order.indexOf(stage);if(index<0)return [];
 return ['cash_management','stable','growth'].includes(stage)?[stage,...order.slice(order.indexOf('selected'))]:order.slice(index);
}
