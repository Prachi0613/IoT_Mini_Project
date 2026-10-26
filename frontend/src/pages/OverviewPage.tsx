import type {HealthEvent,History,Overview} from '../api/client';
import {AnomalyChart,VibrationChart} from '../components/charts';
import {EventTable,HealthStatusPanel,MetricCard,PageHeader,Panel,SourceBadge,StatusBadge} from '../components/ui';
import {isStale,label,number,relativeTime,utcTime} from '../lib/format';

interface Props {overview:Overview;history:History|null;events:HealthEvent[];onAcknowledge:(id:number)=>void;acknowledging:number|null;onViewEvents:()=>void}

export default function OverviewPage({overview,history,events,onAcknowledge,acknowledging,onViewEvents}:Props){
  const feature=overview.latest_feature, environment=overview.latest_environment;
  const envStale=isStale(environment?.received_at_utc);
  const validWindows=history?.points.filter(point=>point.window_quality==='VALID').length;
  return <div className="page-stack">
    <PageHeader eyebrow="Monitoring overview" title={overview.device_id} subtitle="Real-time machine-condition intelligence" actions={<div className="header-facts"><div><span>Latest backend receipt</span><strong>{utcTime(overview.last_received_at_utc)}</strong></div><SourceBadge value={overview.source_type}/></div>}/>
    <HealthStatusPanel overview={overview}/>
    <section className="metrics-grid" aria-label="Latest sensor measurements">
      <MetricCard label="Vibration RMS" value={number(feature?.vibration_rms_g,3)} unit="g" icon="pulse" meta={feature?relativeTime(feature.window_end_us):'Unavailable'}/>
      <MetricCard label="Peak acceleration" value={number(feature?.vibration_peak_g,3)} unit="g" icon="gauge" meta={feature?relativeTime(feature.window_end_us):'Unavailable'}/>
      <MetricCard label="Dominant frequency" value={number(feature?.dominant_frequency_hz,1)} unit="Hz" icon="frequency" meta={feature?relativeTime(feature.window_end_us):'Unavailable'}/>
      <MetricCard label="Gyroscope RMS" value={number(feature?.gyro_rms_dps,2)} unit="°/s" icon="rotate" meta={feature?relativeTime(feature.window_end_us):'Unavailable'}/>
      <MetricCard label="Ambient temperature" value={number(environment?.ambient_temperature_c,1)} unit="°C" icon="temperature" meta={environment?relativeTime(environment.t_mono_us):'Unavailable'}/>
      <MetricCard label="Ambient humidity" value={number(environment?.ambient_humidity_pct,1)} unit="%" icon="humidity" meta={environment?relativeTime(environment.t_mono_us):'Unavailable'}/>
    </section>
    <div className="analytics-grid">
      <Panel title="Vibration trend" kicker="Dynamic vibration RMS · g" className="chart-panel wide"><VibrationChart points={history?.points??[]}/></Panel>
      <Panel title="Anomaly detection" kicker="Isolation Forest score"><AnomalyChart points={history?.points??[]}/></Panel>
    </div>
    <div className="detail-grid">
      <Panel title="Machine condition details" kicker="Analysis provenance"><dl className="detail-list"><div><dt>Model version</dt><dd>{overview.model_version??'Unavailable'}</dd></div><div><dt>Baseline reference</dt><dd>{overview.reference_version==null?'—':`v${overview.reference_version}`}</dd></div><div><dt>Valid recent windows</dt><dd>{validWindows==null?'—':validWindows}</dd></div><div><dt>Current health</dt><dd><StatusBadge value={overview.current_health_state??'NOT ANALYZED'}/></dd></div><div><dt>Processing status</dt><dd>{label(overview.processing_status)}</dd></div><div><dt>Source provenance</dt><dd><SourceBadge value={overview.source_type}/></dd></div></dl></Panel>
      <Panel title="Environmental monitoring" kicker="Ambient DHT11 observations" action={environment&&<StatusBadge value={envStale?'STALE':'FRESH'} tone={envStale?'warning':'normal'}/>}><div className="environment-readings"><div><span>Ambient temperature</span><strong>{number(environment?.ambient_temperature_c,1)}<small>{environment?.ambient_temperature_c==null?'':' °C'}</small></strong></div><div><span>Relative humidity</span><strong>{number(environment?.ambient_humidity_pct,1)}<small>{environment?.ambient_humidity_pct==null?'':' %'}</small></strong></div></div><dl className="detail-list compact"><div><dt>Device-relative sample</dt><dd>{relativeTime(environment?.t_mono_us)}</dd></div><div><dt>Backend receipt</dt><dd>{utcTime(environment?.received_at_utc)}</dd></div><div><dt>Quality</dt><dd>{environment?.valid?'Valid':environment?'Invalid':'Unavailable'}</dd></div></dl>{envStale&&<p className="notice warning">Stale: the latest environmental backend receipt is older than 10 seconds.</p>}</Panel>
    </div>
    <Panel title="Recent maintenance events" kicker={`${overview.active_event_count} active event${overview.active_event_count===1?'':'s'}`} action={<button className="text-button" onClick={onViewEvents}>View all events <span aria-hidden="true">→</span></button>}><EventTable events={events.slice(0,5)} onAcknowledge={onAcknowledge} acknowledging={acknowledging} compact/></Panel>
  </div>
}
