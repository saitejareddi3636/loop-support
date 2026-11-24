export async function streamChat(chatId: string, message: string, onEvent: (event: Record<string, unknown>) => void) {
  const response = await fetch(`/api/chats/${chatId}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message, request_id: crypto.randomUUID() }) });
  if (!response.ok || !response.body) throw new Error((await response.json().catch(() => ({}))).detail || `Chat failed (${response.status})`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let completed = false;
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() || '';
    if (done && buffer.trim()) frames.push(buffer);
    for (const frame of frames) {
      const line = frame.split('\n').find(part => part.startsWith('data: '));
      if (!line) continue;
      const event = JSON.parse(line.slice(6)) as Record<string, unknown>;
      if (event.type === 'complete') completed = true;
      onEvent(event);
      if (event.type === 'error') throw new Error(String(event.message || 'The request failed.'));
    }
    if (done) break;
  }
  if (!completed) throw new Error('The response disconnected. Reopen saved history before retrying; no action was replayed.');
}
