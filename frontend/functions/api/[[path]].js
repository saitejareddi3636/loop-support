// Keep browser cookies first-party; pass SSE and WebSockets through without buffering.
export async function onRequest({ request, env }) {
  const url = new URL(request.url);
  const headers = new Headers(request.headers);
  if (url.origin !== env.PUBLIC_ORIGIN ||
      (headers.has('Origin') && headers.get('Origin') !== env.PUBLIC_ORIGIN)) {
    return new Response('Origin is not permitted', { status: 403 });
  }
  if (!env.API_ORIGIN?.startsWith('https://') || !env.LOOPP_GATEWAY_SECRET) {
    return new Response('API gateway is not configured', { status: 503 });
  }
  const target = new URL(env.API_ORIGIN);
  target.pathname = url.pathname;
  target.search = url.search;
  for (const name of ['Host', 'X-Forwarded-For', 'X-Forwarded-Host', 'X-Forwarded-Proto', 'X-Loopp-Gateway', 'X-Loopp-Client-IP']) {
    headers.delete(name);
  }
  headers.set('X-Loopp-Gateway', env.LOOPP_GATEWAY_SECRET);
  headers.set('X-Loopp-Client-IP', request.headers.get('CF-Connecting-IP') || 'unknown');
  try {
    // No automatic retry: a disconnected mutation may already have committed.
    return await fetch(new Request(target, { method: request.method, headers,
      body: request.body, redirect: 'manual' }));
  } catch {
    return Response.json({ detail: 'Support is temporarily unavailable. Please reconnect.' },
      { status: 503, headers: { 'Cache-Control': 'no-store', 'Retry-After': '30' } });
  }
}
