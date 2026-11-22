"""Text-model list-price estimate, not an invoice or total voice-session cost."""
import os

PRICING_SOURCE='https://aistudio.google.com/docs/pricing#gemini-3.5-flash-lite'
# Standard paid-tier USD / million tokens; verified 2026-11-21 against Google's pricing.
RATES={'gemini-3.5-flash-lite':(.30,2.50,.03)}

def text_cost(model,usage):
    if not usage: return None
    rates=RATES.get(model)
    configured=(os.getenv('TEXT_INPUT_USD_PER_MILLION'),os.getenv('TEXT_OUTPUT_USD_PER_MILLION'))
    if all(configured):
        try: rates=(float(configured[0]),float(configured[1]),float(configured[0]))
        except ValueError: return None
    if not rates: return None
    incoming=usage.get('input_tokens',0);outgoing=usage.get('output_tokens',0)
    cached=min(incoming,(usage.get('input_token_details') or {}).get('cache_read',0))
    return ((incoming-cached)*rates[0]+outgoing*rates[1]+cached*rates[2])/1e6
