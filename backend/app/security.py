"""Opaque cookie capability for a demo browser session, not customer authentication."""
import os
from urllib.parse import urlsplit
from fastapi import HTTPException,Request
from app.storage import session_valid,session_id
COOKIE='loopp_session'

def check_origin(connection):
    if os.getenv('APP_ENV','local')=='production' and connection.scope.get('scheme') not in ('https','wss'):
        raise HTTPException(400,'HTTPS is required')
    origin=connection.headers.get('origin')
    if not origin: return
    expected=urlsplit(origin).netloc
    allowed=[value.strip() for value in os.getenv('ALLOWED_ORIGINS','').split(',') if value.strip()]
    if expected!=connection.headers.get('host') and origin not in allowed:
        raise HTTPException(403,'Origin is not permitted')

def owner(request:Request):
    check_origin(request)
    token=request.cookies.get(COOKIE)
    if not session_valid(token): raise HTTPException(401,'Demo session expired. Refresh to start a new session.')
    return session_id(token)


def consume_quota(session):
    import time
    from app.db import connect
    bucket=int(time.time()//60)
    limit=12 if os.getenv('APP_ENV','local')=='production' else 60
    with connect() as db:
        count=db.execute("INSERT INTO demo_quotas(session_id,bucket) VALUES (%s,%s) ON CONFLICT(session_id,bucket) DO UPDATE SET requests=demo_quotas.requests+1 RETURNING requests",(session,bucket)).fetchone()['requests']
        db.execute('DELETE FROM demo_quotas WHERE session_id=%s AND bucket<%s',(session,bucket-2))
    if count>limit: raise HTTPException(429,'Too many demo requests. Please wait a minute.',headers={'Retry-After':'60'})


def limit_session_creation(request):
    """Persistent per-peer and global quotas; forwarded addresses are not trusted here."""
    import hashlib,hmac,time
    from app.db import connect
    production=os.getenv('APP_ENV','local')=='production'
    secret=os.getenv('SESSION_RATE_LIMIT_SECRET','local-demo-rate-limit-namespace').encode()
    peer=request.client.host if request.client else 'unknown'
    client=hmac.new(secret,peer.encode(),hashlib.sha256).hexdigest()
    limits=[(client,60,int(os.getenv('SESSION_CREATION_PER_MINUTE','10' if production else '60'))),
            (client,86400,int(os.getenv('SESSION_CREATION_PER_DAY','100' if production else '1000'))),
            ('global',60,int(os.getenv('SESSION_CREATION_GLOBAL_PER_MINUTE','200'))),
            ('global',86400,int(os.getenv('SESSION_CREATION_GLOBAL_PER_DAY','5000')))]
    now=int(time.time());blocked=[]
    with connect() as db:
        for identity,window,limit in limits:
            bucket=now//window
            row=db.execute('INSERT INTO session_creation_limits VALUES (%s,%s,%s,1) ON CONFLICT(client_hash,window_seconds,bucket) DO UPDATE SET requests=session_creation_limits.requests+1 RETURNING requests',(identity,window,bucket)).fetchone()
            if row['requests']>limit:blocked.append(window-now%window)
        db.execute('DELETE FROM session_creation_limits WHERE window_seconds=60 AND bucket<%s',(now//60-2,))
        db.execute('DELETE FROM session_creation_limits WHERE window_seconds=86400 AND bucket<%s',(now//86400-2,))
    if blocked:raise HTTPException(429,'Too many new demo sessions. Please reuse your current browser session or try again later.',headers={'Retry-After':str(max(blocked))})
