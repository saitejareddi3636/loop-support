import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export type ToolRun = { id: number; name: string; status: 'running' | 'done' | 'failed' | 'blocked'; result?: unknown; startedAt: number; durationMs?: number; traceId?: string; toolId?: string };

export type TraceEvent = { type: string; name?: string; result?: unknown; duration_ms?: number; trace_id?: string; tool_id?: string; message?: string; category?: string; usage?: { input_tokens?: number; output_tokens?: number }; estimated_cost_usd?: number | null; messages?: unknown; scope?: string };
type Evidence = { decision: string; verification_reason: string; threshold: number; applicable_tier?: string; top_similarity?: number; active_policy_versions?: Record<string, number> };

export type PendingRefund = { status: 'confirmation_required'; token: string; invoice_id: string; amount_cents: number; currency: string };
export type Receipt = { receipt_id: string; invoice_id: string; amount_cents: number; currency: string };

export const toolNames: Record<string, string> = {
  verify_customer: 'Identity verified',
  get_account: 'Account retrieved',
  get_invoice: 'Invoice retrieved',
  list_invoices: 'Invoices and eligibility checked',
  search_policy: 'Policy searched',
  issue_mock_refund: 'Refund eligibility checked',
  create_escalation_ticket: 'Support ticket created',
};

export function startTool(current: ToolRun[], name: string, event?: { trace_id?: string; tool_id?: string }): ToolRun[] {
  return [...current, { id: Date.now() + Math.random(), name, status: 'running', startedAt: performance.now(), traceId: event?.trace_id, toolId: event?.tool_id }];
}

export function isExistingRefund(result: unknown): boolean {
  if (!result || typeof result !== 'object' || !('receipt_id' in result) || typeof result.receipt_id !== 'string') return false;
  // Older persisted traces represented the same business outcome as an error.
  return ('status' in result && result.status === 'already_refunded') ||
    ('error' in result && result.error === 'This invoice already has a mock refund receipt.');
}

function toolCopy(tool: ToolRun): { title: string; detail: string } {
  if (tool.name === 'issue_mock_refund' && isExistingRefund(tool.result)) return { title: 'Refund already recorded', detail: 'Already refunded · Existing receipt found' };
  if (tool.status === 'blocked') return { title: tool.name === 'issue_mock_refund' ? 'Refund check needs attention' : 'More information needed', detail: tool.result && typeof tool.result === 'object' && 'error' in tool.result ? String(tool.result.error) : 'Please clarify your request' };
  if (tool.status === 'failed') return { title: `${tool.name.replaceAll('_', ' ')} failed`, detail: 'Failed' };
  const pending = tool.name === 'issue_mock_refund' && tool.result && typeof tool.result === 'object' && 'status' in tool.result && tool.result.status === 'confirmation_required';
  return { title: toolNames[tool.name] || tool.name.replaceAll('_', ' '), detail: tool.status === 'running' ? 'In progress' : pending ? 'Awaiting your confirmation' : 'Completed' };
}

export function finishTool(current: ToolRun[], name: string, result: unknown, event?: { type?: string; duration_ms?: number; tool_id?: string }): ToolRun[] {
  const index = current.findIndex(item => item.name === name && item.status === 'running' && (!event?.tool_id || item.toolId === event.tool_id));
  if (index < 0) return current;
  return current.map((item, position) => position === index ? {
    ...item, status: event?.type === 'tool_blocked' ? 'blocked' : name === 'issue_mock_refund' && isExistingRefund(result) ? 'done' : event?.type === 'tool_failed' || (result && typeof result === 'object' && 'error' in result) ? 'failed' : 'done', result, durationMs: event?.duration_ms ?? Math.round(performance.now() - item.startedAt),
  } : item);
}

export function restoreTools(events: TraceEvent[]): ToolRun[] {
  let runs: ToolRun[] = [];
  for (const event of events) {
    if (event.type === 'grounding') { const index = runs.map(item => item.name).lastIndexOf('search_policy'); if (index >= 0) runs = runs.map((run, position) => position === index ? { ...run, result: event.result } : run); }
    if (!event.name) continue;
    if (event.type === 'tool_started') runs = startTool(runs, event.name, event);
    if (event.type === 'tool_completed' || event.type === 'tool_failed' || event.type === 'tool_blocked') runs = finishTool(runs, event.name, event.result, event);
  }
  return runs;
}

export function Markdown({ children, className = '' }: { children: string; className?: string }) {
  return <div className={`markdown ${className}`}><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{children}</ReactMarkdown></div>;
}

function sourcesFrom(tools: ToolRun[]) {
  return tools.flatMap(tool => {
    if (tool.name !== 'search_policy' || !tool.result || typeof tool.result !== 'object' || !('sources' in tool.result)) return [];
    const sources = (tool.result as { sources?: unknown }).sources;
    if (!Array.isArray(sources)) return [];
    return sources.filter((source): source is { citation: string; similarity: number; title?: string; version?: number; document_id?: string; section?: string; excerpt?: string } =>
      !!source && typeof source === 'object' && typeof source.citation === 'string' && typeof source.similarity === 'number');
  });
}

export function ActivityPanel({ tools, compact = false, events = [] }: { tools: ToolRun[]; compact?: boolean; events?: TraceEvent[] }) {
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const sources = sourcesFrom(tools);
  const documents = sources.filter((source, index) => sources.findIndex(item => (item.document_id || item.citation) === (source.document_id || source.citation) && item.version === source.version) === index);
  return <section className={`activity-panel ${compact ? 'compact' : ''}`} aria-label="Agent activity">
    <div className="section-heading"><div><span className="section-kicker">Live intelligence</span><h2>Agent activity</h2></div><span className="activity-count">{tools.length ? `${tools.length} actions` : 'Standing by'}</span></div>
    {tools.length ? <ol className="activity-timeline" aria-live="polite">{tools.map(tool => <li key={tool.id} className={tool.status}>
      <span className="timeline-mark" aria-hidden="true">{tool.status === 'blocked' ? '–' : tool.status === 'failed' ? '!' : tool.status === 'done' ? '✓' : ''}</span>
      <div className="timeline-copy"><strong>{toolCopy(tool).title}</strong><small>{toolCopy(tool).detail}</small></div>
      {tool.durationMs !== undefined && <span className="timeline-time">{tool.durationMs < 1000 ? `${tool.durationMs}ms` : `${(tool.durationMs / 1000).toFixed(1)}s`}</span>}
    </li>)}</ol> : <div className="activity-empty"><span className="activity-empty-line" /><p>Actions will appear here as Loopp checks your request.</p></div>}
    {sources.length > 0 && <div className="source-block"><span className="source-label">Policy sources</span><div className="source-list">{documents.map((source, index) => <span className="source-chip" key={`${source.citation}-${index}`} title={`${source.section || source.title || 'Policy'} · ${source.citation} · similarity ${source.similarity}`}><span className="source-dot" />{source.title || source.document_id || source.citation}{source.version ? ` · v${source.version}` : ''}</span>)}</div></div>}
    {tools.some(tool => tool.name === 'search_policy' && tool.status === 'done') && sources.length === 0 && <p className="source-none">No reliable policy match found.</p>}
    {tools.filter(tool => tool.name === 'search_policy' && tool.result && typeof tool.result === 'object' && 'decision' in tool.result).map(tool => { const evidence = tool.result as Evidence; return <div className={`evidence-state ${evidence.decision.toLowerCase()}`} key={`evidence-${tool.id}`}><strong>{evidence.decision.replaceAll('_', ' ')}</strong><p>{evidence.verification_reason}</p><small>Threshold {evidence.threshold} · Tier {evidence.applicable_tier || 'General'} · Top similarity {evidence.top_similarity ?? 'unavailable'}</small><p>{Object.entries(evidence.active_policy_versions || {}).map(([doc, version]) => `${doc} v${version}`).join(' · ')}</p></div>; })}
    {tools.filter(tool => tool.name === 'create_escalation_ticket' && tool.status === 'done').map(tool => { const ticket = tool.result as { ticket_id?: string; status?: string }; return <p className="ticket-state" key={tool.id}>Ticket {ticket.ticket_id} · {ticket.status}</p>; })}
    {events.filter(event => event.type === 'escalation_offered').slice(-1).map((event, index) => <p className="source-none" key={`offer-${index}`}>{event.message}</p>)}
    <button type="button" className="inspector-toggle" aria-expanded={inspectorOpen} onClick={() => setInspectorOpen(!inspectorOpen)}><span>Technical inspector</span><span aria-hidden="true">{inspectorOpen ? '−' : '+'}</span></button>
    {inspectorOpen && <div className="inspector-content">
      <p className="inspector-note">Backend durations and provider-reported usage. Similarity measures retrieval relevance. Cost estimates cover text model calls at standard list prices; Live audio and embeddings are excluded.</p>
      {tools.length ? tools.map(tool => <div className="inspector-row" key={`inspect-${tool.id}`}><code>{tool.name}</code><span>{tool.durationMs !== undefined ? `${tool.durationMs} ms` : 'Running'}</span></div>) : <p className="inspector-muted">No tool events yet.</p>}
      {sources.map((source, index) => source.excerpt && <details className="source-evidence" key={`evidence-${source.citation}-${index}`}><summary>{source.title || 'Policy'} · v{source.version} · {source.section}</summary><code>{source.citation}</code><p>{source.excerpt}</p></details>)}
      {sources.map((source, index) => <div className="inspector-row" key={`similarity-${index}`}><code>{source.citation} similarity</code><span>{source.similarity.toFixed(3)}</span></div>)}
      {events.filter(event => ['model_completed', 'retry_scheduled', 'complete', 'error', 'escalation_offered'].includes(event.type)).map((event, index) => <div className="inspector-row" key={`event-${index}`}><code>{event.type}</code><span>{event.duration_ms !== undefined ? `${event.duration_ms} ms` : event.message || event.category || ''}{event.usage ? ` · ${event.usage.input_tokens ?? '?'} in / ${event.usage.output_tokens ?? '?'} out tokens` : ''}{event.type === 'model_completed' ? event.estimated_cost_usd != null ? ` · ~$${event.estimated_cost_usd.toFixed(6)}` : ' · cost unavailable' : ''}</span></div>)}
      <div className="inspector-row unavailable"><code>Gemini Live audio cost</code><span>Unavailable</span></div>
      <div className="inspector-row unavailable"><code>Embedding usage / cost</code><span>Unavailable</span></div>
      <p className="inspector-note">Text-only estimate. A complete session cost is unavailable; no missing component is treated as zero.</p>
      {events.filter(event => event.type === 'live_usage').slice(-1).map((event, index) => <details key={`live-${index}`}><summary>Provider-reported Live usage</summary><pre>{JSON.stringify(event.usage, null, 2)}</pre></details>)}
      {events.filter(event => event.type === 'prompt_trace').map((event, index) => <details key={`prompt-${index}`}><summary>Prompt trace · {event.trace_id?.slice(0, 10)}</summary><pre>{JSON.stringify(event.messages, null, 2)}</pre></details>)}
    </div>}
  </section>;
}

export function RefundCard({ pending, receipt, busy, onConfirm }: { pending: PendingRefund | null; receipt: Receipt | null; busy: boolean; onConfirm: () => void }) {
  if (!pending && !receipt) return null;
  if (receipt) return <div className="refund-card receipt-card"><div className="refund-icon">✓</div><div><span className="refund-overline">Mock receipt issued</span><h3>{receipt.invoice_id}</h3><p>{(receipt.amount_cents / 100).toFixed(2)} {receipt.currency} · No real money moved</p><code>{receipt.receipt_id}</code></div></div>;
  return <div className="refund-card"><div className="refund-card-top"><span className="refund-overline">Your confirmation is needed</span><span className="refund-state">Pending</span></div><h3>Confirm this refund</h3><div className="refund-amount"><span>{pending!.invoice_id}</span><strong>{(pending!.amount_cents / 100).toFixed(2)} <small>{pending!.currency}</small></strong></div><p>This is a mock refund. No real money will move.</p><button type="button" className="button-jade" disabled={busy} onClick={onConfirm}>{busy ? 'Confirming…' : 'Confirm invoice and amount'}</button></div>;
}
