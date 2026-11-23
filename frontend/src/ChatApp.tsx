import { FormEvent, useEffect, useRef, useState } from 'react';
import { ActivityPanel, restoreTools, finishTool, Markdown, RefundCard, startTool, type PendingRefund, type Receipt, type ToolRun, type TraceEvent } from './Activity';

type Chat = { id: string; title: string };
type Message = { id: number; role: 'user' | 'assistant'; content: string };
type StreamEvent = { type: 'delta'; text: string } | { type: 'tool_started' | 'tool_completed' | 'tool_failed' | 'tool_blocked'; name: string; result?: unknown; tool_id?: string; trace_id?: string; duration_ms?: number } | { type: 'complete' } | { type: 'answer_reset' } | { type: 'grounding'; result: unknown } | { type: 'error'; message: string };

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export default function ChatApp() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [chatId, setChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [tools, setTools] = useState<ToolRun[]>([]);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [pendingRefund, setPendingRefund] = useState<PendingRefund | null>(null);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [refundReceipts, setRefundReceipts] = useState<Receipt[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const mobileActivityRef = useRef<HTMLDetailsElement>(null);
  const nextId = useRef(-1);

  useEffect(() => {
    void (async () => {
      try {
        const list = await request<Chat[]>('/api/chats');
        setChats(list);
        const preferred = localStorage.getItem('loopp-chat-id');
        if (preferred && list.some(chat => chat.id === preferred)) await openChat(preferred);
        else if (list[0]) await openChat(list[0].id);
      } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not load chats.'); }
      finally { setLoading(false); }
    })();
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, tools]);
  useEffect(() => { if (tools.length && mobileActivityRef.current) mobileActivityRef.current.open = true; }, [tools.length]);

  async function openChat(id: string) {
    if (busy) return;
    setBusy(true);
    try {
      const chat = await request<Chat & { messages: Message[]; refund_receipts?: Receipt[] }>(`/api/chats/${id}`);
      setChatId(chat.id);
      setMessages(chat.messages);
      setTools([]);
      setEvents([]);
      const traces = await request<{ events: TraceEvent[] }[]>(`/api/chats/${id}/traces`);
      if (traces[0]) { setEvents(traces[0].events); setTools(restoreTools(traces[0].events)); }
      setRefundReceipts(chat.refund_receipts || []);
      setReceipt(chat.refund_receipts?.at(-1) || null);
      try { setPendingRefund(await request<PendingRefund | null>(`/api/chats/${id}/refund-pending`)); }
      catch { setPendingRefund(null); }
      setError('');
      localStorage.setItem('loopp-chat-id', chat.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not open this conversation.'); }
    finally { setBusy(false); }
  }

  async function newChat() {
    if (busy || loading) return;
    setBusy(true);
    try {
      const chat = await request<Chat>('/api/chats', { method: 'POST' });
      setChats(current => [chat, ...current]);
      setChatId(chat.id);
      setMessages([]);
      setTools([]);
      setEvents([]);
      setPendingRefund(null);
      setReceipt(null);
      setRefundReceipts([]);
      setError('');
      localStorage.setItem('loopp-chat-id', chat.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not start a chat.'); }
    finally { setBusy(false); }
  }

  function handleEvent(event: StreamEvent) {
    if (event.type === 'delta') {
      setMessages(current => {
        const last = current.at(-1);
        if (!last || last.role !== 'assistant') return current;
        return [...current.slice(0, -1), { ...last, content: last.content + event.text }];
      });
    } else if (event.type === 'tool_started') {
      setTools(current => startTool(current, event.name, event));
    } else if (event.type === 'tool_completed' || event.type === 'tool_failed' || event.type === 'tool_blocked') {
      if (event.name === 'issue_mock_refund' && event.result && typeof event.result === 'object' && 'status' in event.result && event.result.status === 'confirmation_required') {
        setPendingRefund(event.result as PendingRefund);
        setReceipt(null);
      }
      setTools(current => finishTool(current, event.name, event.result, event));
    } else if (event.type === 'grounding') {
      setTools(current => current.map((tool, index) => index === current.map(item => item.name).lastIndexOf('search_policy') ? { ...tool, result: event.result } : tool));
    } else if (event.type === 'error') {
      setError(event.message);
      setMessages(current => current.at(-1)?.role === 'assistant' && !current.at(-1)?.content ? current.slice(0, -1) : current);
    }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    setTools([]);
    setEvents([]);
    if (!text || busy || loading) return;
    setBusy(true);
    setError('');
    setTools([]);
      setEvents([]);
    setPendingRefund(null);
    setReceipt(null);
    let id = chatId;
    try {
      if (!id) {
        const chat = await request<Chat>('/api/chats', { method: 'POST' });
        id = chat.id;
        setChatId(id);
        setChats(current => [chat, ...current]);
        localStorage.setItem('loopp-chat-id', id);
      }
      setInput('');
      setMessages(current => [...current, { id: nextId.current--, role: 'user', content: text }, { id: nextId.current--, role: 'assistant', content: '' }]);
      const response = await fetch(`/api/chats/${id}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text, request_id: crypto.randomUUID() }) });
      if (!response.ok || !response.body) throw new Error((await response.json().catch(() => ({}))).detail || `Chat failed (${response.status})`);
      const reader = response.body.getReader();
      let streamFinished = false;
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() || '';
        for (const frame of frames) {
          const line = frame.split('\n').find(part => part.startsWith('data: '));
          if (line) { const parsed = JSON.parse(line.slice(6)); if (parsed.type === 'complete' || parsed.type === 'error') streamFinished = true; setEvents(current => [...current, parsed as TraceEvent]); if (parsed.type === 'answer_reset') setMessages(current => current.map((item, index) => index === current.length - 1 ? { ...item, content: '' } : item)); else handleEvent(parsed as StreamEvent); }
        }
        if (done) break;
      }
      if (!streamFinished) throw new Error('The response disconnected before completion. Your saved history can be reopened; no action was replayed.');
      setChats(current => current.map(chat => chat.id === id && chat.title === 'New chat' ? { ...chat, title: text.slice(0, 55) } : chat));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The chat disconnected.');
      setMessages(current => current.at(-1)?.role === 'assistant' && !current.at(-1)?.content ? current.slice(0, -1) : current);
    } finally { setBusy(false); }
  }

  async function confirmRefund() {
    if (!chatId || !pendingRefund || busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await request<{ message?: string; receipt_id?: string; invoice_id?: string; amount_cents?: number; currency?: string }>(`/api/chats/${chatId}/refund-confirmations`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: pendingRefund.token, invoice_id: pendingRefund.invoice_id, amount_cents: pendingRefund.amount_cents }),
      });
      if (result.message) setMessages(current => [...current, { id: nextId.current--, role: 'assistant', content: result.message! }]);
      if (result.receipt_id && result.invoice_id && result.amount_cents !== undefined) {
        const issued = { receipt_id: result.receipt_id, invoice_id: result.invoice_id, amount_cents: result.amount_cents, currency: result.currency || 'USD' };
        setReceipt(issued);
        setRefundReceipts(current => [...current.filter(item => item.receipt_id !== issued.receipt_id), issued]);
      }
      setPendingRefund(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not confirm the mock refund.'); }
    finally { setBusy(false); }
  }

  return <main className="chat-page">
    <aside className="chat-sidebar">
      <div className="sidebar-top"><div className="wordmark">loopp<span className="wordmark-dot">.</span><span className="wordmark-divider" /><span className="wordmark-product">signal</span></div><p>Thoughtful support, with the details in view.</p></div>
      <button type="button" className="new-chat" onClick={() => void newChat()} disabled={busy || loading}><span aria-hidden="true">＋</span> New conversation</button>
      <div className="sidebar-section-title">Conversations <span>{chats.length || ''}</span></div>
      <div className="chat-list" aria-label="Conversations">{loading ? <p className="sidebar-muted">Loading conversations…</p> : chats.length ? chats.map(chat => <button type="button" key={chat.id} className={chat.id === chatId ? 'active' : ''} onClick={() => void openChat(chat.id)} disabled={busy}><span className="chat-list-mark" />{chat.title}</button>) : <p className="sidebar-muted">Your conversations will appear here.</p>}</div>
      <div className="sidebar-bottom"><span className="sidebar-signal" />Voice and chat share your history</div>
    </aside>
    <div className="chat-workspace">
      <section className="chat-main" aria-label="Chat conversation">
        <header className="chat-header"><div><span className="section-kicker">Customer support</span><h2>{chatId && chats.find(chat => chat.id === chatId)?.title !== 'New chat' ? chats.find(chat => chat.id === chatId)?.title : 'New conversation'}</h2></div><span className={`chat-header-status ${error ? 'has-error' : ''}`}><span /> {error ? 'Connection issue' : loading ? 'Opening' : busy ? 'Working' : 'Ready to help'}</span></header>
        <div className="message-list">
          {loading && <div className="chat-loading"><span className="loading-line" /><span className="loading-line short" /><p>Opening your conversation…</p></div>}
          {!loading && messages.length === 0 && <div className="chat-welcome"><div className="welcome-emblem"><span>l</span></div><span className="section-kicker">Your conversation starts here</span><h1>What can I help you resolve?</h1><p>Ask about an invoice, your account, or a support policy. Loopp checks the details before answering.</p><div className="suggested-prompts"><button type="button" onClick={() => setInput('I’m alex.morgan@example.test. Tell me about invoice INV-1002.')}><span>Explore an invoice</span><span aria-hidden="true">↗</span></button><button type="button" onClick={() => setInput('What is the current refund policy?')}><span>Ask about a policy</span><span aria-hidden="true">↗</span></button></div></div>}
          {messages.map(message => <div key={message.id} className={`chat-message ${message.role}`}><div className="message-avatar">{message.role === 'user' ? 'Y' : 'l'}</div><div className="message-body"><span className="message-author">{message.role === 'user' ? 'You' : 'Loopp'}</span>{message.content ? <Markdown>{message.content}</Markdown> : <div className="thinking-state" aria-label="Loopp is thinking"><span /><span /><span /></div>}</div></div>)}
          {(pendingRefund || receipt) && <RefundCard pending={pendingRefund} receipt={receipt} busy={busy} onConfirm={() => void confirmRefund()} />}
          <div ref={bottomRef} />
        </div>
        <details className="mobile-activity" ref={mobileActivityRef}><summary>Agent activity <span>{tools.length ? `${tools.length} actions · ${tools.at(-1)?.status === 'running' ? 'Working' : 'Checked'}` : 'Standing by'}</span></summary><ActivityPanel tools={tools} events={events} receipts={refundReceipts} compact /></details>
        {error && <div className="chat-error" role="alert"><span>{error}</span><button type="button" onClick={() => chatId ? void openChat(chatId) : window.location.reload()}>Retry</button></div>}
        <form className="chat-composer" onSubmit={send}><div className="composer-frame"><textarea aria-label="Message Loopp" value={input} onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Ask Loopp anything about your account…" disabled={busy || loading} rows={2} /><div className="composer-bottom"><span>Enter to send · Shift + Enter for a new line</span><button type="submit" disabled={busy || loading || !input.trim()} aria-label="Send message">{busy ? <span className="send-spinner" /> : <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6"/></svg>}</button></div></div><p className="composer-note">Fictional demo data · Mock financial actions only.</p></form>
      </section>
      <aside className="chat-rail"><ActivityPanel tools={tools} events={events} receipts={refundReceipts} /><div className="rail-note"><span className="rail-note-mark">✳</span><p>Loopp checks account records and active policies before it acts.</p></div></aside>
    </div>
  </main>;
}
