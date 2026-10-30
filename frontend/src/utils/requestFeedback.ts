import {authenticationExpiredMessage,isSessionAuthenticationFailure} from './sessionAuthentication.ts';

/** Presentation only: never changes retry, authorization or execution state. */
export const unverifiedResult='结果尚未核实，请先核对原任务或操作记录，避免重复提交。';
export function requestFeedback(error:unknown):string{
 if(isSessionAuthenticationFailure(error))return authenticationExpiredMessage;
 if(error instanceof Error&&!('response' in error)&&!('request' in error)&&/[\u4e00-\u9fff]/.test(error.message))return error.message;
 const response=(error as {response?:{status?:number;data?:{detail?:string|{code?:string;message?:string}}}})?.response;
 const detail=response?.data?.detail;
 const code=typeof detail==='object'?detail?.code:undefined;
 const reason=typeof detail==='string'?detail:detail?.message;
 const reasons:Record<string,string>={
  TOTP_INVALID:'验证器验证码不正确，请输入当前有效验证码。',SMS_INVALID:'短信验证码不正确，请核对本次操作的短信。',
  TOTP_REPLAY:'这个验证码已经使用过，请等待新的验证码。',
  SECURITY_LOCKED:'当前操作受到安全限制，请按页面提示等待恢复。',
  AGENT_SESSION_LOCKED:'当前智能会话已暂停，请核对安全提示。',
  PROMPT_INJECTION_BLOCKED:'请求未通过安全校验，请使用正常业务指令；系统不会因此跳过本人确认或验证。',
 };
 if(code&&reasons[code])return `${reasons[code]} 当前请求未获通过，其他步骤请以原任务和操作记录为准。`;
 if(!response?.status||response.status>=500)return `请求处理或连接发生异常。${unverifiedResult}`;
 if(response.status===403)return `${reason&&/[\u4e00-\u9fff]/.test(reason)?reason:'当前请求无权办理或未满足安全条件。'} 请核对本人身份及原任务要求，不要尝试跳过授权。`;
 if(reason&&/[\u4e00-\u9fff]/.test(reason))return reason;
 if(response.status===409)return '任务或资料状态已变化，请先核对原任务及操作记录，再按当前状态继续。';
 if(response.status===422)return '本次输入未通过校验，请核对表单及页面提示。原任务结果以操作记录为准。';
 if(response.status===429)return '请求过于频繁，请按页面提示稍后继续；已提交的操作请先核对原任务。';
 return `请求未正常完成。${unverifiedResult}`;
}
