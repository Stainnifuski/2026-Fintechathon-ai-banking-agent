import {useEffect,useRef,useState} from 'react';
import {getVoiceStatus,transcribeVoice,type VoiceStatus} from '../services/voice';
import {recordingMime,VOICE_MAX_BYTES,VOICE_MAX_SECONDS} from '../utils/voiceInput';
import './novaVoice.css';

type Phase='idle'|'permission'|'recording'|'processing';

/** Optional input accessory: never sends a message, confirms a card or calls MFA. */
export default function NovaVoiceInput({disabled,onText,onBusy}:{disabled:boolean;onText:(text:string)=>boolean;onBusy:(busy:boolean)=>void}){
  const [phase,setPhase]=useState<Phase>('idle');
  const [status,setStatus]=useState<VoiceStatus|null>(null);
  const [checking,setChecking]=useState(true),[notice,setNotice]=useState(''),[seconds,setSeconds]=useState(0);
  const live=useRef(true),generation=useRef(0),stream=useRef<MediaStream|null>(null),recorder=useRef<MediaRecorder|null>(null);
  const request=useRef<AbortController|null>(null),timer=useRef<ReturnType<typeof setInterval>|null>(null);
  const callbacks=useRef({onText,onBusy});callbacks.current={onText,onBusy};
  const supported=typeof window!=='undefined'&&window.isSecureContext&&!!navigator.mediaDevices?.getUserMedia&&typeof MediaRecorder!=='undefined';
  const release=()=>{
    if(timer.current){clearInterval(timer.current);timer.current=null;}
    stream.current?.getTracks().forEach(track=>track.stop());stream.current=null;
  };
  const changePhase=(next:Phase)=>{setPhase(next);callbacks.current.onBusy(next!=='idle');};
  const cancel=()=>{
    generation.current++;request.current?.abort();request.current=null;
    if(recorder.current?.state==='recording')recorder.current.stop();
    recorder.current=null;release();
    if(live.current){changePhase('idle');setNotice('已取消语音输入，未发送对话。');}
  };
  const check=async()=>{
    if(!supported){setChecking(false);return;}
    const controller=new AbortController();request.current=controller;setChecking(true);
    try{const value=await getVoiceStatus(controller.signal);if(live.current&&!controller.signal.aborted)setStatus(value);}
    catch{if(live.current&&!controller.signal.aborted)setStatus({ready:false,message:'语音服务暂不可用，请重试；仍可输入文字。',max_seconds:45,max_bytes:VOICE_MAX_BYTES});}
    finally{if(live.current&&!controller.signal.aborted)setChecking(false);}
  };
  useEffect(()=>{
    live.current=true;void check();
    return()=>{live.current=false;generation.current++;request.current?.abort();if(recorder.current?.state==='recording')recorder.current.stop();release();callbacks.current.onBusy(false);};
  },[]);
  useEffect(()=>{if(disabled&&phase!=='idle')cancel();},[disabled]);

  const start=async()=>{
    if(disabled||phase!=='idle'||!status?.ready)return;
    const id=++generation.current;setNotice('');setSeconds(0);changePhase('permission');
    try{
      const microphone=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
      if(!live.current||generation.current!==id){microphone.getTracks().forEach(t=>t.stop());return;}
      stream.current=microphone;
      const mime=recordingMime(t=>MediaRecorder.isTypeSupported(t));
      const device=new MediaRecorder(microphone,mime?{mimeType:mime}:undefined);recorder.current=device;
      const chunks:Blob[]=[];let size=0,tooLarge=false;const started=Date.now();
      device.ondataavailable=event=>{
        if(event.data.size){size+=event.data.size;if(size>VOICE_MAX_BYTES){tooLarge=true;if(device.state==='recording')device.stop();}else chunks.push(event.data);}
      };
      device.onerror=()=>{if(live.current&&generation.current===id){cancel();setNotice('录音失败，请检查麦克风后重试。');}};
      device.onstop=async()=>{
        release();recorder.current=null;
        if(!live.current||generation.current!==id)return;
        if(tooLarge||!size){changePhase('idle');setNotice(tooLarge?'录音超过4MB，请缩短录音。':'录音为空，请重新录音。');return;}
        changePhase('processing');setNotice('正在识别，结果将填入输入框，不会自动发送…');
        const controller=new AbortController();request.current=controller;
        try{
          const text=await transcribeVoice(new Blob(chunks,{type:device.mimeType||mime||'audio/webm'}),controller.signal);
          if(!live.current||generation.current!==id||controller.signal.aborted)return;
          setNotice(callbacks.current.onText(text)?'已填入文字，请检查产品名称、代码和金额后手动发送。':'识别结果与已有文字超过2000字，未覆盖输入框，请缩短录音。');
        }catch(error){
          if(live.current&&generation.current===id&&!controller.signal.aborted){
            const detail=(error as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail;
            setNotice(detail?.message||'识别失败，请检查网络后重试；未发送对话。');
          }
        }finally{if(live.current&&generation.current===id)changePhase('idle');}
      };
      device.start(250);changePhase('recording');
      timer.current=setInterval(()=>{
        const elapsed=Math.floor((Date.now()-started)/1000);setSeconds(elapsed);
        if(Date.now()-started>=(VOICE_MAX_SECONDS-0.5)*1000&&device.state==='recording')device.stop();
      },250);
    }catch(error){
      if(!live.current||generation.current!==id)return;
      release();changePhase('idle');
      setNotice((error as {name?:string}).name==='NotAllowedError'?'麦克风权限未允许，请在浏览器设置中允许后重试。':'无法打开麦克风，请检查设备是否可用或被其他应用占用。');
    }
  };
  const stop=()=>{if(recorder.current?.state==='recording')recorder.current.stop();};
  const label=phase==='recording'?'结束录音':phase==='processing'?'取消识别':phase==='permission'?'取消打开麦克风':'开始语音输入';
  const message=!supported?'当前浏览器不支持录音，请使用新版Chrome/Edge并通过localhost或HTTPS访问。':
    phase==='recording'?`正在录音 ${seconds}/${VOICE_MAX_SECONDS}秒，点击麦克风结束。`:notice||(!status?.ready?status?.message:'');
  return <div className="nova-voice-accessory">
    {!checking&&status&&!status.ready&&supported?<button type="button" className="nova-voice-button" disabled={disabled} onClick={()=>void check()} aria-label="重试语音服务" title={status.message}>重试语音</button>:
      <button type="button" className={`nova-voice-button ${phase==='recording'?'is-recording':''}`} disabled={disabled||checking||!supported||(!status?.ready&&phase==='idle')} aria-label={checking?'正在检查语音服务':label} aria-pressed={phase==='recording'} onClick={()=>{if(phase==='recording')stop();else if(phase!=='idle')cancel();else void start();}} title={checking?'正在检查语音服务':label}>
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/></svg>
      </button>}
    {message&&<span className="nova-voice-notice" role="status">{message}</span>}
  </div>;
}
