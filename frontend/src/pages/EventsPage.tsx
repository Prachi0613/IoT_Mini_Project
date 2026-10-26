import type {HealthEvent} from '../api/client';
import {EventTable,PageHeader} from '../components/ui';

interface Props {events:HealthEvent[];severity:string;acknowledgement:string;onSeverityChange:(value:string)=>void;onAcknowledgementChange:(value:string)=>void;onAcknowledge:(id:number)=>void;acknowledging:number|null;total:number;loading:boolean}

export default function EventsPage({events,severity,acknowledgement,onSeverityChange,onAcknowledgementChange,onAcknowledge,acknowledging,total,loading}:Props){return <div className="page-stack"><PageHeader eyebrow="Operations log" title="Maintenance Events" subtitle="Detected anomalies, system warnings and maintenance observations"/>
  <section className="control-bar event-controls"><label className="field"><span>Severity</span><select value={severity} onChange={event=>onSeverityChange(event.target.value)}><option value="">All severities</option><option value="INFO">Info</option><option value="WARNING">Warning</option><option value="CRITICAL">Critical</option></select></label><label className="field"><span>Acknowledgement</span><select value={acknowledgement} onChange={event=>onAcknowledgementChange(event.target.value)}><option value="all">All events</option><option value="open">Open</option><option value="acknowledged">Acknowledged</option></select></label><div className="result-count"><span>Persisted results</span><strong>{loading?'—':total}</strong></div></section>
  <section className="panel"><header className="panel-header"><div><span>Current device selection</span><h2>Event register</h2></div><span className="read-only">Real API data</span></header><div className="panel-body flush"><EventTable events={events} onAcknowledge={onAcknowledge} acknowledging={acknowledging}/></div></section>
  <p className="data-note">Events describe observed processing and condition transitions only. They do not infer a component fault or time-to-failure.</p>
 </div>}
