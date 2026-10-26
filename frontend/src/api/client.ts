export type SourceType = 'SIMULATED' | 'HARDWARE';
export interface Device { device_id: string; latest_boot_id: string; source_type: SourceType; last_received_at_utc: string; session_count: number }
export interface Feature { window_start_us: number; window_end_us: number; window_quality: 'VALID'|'INVALID'; quality_flags: string[]; sample_count: number; observed_sampling_hz: number|null; vibration_rms_g: number|null; vibration_peak_g: number|null; dominant_frequency_hz: number|null; gyro_rms_dps: number|null; ambient_temperature_c: number|null; ambient_humidity_pct: number|null }
export interface EnvironmentObservation { t_mono_us:number; ambient_temperature_c:number|null; ambient_humidity_pct:number|null; valid:number; error_code?:string|null; received_at_utc?:string }
export interface Overview { device_id:string; boot_id:string; source_type:SourceType; last_received_at_utc:string; latest_motion:Record<string,number|string>|null; latest_environment:EnvironmentObservation|null; latest_feature:Feature|null; latest_prediction:{isolation_score:number;isolation_threshold:number;health_state?:string;created_at_utc?:string}|null; current_health_state:string|null; processing_status:string; model_version:string|null; reference_version:number|null; active_event_count:number }
export interface Session { device_id:string; boot_id:string; source_type:SourceType; first_received_at_utc:string; last_received_at_utc:string; batch_count:number; motion_sample_count:number; environment_sample_count:number }
export interface HistoryPoint extends Feature { isolation_score:number|null; isolation_threshold:number|null; model_version:string|null; reference_version:number|null; health_state:string|null }
export interface History { device_id:string;boot_id:string;source_type:SourceType;points:HistoryPoint[];environment_observations:EnvironmentObservation[];decimation:string }
export interface HealthEvent { event_id:number;device_id:string;boot_id:string;source_type:SourceType;event_type:string;health_state:string;severity:string;window_start_us:number|null;window_end_us:number|null;model_version:string|null;baseline_version:number|null;detected_at_utc:string;acknowledged:number;acknowledged_at_utc:string|null;acknowledgement_note:string|null }
export interface ModelRecord { model_version:string;source_type:SourceType;feature_schema_version:string;status:string;created_at_utc:string;metadata:Record<string,unknown> }
export interface Baseline { model:ModelRecord; references:Array<Record<string,unknown>>; active_reference:Record<string,unknown>|null; adaptation_enabled:boolean; adaptation_summary:{approved:number;rejected:number}; adaptation_events:Array<Record<string,unknown>> }
export interface MonitorStatus { api_available:boolean; database_available:boolean; worker:Record<string,unknown>|null; last_checkpoint:Record<string,unknown>|null; sessions_without_checkpoint:number }
export interface EventFilters { device?:string; severity?:string; acknowledged?:boolean }

async function json<T>(path:string, init:RequestInit={}):Promise<T>{
  const response=await fetch(path,{...init,headers:{'Content-Type':'application/json',...(init.headers||{})}});
  if(!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json() as Promise<T>;
}
export const api={
  devices:(signal?:AbortSignal)=>json<{devices:Device[]}>('/api/v1/monitor/devices',{signal}),
  overview:(id:string,signal?:AbortSignal)=>json<Overview>(`/api/v1/monitor/devices/${encodeURIComponent(id)}/overview`,{signal}),
  sessions:(id:string,signal?:AbortSignal)=>json<{sessions:Session[]}>(`/api/v1/monitor/devices/${encodeURIComponent(id)}/sessions`,{signal}),
  history:(id:string,boot:string,signal?:AbortSignal,startUs?:number,endUs?:number)=>{const query=new URLSearchParams({boot_id:boot,limit:'500'});if(startUs!=null)query.set('start_us',String(startUs));if(endUs!=null)query.set('end_us',String(endUs));return json<History>(`/api/v1/monitor/devices/${encodeURIComponent(id)}/history?${query}`,{signal})},
  events:(filters:EventFilters={},signal?:AbortSignal)=>{const query=new URLSearchParams({limit:'200'});if(filters.device)query.set('device_id',filters.device);if(filters.severity)query.set('severity',filters.severity);if(filters.acknowledged!=null)query.set('acknowledged',String(filters.acknowledged));return json<{events:HealthEvent[];total:number}>(`/api/v1/monitor/events?${query}`,{signal})},
  models:(signal?:AbortSignal)=>json<{models:ModelRecord[]}>('/api/v1/monitor/models',{signal}),
  baseline:(version:string,signal?:AbortSignal)=>json<Baseline>(`/api/v1/monitor/models/${encodeURIComponent(version)}/baseline`,{signal}),
  status:(signal?:AbortSignal)=>json<MonitorStatus>('/api/v1/monitor/status',{signal}),
  acknowledge:(id:number,note:string)=>json<HealthEvent>(`/api/v1/monitor/events/${id}/acknowledge`,{method:'POST',body:JSON.stringify({note:note||null})})
};
