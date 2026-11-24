import type { PendingRefund, Receipt, TraceEvent } from './Activity';

export const CHAT_ID_KEY = 'loopp-chat-id';

export type SyncedMessage = { id: number; role: 'user' | 'assistant'; content: string };
export type SyncedChat = {
  id: string;
  title?: string;
  messages: SyncedMessage[];
  refund_receipts: Receipt[];
  active_turn?: boolean;
  traces: { events: TraceEvent[] }[];
  pending: PendingRefund | null;
};

function sleep(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timer = window.setTimeout(() => resolve(), ms);
    signal?.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      },
      { once: true },
    );
  });
}

/** Remember the shared chat id used by both Chat and Voice. */
export function rememberChatId(chatId: string) {
  localStorage.setItem(CHAT_ID_KEY, chatId);
}

export function rememberedChatId(): string | null {
  return localStorage.getItem(CHAT_ID_KEY);
}

async function fetchSyncedChat(chatId: string, signal?: AbortSignal): Promise<SyncedChat | null> {
  const response = await fetch(`/api/chats/${chatId}`, { signal });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error((await response.json().catch(() => ({}))).detail || `Could not open chat (${response.status})`);
  }
  const chat = (await response.json()) as {
    id: string;
    title?: string;
    messages: SyncedMessage[];
    refund_receipts?: Receipt[];
    active_turn?: boolean;
  };
  const [tracesResponse, pendingResponse] = await Promise.all([
    fetch(`/api/chats/${chatId}/traces`, { signal }),
    fetch(`/api/chats/${chatId}/refund-pending`, { signal }),
  ]);
  const traces = tracesResponse.ok
    ? ((await tracesResponse.json()) as { events: TraceEvent[] }[])
    : [];
  const pending = pendingResponse.ok
    ? ((await pendingResponse.json()) as PendingRefund | null)
    : null;
  rememberChatId(chat.id);
  return {
    id: chat.id,
    title: chat.title,
    messages: chat.messages || [],
    refund_receipts: chat.refund_receipts || [],
    active_turn: !!chat.active_turn,
    traces,
    pending,
  };
}

/**
 * Load messages, receipts, traces, and pending refunds for a chat.
 * Emits the first Postgres snapshot immediately, then keeps polling while a
 * turn is active so chat↔voice switches after unmount / WebSocket close catch
 * the completed assistant row, receipts, and tool traces.
 */
export async function hydrateConversation(
  chatId: string,
  signal?: AbortSignal,
  options?: { maxWaitMs?: number; pollMs?: number; onUpdate?: (chat: SyncedChat) => void },
): Promise<SyncedChat | null> {
  const maxWaitMs = options?.maxWaitMs ?? 12_000;
  const pollMs = options?.pollMs ?? 250;
  const deadline = Date.now() + maxWaitMs;
  let last: SyncedChat | null = null;

  while (Date.now() <= deadline) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    last = await fetchSyncedChat(chatId, signal);
    if (!last) return null;
    options?.onUpdate?.(last);
    if (!last.active_turn) return last;
    await sleep(pollMs, signal);
  }
  return last;
}

/** Find an existing shared chat id. Does not create one (safe for mount/hydrate). */
export async function peekSharedChatId(signal?: AbortSignal): Promise<string | null> {
  const preferred = rememberedChatId();
  if (preferred) {
    const response = await fetch(`/api/chats/${preferred}`, { signal });
    if (response.ok) return preferred;
    if (response.status !== 404) {
      throw new Error((await response.json().catch(() => ({}))).detail || `Could not open shared chat (${response.status})`);
    }
  }
  const listResponse = await fetch('/api/chats', { signal });
  if (!listResponse.ok) {
    throw new Error((await listResponse.json().catch(() => ({}))).detail || `Could not list chats (${listResponse.status})`);
  }
  const list = (await listResponse.json()) as { id: string }[];
  if (list[0]?.id) {
    rememberChatId(list[0].id);
    return list[0].id;
  }
  return null;
}

/** Resolve or create the shared chat id. Only creates after a confirmed miss — never on transient errors. */
export async function resolveSharedChatId(signal?: AbortSignal): Promise<{
  chatId: string | null;
  created: boolean;
}> {
  const existing = await peekSharedChatId(signal);
  if (existing) return { chatId: existing, created: false };
  const created = await fetch('/api/chats', { method: 'POST', signal });
  if (!created.ok) {
    throw new Error((await created.json().catch(() => ({}))).detail || 'Could not create a shared chat.');
  }
  const chat = (await created.json()) as { id: string };
  rememberChatId(chat.id);
  return { chatId: chat.id, created: true };
}
