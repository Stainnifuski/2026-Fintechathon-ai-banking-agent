import {apiClient} from './api';

export interface VoiceStatus {ready:boolean;message:string;max_seconds:number;max_bytes:number}
export const getVoiceStatus = async (signal?:AbortSignal):Promise<VoiceStatus> =>
  (await apiClient.get('/nova/voice/status',{signal})).data;
export const transcribeVoice = async (audio:Blob,signal:AbortSignal):Promise<string> => {
  const result=await apiClient.post('/nova/voice/transcriptions',audio,{
    headers:{'Content-Type':audio.type||'audio/webm'},signal,timeout:85000,
  });
  return result.data.text;
};
