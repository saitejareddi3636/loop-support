import { useEffect, useState } from 'react';
import ChatApp from './ChatApp';
import VoiceApp from './VoiceApp';

export default function App() {
  const [session, setSession] = useState<'loading' | 'ready' | 'error'>('loading');
  useEffect(() => { void fetch('/api/session', { method: 'POST' }).then(response => setSession(response.ok ? 'ready' : 'error')).catch(() => setSession('error')); }, []);
  const [mode, setMode] = useState<'chat' | 'voice'>(() => new URLSearchParams(window.location.search).get('mode') === 'chat' ? 'chat' : 'voice');
  function changeMode(next: 'chat' | 'voice') {
    setMode(next);
    const url = new URL(window.location.href);
    url.searchParams.set('mode', next);
    window.history.replaceState(null, '', url);
  }
  if (session !== 'ready') return <main className="session-state"><p>{session === 'loading' ? 'Opening your private demo session…' : 'Could not open the demo. Check PostgreSQL migrations and refresh.'}</p>{session === 'error' && <button onClick={() => window.location.reload()}>Try again</button>}</main>;
  return <div className={`app-shell ${mode}`}>
    <nav className="mode-switch" aria-label="Conversation mode">
      <button type="button" className={mode === 'voice' ? 'selected' : ''} aria-current={mode === 'voice' ? 'page' : undefined} onClick={() => changeMode('voice')}><span className="mode-glyph voice-glyph" aria-hidden="true" />Voice</button>
      <button type="button" className={mode === 'chat' ? 'selected' : ''} aria-current={mode === 'chat' ? 'page' : undefined} onClick={() => changeMode('chat')}><span className="mode-glyph chat-glyph" aria-hidden="true" />Chat</button>
    </nav>
    {mode === 'voice' ? <VoiceApp /> : <ChatApp />}
  </div>;
}
