"""Bounded provider recovery. Business actions are never replayed by this module."""
import asyncio
import math
import os
import random
import time
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime

class RetryLater(Exception):
    def __init__(self,category,retry_after_seconds):
        self.category=category
        self.retry_after_seconds=retry_after_seconds
        super().__init__('The provider retry instruction exceeds this request budget.')
    @property
    def user_message(self):
        if self.retry_after_seconds:
            return f'The AI provider is temporarily busy. Please try again in at least {math.ceil(self.retry_after_seconds)} seconds. Your conversation is saved; no action will be automatically replayed.'
        return 'The provider could not respond within the request budget. Please try again later. Your conversation is saved.'


def classify(error):
    if isinstance(error,RetryLater):return error.category,error.retry_after_seconds,False
    status=getattr(error,'status_code',None) or getattr(error,'code',None) or getattr(getattr(error,'response',None),'status_code',None)
    try:status=int(status)
    except (TypeError,ValueError):status=None
    category='timeout' if isinstance(error,(TimeoutError,asyncio.TimeoutError)) or 'timeout' in type(error).__name__.lower() else 'rate_limit' if status==429 else 'upstream_unavailable' if status and status>=500 else 'configuration' if status in (400,401,403,404) else 'tool_unavailable'
    headers=getattr(getattr(error,'response',None),'headers',{}) or getattr(error,'headers',{}) or {}
    value=headers.get('Retry-After') or headers.get('retry-after')
    delay=0
    if value is not None:
        try:delay=float(value)
        except (TypeError,ValueError):
            try:
                target=parsedate_to_datetime(value)
                if target.tzinfo is None:target=target.replace(tzinfo=timezone.utc)
                delay=max(0,(target-datetime.now(timezone.utc)).total_seconds())
            except (TypeError,ValueError,OverflowError):delay=0
    if not math.isfinite(delay) or delay<0:delay=0
    return category,delay,category in ('timeout','rate_limit','upstream_unavailable')


def retry_delay(error,attempt,remaining):
    category,instructed,retryable=classify(error)
    if not retryable:raise error
    delay=max(instructed,min(2**attempt+random.uniform(0,.3),8))
    # Reserve time for the next attempt; never shorten a valid provider instruction.
    if delay+2>=remaining:raise RetryLater(category,instructed or delay) from error
    return delay


def sync_lookup(operation):
    deadline=time.monotonic()+float(os.getenv('EMBEDDING_REQUEST_BUDGET_SECONDS','45'))
    for attempt in range(3):
        try:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise RetryLater('timeout',0)
            return operation(remaining)
        except Exception as error:
            if attempt==2:
                category,instructed,retryable=classify(error)
                if retryable:raise RetryLater(category,instructed) from error
                raise
            delay=retry_delay(error,attempt,deadline-time.monotonic())
            time.sleep(delay)
