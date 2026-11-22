import { useEffect, useRef, useState } from 'react';
import { ActivityPanel, restoreTools, finishTool, Markdown, RefundCard, startTool, type PendingRefund, type Receipt, type ToolRun, type TraceEvent } from './Activity';
import SignalOrb, { type OrbState } from './SignalOrb';

type Line = { id: number; speaker: 'user' | 'assistant'; text: string };
type State = 'disconnected' | 'connecting' | 'ready';
type ServerEvent =
  | { type: 'ready'; model: string; chat_id: string }
  | { type: 'transcript'; speaker: 'user' | 'assistant'; text: string }
  | { type: 'interrupted' | 'turn_complete' }
  | { type: 'tool_started' | 'tool_completed' | 'tool_failed' | 'tool_blocked'; name: string; result?: unknown; tool_id?: string; trace_id?: string; duration_ms?: number }
  | { type: 'grounding'; result: unknown }
  | { type: 'turn_started'; call_id: string }
  | { type: 'agent_error'; message: string }
  | { type: 'agent_answer'; answer: string }
  | { type: 'notice'; message: string }
  | { type: 'error'; message: string };

export default function VoiceApp() {
  const [state, setState] = useState<State>('disconnected');
  const [muted, setMuted] = useState(false);
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [agentBusy, setAgentBusy] = useState(false);
  const [error, setError] = useState('');
  const [lines, setLines] = useState<Line[]>([]);
  const [model, setModel] = useState('');
  const [speaking, setSpeaking] = useState(false);
  const [tools, setTools] = useState<ToolRun[]>([]);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [pendingRefund, setPendingRefund] = useState<PendingRefund | null>(null);
  const [agentAnswer, setAgentAnswer] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [inputAnalyser, setInputAnalyser] = useState<AnalyserNode | null>(null);
  const [outputAnalyser, setOutputAnalyser] = useState<AnalyserNode | null>(null);
  const [interrupted, setInterrupted] = useState(false);
  const interruptTimerRef = useRef<number | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const micRef = useRef<MediaStream | null>(null);
  const inputContextRef = useRef<AudioContext | null>(null);
  const outputContextRef = useRef<AudioContext | null>(null);
  const outputMeterRef = useRef<AnalyserNode | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const sourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const nextPlayRef = useRef(0);
  const mutedRef = useRef(false);
  const activeSpeakerRef = useRef<Line['speaker'] | null>(null);
  const sequenceRef = useRef(0);
  const manualCloseRef = useRef(false);
  const connectionIdRef = useRef(0);
  const chatIdRef = useRef<string | null>(null);
  const orbState: OrbState = state === 'disconnected' ? 'disconnected' : state === 'connecting' ? 'connecting' : muted ? 'muted' : interrupted ? 'interrupted' : speaking ? 'speaking' : agentBusy || tools.some(tool => tool.status === 'running') ? 'processing' : 'listening';
  const stateCopy: Record<OrbState, { title: string; detail: string }> = {
    disconnected: { title: 'A conversation, when you’re ready.', detail: 'Fictional demo data. Connect to speak with Loopp.' },
    connecting: { title: 'Making the connection.', detail: 'Opening a secure voice session…' },
    listening: { title: 'I’m listening.', detail: 'Ask about an account, invoice, or policy.' },
    processing: { title: 'Looking into it.', detail: 'Checking the details behind your request.' },
    speaking: { title: 'Here’s what I found.', detail: 'You can interrupt at any time.' },
    interrupted: { title: 'I’m listening.', detail: 'Go ahead with your next question.' },
    muted: { title: 'Microphone muted.', detail: 'Unmute when you’re ready to continue.' },
  };

  useEffect(() => {
    const id = localStorage.getItem('loopp-chat-id');
    if (!id) return;
    chatIdRef.current = id;
    void fetch(`/api/chats/${id}`).then(async response => {
      if (!response.ok) return;
      const chat = await response.json() as { messages: { id: number; role: 'user' | 'assistant'; content: string }[] };
      setLines(chat.messages.map(message => ({ id: message.id, speaker: message.role, text: message.content })));
      sequenceRef.current = Math.max(0, ...chat.messages.map(message => message.id));
    }).catch(() => { /* Reconnect can still create a fresh chat. */ });
    void fetch(`/api/chats/${id}/traces`).then(async response => {
      if (response.ok) { const traces = await response.json() as { events: TraceEvent[] }[]; if (traces[0]) { setEvents(traces[0].events); setTools(restoreTools(traces[0].events)); } }
    }).catch(() => {});
    void fetch(`/api/chats/${id}/refund-pending`).then(async response => {
      if (response.ok) setPendingRefund(await response.json() as PendingRefund | null);
    }).catch(() => { /* A missing pending request does not block voice. */ });
  }, []);

  function clearPlayback() {
    for (const source of sourcesRef.current) {
      try { source.stop(); } catch { /* Already finished. */ }
    }
    sourcesRef.current.clear();
    nextPlayRef.current = 0;
    setSpeaking(false);
  }

  function playPcm(bytes: ArrayBuffer) {
    const context = outputContextRef.current;
    if (!context || bytes.byteLength < 2) return;
    const pcm = new Int16Array(bytes);
    const buffer = context.createBuffer(1, pcm.length, 24000);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(outputMeterRef.current || context.destination);
    const start = Math.max(context.currentTime + 0.04, nextPlayRef.current);
    source.start(start);
    nextPlayRef.current = start + buffer.duration;
    sourcesRef.current.add(source);
    setSpeaking(true);
    source.onended = () => {
      sourcesRef.current.delete(source);
      if (sourcesRef.current.size === 0) setSpeaking(false);
    };
  }

  function addTranscript(speaker: Line['speaker'], text: string) {
    if (!text) return;
    setLines(current => {
      const previous = current.at(-1);
      if (previous?.speaker === speaker && activeSpeakerRef.current === speaker) {
        return [...current.slice(0, -1), { ...previous, text: previous.text + text }];
      }
      activeSpeakerRef.current = speaker;
      return [...current, { id: ++sequenceRef.current, speaker, text }];
    });
  }

  async function cleanup() {
    clearPlayback();
    workletRef.current?.disconnect();
    workletRef.current = null;
    micRef.current?.getTracks().forEach(track => track.stop());
    micRef.current = null;
    const inputContext = inputContextRef.current;
    const outputContext = outputContextRef.current;
    inputContextRef.current = null;
    outputContextRef.current = null;
    outputMeterRef.current = null;
    activeSpeakerRef.current = null;
    setState('disconnected');
    setAgentBusy(false);
    setInputAnalyser(null);
    setOutputAnalyser(null);
    setInterrupted(false);
    if (interruptTimerRef.current) window.clearTimeout(interruptTimerRef.current);
    await Promise.allSettled([
      inputContext?.close(),
      outputContext?.close(),
    ]);
  }

  function disconnect() {
    connectionIdRef.current++;
    manualCloseRef.current = true;
    wsRef.current?.close();
    wsRef.current = null;
    void cleanup();
  }

  async function connect() {
    if (state !== 'disconnected') return;
    const connectionId = ++connectionIdRef.current;
    setError('');
    setMuted(false);
    mutedRef.current = false;
    manualCloseRef.current = false;
    setState('connecting');
    try {
      let chatId = chatIdRef.current || localStorage.getItem('loopp-chat-id');
      if (!chatId || !(await fetch(`/api/chats/${chatId}`)).ok) {
        const created = await fetch('/api/chats', { method: 'POST' });
        if (!created.ok) throw new Error('Could not create a shared chat.');
        chatId = ((await created.json()) as { id: string }).id;
      }
      chatIdRef.current = chatId;
      localStorage.setItem('loopp-chat-id', chatId);
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (connectionId !== connectionIdRef.current) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      micRef.current = stream;
      // The worklet resamples the device's actual rate to the API's 16 kHz input.
      const input = new AudioContext();
      inputContextRef.current = input;
      // Web Audio resamples the API's 24 kHz output for the user's device.
      const output = new AudioContext();
      outputContextRef.current = output;
      const outputMeter = output.createAnalyser();
      outputMeter.fftSize = 512;
      outputMeter.connect(output.destination);
      outputMeterRef.current = outputMeter;
      setOutputAnalyser(outputMeter);
      await Promise.all([input.resume(), output.resume(), input.audioWorklet.addModule('/pcm-worklet.js')]);
      if (connectionId !== connectionIdRef.current) return;
      const mic = input.createMediaStreamSource(stream);
      const inputMeter = input.createAnalyser();
      inputMeter.fftSize = 512;
      mic.connect(inputMeter);
      setInputAnalyser(inputMeter);
      const worklet = new AudioWorkletNode(input, 'pcm-worklet');
      workletRef.current = worklet;
      mic.connect(worklet);
      worklet.connect(input.destination);
      worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        const ws = wsRef.current;
        if (!mutedRef.current && ws?.readyState === WebSocket.OPEN && ws.bufferedAmount < 256_000) {
          ws.send(event.data);
        }
      };

      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${protocol}//${location.host}/api/voice?chat_id=${encodeURIComponent(chatId)}`);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;
      ws.onmessage = (event: MessageEvent) => {
        if (event.data instanceof ArrayBuffer) {
          playPcm(event.data);
          return;
        }
        const message = JSON.parse(event.data) as ServerEvent;
        if (message.type === 'turn_started') {
          setTools([]); setEvents([]); setAgentAnswer(''); setAgentBusy(true);
        } else if (!['transcript', 'turn_complete'].includes(message.type)) {
          setEvents(current => [...current.slice(-249), message as TraceEvent]);
        }
        if (message.type === 'ready') {
          setModel(message.model);
          chatIdRef.current = message.chat_id;
          localStorage.setItem('loopp-chat-id', message.chat_id);
          setState('ready');
        } else if (message.type === 'transcript') {
          addTranscript(message.speaker, message.text);
        } else if (message.type === 'interrupted') {
          clearPlayback();
          activeSpeakerRef.current = null;
          setInterrupted(true);
          if (interruptTimerRef.current) window.clearTimeout(interruptTimerRef.current);
          interruptTimerRef.current = window.setTimeout(() => setInterrupted(false), 1000);
        } else if (message.type === 'turn_complete') {
          activeSpeakerRef.current = null;
        } else if (message.type === 'tool_started') {
          setTools(current => startTool(current, message.name, message));
        } else if ((message.type === 'tool_completed' || message.type === 'tool_failed' || message.type === 'tool_blocked')) {
          if (message.name === 'issue_mock_refund' && message.result && typeof message.result === 'object' && 'status' in message.result && message.result.status === 'confirmation_required') {
            setPendingRefund(message.result as PendingRefund);
            setReceipt(null);
          }
          setTools(current => finishTool(current, message.name, message.result, message));
        } else if (message.type === 'grounding') {
          setTools(current => current.map((tool, index) => index === current.map(item => item.name).lastIndexOf('search_policy') ? { ...tool, result: message.result } : tool));
        } else if (message.type === 'agent_answer') {
          setAgentAnswer(message.answer);
          setAgentBusy(false);
        } else if (message.type === 'agent_error') {
          setError(message.message); setAgentBusy(false);
        } else if (message.type === 'notice') {
          setError(message.message);
        } else if (message.type === 'error') {
          setError(message.message);
          ws.close();
        }
      };
      ws.onerror = () => {
        if (wsRef.current === ws) {
          setError(current => current || 'Voice connection failed. Check the backend terminal.');
        }
      };
      ws.onclose = () => {
        if (wsRef.current !== ws) return;
        if (!manualCloseRef.current) setError(current => current || 'Voice disconnected. Reconnect to try again.');
        wsRef.current = null;
        void cleanup();
      };
    } catch (cause) {
      if (connectionId !== connectionIdRef.current) return;
      setError(cause instanceof Error ? cause.message : 'Could not start the microphone.');
      await cleanup();
    }
  }

  function toggleMute() {
    const next = !mutedRef.current;
    mutedRef.current = next;
    setMuted(next);
    if (next && wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'audio_stream_end' }));
    }
  }

  async function confirmRefund() {
    const chatId = chatIdRef.current;
    if (!chatId || !pendingRefund || confirming) return;
    setConfirming(true);
    setError('');
    try {
      const response = await fetch(`/api/chats/${chatId}/refund-confirmations`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: pendingRefund.token, invoice_id: pendingRefund.invoice_id, amount_cents: pendingRefund.amount_cents }),
      });
      const result = await response.json() as { detail?: string; message?: string; receipt_id?: string; invoice_id?: string; amount_cents?: number; currency?: string };
      if (!response.ok) throw new Error(result.detail || 'Could not confirm the mock refund.');
      if (result.message) {
        activeSpeakerRef.current = null;
        addTranscript('assistant', result.message);
        setAgentAnswer(result.message);
      }
      if (result.receipt_id && result.invoice_id && result.amount_cents !== undefined) setReceipt({ receipt_id: result.receipt_id, invoice_id: result.invoice_id, amount_cents: result.amount_cents, currency: result.currency || 'USD' });
      setPendingRefund(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not confirm the mock refund.'); }
    finally { setConfirming(false); }
  }

  useEffect(() => () => {
    connectionIdRef.current++;
    manualCloseRef.current = true;
    wsRef.current?.close();
    void cleanup();
  }, []);

  return <main className="voice-page">
    <header className="voice-header"><div className="wordmark">loopp<span className="wordmark-dot">.</span><span className="wordmark-divider" /><span className="wordmark-product">signal</span></div><span className="session-indicator"><span className={`session-light ${state === 'ready' ? 'on' : ''}`} />{state === 'ready' ? 'Live session' : state === 'connecting' ? 'Connecting' : 'Session offline'}</span></header>
    <div className={`voice-layout ${railCollapsed ? 'details-collapsed' : ''}`}>
      <section className="voice-stage" aria-label="Live voice conversation">
        <div className="stage-topline"><span>Speak freely</span><span>{model && state === 'ready' ? model : 'Voice support'}</span></div>
        <div className="orb-stage"><SignalOrb state={orbState} input={inputAnalyser} output={outputAnalyser} /><span className={`orb-signal ${orbState}`} aria-hidden="true" /></div>
        <div className="voice-stage-copy" role="status"><span className={`voice-state-label ${orbState}`}><span className="voice-state-dot" />{orbState === 'speaking' ? 'Speaking' : orbState === 'processing' ? 'Working' : orbState === 'muted' ? 'Muted' : orbState === 'interrupted' ? 'Interrupted' : orbState === 'listening' ? 'Listening' : orbState === 'connecting' ? 'Connecting' : 'Ready when you are'}</span><h1>{stateCopy[orbState].title}</h1><p>{stateCopy[orbState].detail}</p></div>
        <div className="voice-controls">
          {state === 'ready' && <button type="button" className={`control-button mute-control ${muted ? 'is-muted' : ''}`} onClick={toggleMute} aria-label={muted ? 'Unmute microphone' : 'Mute microphone'} aria-pressed={muted}><span className="control-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3m-4 0h8"/>{muted && <path d="M3 3l18 18"/>}</svg></span><span>{muted ? 'Unmute' : 'Mute'}</span></button>}
          {state === 'disconnected' ? <button type="button" className="connect-button" onClick={() => void connect()}><span className="connect-icon" aria-hidden="true">◉</span>Start voice conversation</button> : <button type="button" className="control-button end-control" onClick={disconnect} aria-label={state === 'connecting' ? 'Cancel connection' : 'End call'}><span className="control-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 5l14 14M19 5 5 19"/></svg></span><span>{state === 'connecting' ? 'Cancel' : 'End call'}</span></button>}
        </div>
        {error && <div className="voice-error" role="alert"><span>{error}</span>{state === 'disconnected' && <button type="button" onClick={() => void connect()}>Reconnect</button>}</div>}
        <p className="stage-footnote">Your voice and chat stay in the same conversation.</p>
      </section>
      <aside className={`voice-rail ${railCollapsed ? 'is-collapsed' : ''}`} aria-label="Conversation details">
        <div className="voice-rail-heading"><span>Conversation details</span><button type="button" aria-label={railCollapsed ? 'Show conversation details' : 'Minimize conversation details'} aria-expanded={!railCollapsed} aria-controls="voice-details-body" title={railCollapsed ? 'Show details' : 'Minimize details'} onClick={() => setRailCollapsed(value => !value)}>{railCollapsed ? '‹' : '›'}</button></div>
        <div className="voice-rail-scroll" id="voice-details-body" hidden={railCollapsed}>
        <section className="transcript-panel" aria-label="Live transcript"><div className="section-heading"><div><span className="section-kicker">Conversation</span><h2>Live transcript</h2></div><span className="transcript-count">{lines.length ? `${lines.length} messages` : 'Waiting'}</span></div>
          {lines.length === 0 ? <div className="transcript-empty"><span className="transcript-rule" /><p>Your words and Loopp’s responses will appear here.</p></div> : <div className="transcript-lines" aria-live="polite">{lines.map(line => <div key={line.id} className={`transcript-line ${line.speaker}`}><span className="transcript-speaker">{line.speaker === 'user' ? 'You' : 'Loopp'}</span><p>{line.text}</p></div>)}</div>}</section>
        <ActivityPanel tools={tools} events={events} compact />
        {(pendingRefund || receipt) && <RefundCard pending={pendingRefund} receipt={receipt} busy={confirming} onConfirm={() => void confirmRefund()} />}
        {agentAnswer && !lines.some(line => line.speaker === 'assistant' && line.text.includes(agentAnswer.slice(0, 20))) && <div className="agent-answer"><span className="section-kicker">Checked answer</span><Markdown>{agentAnswer}</Markdown></div>}
        </div>
      </aside>
    </div>
  </main>;
}
