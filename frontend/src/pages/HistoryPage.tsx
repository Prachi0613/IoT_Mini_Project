import {useMemo,useState} from 'react';
import type {History,Session} from '../api/client';
import {AnomalyChart,EnvironmentChart,FrequencyChart,HealthTimeline,VibrationChart} from '../components/charts';
import {EmptyState,PageHeader,Panel,SourceBadge,StatusBadge} from '../components/ui';
import {number,relativeTime} from '../lib/format';

interface Props {history:History|null;sessions:Session[];boot:string;onBootChange:(value:string)=>void;loading:boolean}
type Range='all'|'60'|'300';

export default function HistoryPage({history,sessions,boot,onBootChange,loading}:Props){
  const [range,setRange]=useState<Range>('all');
  const view=useMemo(()=>{if(!history||range==='all')return history;const latest=Math.max(0,...history.points.map(p=>p.window_start_us),...history.environment_observations.map(p=>p.t_mono_us));const start=latest-Number(range)*1e6;return {...history,points:history.points.filter(p=>p.window_start_us>=start),environment_observations:history.environment_observations.filter(p=>p.t_mono_us>=start)}},[history,range]);
  const invalid=view?.points.filter(point=>point.window_quality==='INVALID').length??0;
  const sensorErrors=view?.points.filter(point=>point.health_state==='SENSOR_ERROR').length??0;
  const missingEnvironment=view?.environment_observations.filter(point=>!point.valid||point.ambient_temperature_c==null||point.ambient_humidity_pct==null).length??0;
  const observed=view?.points.map(point=>point.observed_sampling_hz).filter((value):value is number=>value!=null)??[];
  const meanHz=observed.length?observed.reduce((a,b)=>a+b,0)/observed.length:null;
  return <div className="page-stack"><PageHeader eyebrow="Engineering inspection" title="Historical Analytics" subtitle="Recorded sensor behavior and anomaly trends"/>
    <section className="control-bar"><label className="field"><span>Recording / session</span><select aria-label="Recorded session" value={boot} onChange={event=>onBootChange(event.target.value)}>{sessions.map(session=><option key={session.boot_id} value={session.boot_id}>{session.boot_id}</option>)}</select></label><label className="field"><span>Displayed time range</span><select value={range} onChange={event=>setRange(event.target.value as Range)}><option value="all">All loaded windows</option><option value="60">Latest 60 seconds</option><option value="300">Latest 5 minutes</option></select></label>{history&&<div className="control-source"><span>Source identification</span><SourceBadge value={history.source_type}/></div>}</section>
    {loading?<div className="state-panel loading-state">Loading recorded windows…</div>:!view?<EmptyState title="Select a recorded session."/>:<>
      <div className="quality-strip"><div><span>Feature windows</span><strong>{view.points.length}</strong></div><div><span>Invalid windows</span><strong>{invalid}</strong></div><div><span>Sensor-error periods</span><strong>{sensorErrors}</strong></div><div><span>Missing environment</span><strong>{missingEnvironment}</strong></div><div><span>Mean observed rate</span><strong>{number(meanHz,1)}{meanHz==null?'':' Hz'}</strong></div></div>
      <div className="history-grid"><Panel title="Vibration RMS history" kicker="Dynamic acceleration · g" className="chart-panel"><VibrationChart points={view.points}/></Panel><Panel title="Anomaly score vs threshold" kicker="Higher score means more anomalous" className="chart-panel"><AnomalyChart points={view.points}/></Panel><Panel title="Frequency and gyroscope" kicker="Separate Hz and °/s axes" className="chart-panel"><FrequencyChart points={view.points}/></Panel><Panel title="Ambient conditions" kicker="Temperature · humidity" className="chart-panel"><EnvironmentChart observations={view.environment_observations}/></Panel></div>
      <Panel title="Health-state timeline" kicker="Model decisions by session-relative time"><HealthTimeline points={view.points}/><div className="timeline-legend">{['NORMAL','WARNING','DEGRADING','MAINTENANCE','SENSOR_ERROR','UNKNOWN_CONDITION'].map(state=><span key={state}><i className={`state-${state.toLowerCase()}`}/>{state.replaceAll('_',' ')}</span>)}</div></Panel>
      <Panel title="Window quality inspection" kicker="Bounded stored data · no smoothing or interpolation"><div className="table-wrap"><table className="data-table"><thead><tr><th>Session time</th><th>Quality</th><th>Samples</th><th>Observed rate</th><th>Health state</th><th>Flags</th></tr></thead><tbody>{view.points.map(point=><tr key={point.window_start_us}><td>{relativeTime(point.window_start_us)}</td><td><StatusBadge value={point.window_quality} tone={point.window_quality==='VALID'?'normal':'warning'}/></td><td>{point.sample_count}</td><td>{number(point.observed_sampling_hz,1)}{point.observed_sampling_hz==null?'':' Hz'}</td><td>{point.health_state?.replaceAll('_',' ')??'Not analyzed'}</td><td>{point.quality_flags.length?point.quality_flags.join(', '):'—'}</td></tr>)}</tbody></table>{!view.points.length&&<EmptyState title="No feature windows in this displayed range."/>}</div><p className="data-note">Time axis is device monotonic session-relative time. Backend UTC receipt time is not converted into acquisition time. {view.decimation}.</p></Panel>
    </>}
  </div>
}
