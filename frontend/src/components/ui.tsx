import type {ReactNode} from 'react';
import type {Device,HealthEvent,Overview,SourceType} from '../api/client';
import {label,relativeTime,utcTime} from '../lib/format';

export type Page='Overview'|'History'|'Models'|'Events';

const paths:Record<string,ReactNode>={
  overview:<><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></>,
  history:<><path d="M3 3v18h18"/><path d="m7 16 4-5 4 3 5-7"/></>,
  models:<><path d="M8 2v3M16 2v3M8 19v3M16 19v3M2 8h3M2 16h3M19 8h3M19 16h3"/><rect x="5" y="5" width="14" height="14" rx="2"/><circle cx="12" cy="12" r="3"/></>,
  events:<><path d="M6 3h12v18H6z"/><path d="M9 8h6M9 12h6M9 16h4"/></>,
  pulse:<><path d="M3 12h4l2-6 4 12 2-6h6"/></>,
  gauge:<><path d="M4 15a8 8 0 1 1 16 0"/><path d="m12 13 4-4"/></>,
  frequency:<><path d="M3 12c3-8 5 8 9 0s6 0 9 0"/></>,
  rotate:<><path d="M20 11a8 8 0 1 0-2 5.5"/><path d="m20 4v7h-7"/></>,
  temperature:<><path d="M10 14.5V5a2 2 0 0 1 4 0v9.5a4 4 0 1 1-4 0Z"/></>,
  humidity:<><path d="M12 3s6 6 6 11a6 6 0 0 1-12 0c0-5 6-11 6-11Z"/></>,
  menu:<><path d="M4 7h16M4 12h16M4 17h16"/></>,
  chevron:<path d="m9 18 6-6-6-6"/>,
};

export function Icon({name,size=18}:{name:string;size?:number}){return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>}

export function SourceBadge({value}:{value:SourceType}){return <span className={`badge source-badge ${value.toLowerCase()}`}><span className="badge-dot"/>{value}</span>}

export function StatusBadge({value,tone}:{value:string;tone?:string}){return <span className={`badge status-badge ${tone??value.toLowerCase()}`}>{label(value)}</span>}

export function EmptyState({title,detail}:{title:string;detail?:string}){return <div className="state-panel empty-state"><span className="state-symbol">—</span><strong>{title}</strong>{detail&&<p>{detail}</p>}</div>}
export function LoadingState({label='Loading current data…'}:{label?:string}){return <div className="state-panel loading-state" role="status"><span className="loading-mark"/><span>{label}</span></div>}
export function ErrorState({message,onRetry}:{message:string;onRetry?:()=>void}){return <div role="alert" className="state-panel error-state"><div><strong>Backend data unavailable</strong><p>{message}</p></div>{onRetry&&<button className="button secondary" onClick={onRetry}>Retry connection</button>}</div>}

export function PageHeader({eyebrow,title,subtitle,actions}:{eyebrow?:string;title:string;subtitle:string;actions?:ReactNode}){return <div className="page-header"><div>{eyebrow&&<span className="eyebrow">{eyebrow}</span>}<h1>{title}</h1><p>{subtitle}</p></div>{actions&&<div className="page-actions">{actions}</div>}</div>}

export function Panel({title,kicker,action,children,className=''}:{title?:string;kicker?:string;action?:ReactNode;children:ReactNode;className?:string}){return <section className={`panel ${className}`}><header className="panel-header"><div>{kicker&&<span>{kicker}</span>}{title&&<h2>{title}</h2>}</div>{action}</header><div className="panel-body">{children}</div></section>}

export function MetricCard({label:title,value,unit,icon,meta}:{label:string;value:string;unit:string;icon:string;meta:string}){return <article className="metric-card"><div className="metric-icon"><Icon name={icon}/></div><div className="metric-label">{title}</div><div className="metric-value">{value}<span>{value==='—'?'':unit}</span></div><div className="metric-meta">{meta}</div></article>}

export function HealthStatusPanel({overview}:{overview:Overview}){
  const modelUnavailable=overview.processing_status.startsWith('MODEL_')||(!overview.latest_prediction&&!overview.model_version);
  const health=modelUnavailable?'MODEL UNAVAILABLE':overview.latest_prediction?(overview.current_health_state??'UNKNOWN_CONDITION'):'NOT ANALYZED';
  const explanation:Record<string,string>={NORMAL:'Signals are within the current model and reference limits.',WARNING:'Anomaly evidence has crossed a monitored warning condition.',DEGRADING:'Recent evidence indicates a degrading machine condition.',MAINTENANCE:'The current condition requires maintenance attention.',SENSOR_ERROR:'Input quality prevents a reliable condition assessment.',UNKNOWN_CONDITION:'The available evidence cannot be classified reliably.','NOT ANALYZED':'Telemetry is present, but no scored observation is available.','MODEL UNAVAILABLE':'Telemetry is present, but no compatible active model can score it.'};
  return <section className={`health-panel health-${health.toLowerCase().replaceAll(' ','-')}`}>
    <div className="health-mark"><span/><span/><span/></div>
    <div className="health-primary"><span className="eyebrow">Machine condition</span><strong>{health}</strong><p>{explanation[health]}</p></div>
    <dl className="health-meta"><div><dt>Last scored observation</dt><dd>{overview.latest_feature?relativeTime(overview.latest_feature.window_end_us):'—'}</dd></div><div><dt>Active model</dt><dd>{overview.model_version??'Unavailable'}</dd></div><div><dt>Processing</dt><dd>{label(overview.processing_status)}</dd></div></dl>
  </section>
}

export function DeviceSelector({devices,value,onChange,id='device-selector'}:{devices:Device[];value:string;onChange:(value:string)=>void;id?:string}){return <label className="field" htmlFor={id}><span>Device</span><select id={id} value={value} onChange={event=>onChange(event.target.value)}><option value="">Select device</option>{devices.map(item=><option value={item.device_id} key={item.device_id}>{item.device_id}</option>)}</select></label>}

export function EventTable({events,onAcknowledge,compact=false,acknowledging}:{events:HealthEvent[];onAcknowledge:(id:number)=>void;compact?:boolean;acknowledging?:number|null}){
  if(!events.length)return <EmptyState title="No maintenance events recorded for this selection." detail="Persisted condition and processing events will appear here."/>;
  return <div className="table-wrap"><table className="data-table"><thead><tr><th>Event timestamp</th><th>Event</th><th>Severity</th>{!compact&&<th>Machine state</th>}{!compact&&<th>Device / source</th>}<th>Status</th></tr></thead><tbody>{events.map(event=><tr key={event.event_id}><td><time dateTime={event.detected_at_utc}>{utcTime(event.detected_at_utc)}</time><small>{relativeTime(event.window_start_us)}</small></td><td><strong>{label(event.event_type)}</strong>{!compact&&<small>{event.model_version??'No model version'}</small>}</td><td><StatusBadge value={event.severity} tone={event.severity.toLowerCase()}/></td>{!compact&&<td>{label(event.health_state)}</td>}{!compact&&<td>{event.device_id}<small><SourceBadge value={event.source_type}/></small></td>}<td>{event.acknowledged?<span className="acknowledged">Acknowledged</span>:<button className="button table-button" disabled={acknowledging===event.event_id} onClick={()=>onAcknowledge(event.event_id)}>{acknowledging===event.event_id?'Saving…':'Acknowledge'}</button>}</td></tr>)}</tbody></table></div>
}

export function Sidebar({page,onNavigate,collapsed,onToggle,mobileOpen,onClose}:{page:Page;onNavigate:(page:Page)=>void;collapsed:boolean;onToggle:()=>void;mobileOpen:boolean;onClose:()=>void}){
  const navigation:Array<{page:Page;label:string;icon:string}>=[{page:'Overview',label:'Overview',icon:'overview'},{page:'History',label:'Historical Analytics',icon:'history'},{page:'Models',label:'ML & Self-Learning',icon:'models'},{page:'Events',label:'Maintenance Events',icon:'events'}];
  return <><aside className={`sidebar ${collapsed?'collapsed':''} ${mobileOpen?'mobile-open':''}`} aria-label="Primary navigation"><div className="brand"><div className="brand-mark">PM</div><div className="brand-copy"><strong>Predictive<br/>Maintenance</strong><span>Industrial condition intelligence</span></div></div><nav>{navigation.map(item=><button key={item.page} className={page===item.page?'active':''} aria-label={item.page} aria-current={page===item.page?'page':undefined} onClick={()=>{onNavigate(item.page);onClose()}} title={collapsed?item.label:undefined}><Icon name={item.icon}/><span>{item.label}</span></button>)}</nav><button className="collapse-button" onClick={onToggle} aria-label={collapsed?'Expand sidebar':'Collapse sidebar'}><Icon name="chevron"/><span>{collapsed?'Expand':'Collapse sidebar'}</span></button></aside>{mobileOpen&&<button className="sidebar-backdrop" aria-label="Close navigation" onClick={onClose}/>}</>
}
