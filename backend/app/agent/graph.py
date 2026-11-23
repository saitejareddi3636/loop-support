"""Dynamic LangGraph loop with bounded recovery and deterministic policy output."""
import asyncio
import json
import os
import re
import time
from uuid import uuid4
from typing import Annotated
import operator
from langchain_core.messages import AIMessage,SystemMessage,ToolMessage,message_chunk_to_message
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.config import get_stream_writer
from langgraph.graph import END,START,MessagesState,StateGraph
from app.tools.support import tools_for_chat
from app.agent.answers import refund_reply,invoice_list_reply
from app.policy import grounded_answer,validate_evidence,decision
from app.resilience import classify,retry_delay,RetryLater
from app.metrics import text_cost,PRICING_SOURCE
from app.storage import offer_escalation,verified_customer,conversation_context
from app.data import account_by_customer

SYSTEM_PROMPT="""You are Loopp, a warm, concise support assistant for a fictional demo.
Greet people naturally. Ask politely for their name and account email when needed, one step at a time. A greeting never requires policy evidence.
The backend conversation context records the already verified account. Do not ask for the email again when it is present. Omit customer_id in get_account/get_invoice; never invent customer or invoice IDs.
Use tools as the source of truth. Get account details, then list_invoices when someone asks whether they have eligible refunds without naming an invoice. A general policy does not prove that any personal invoice qualifies. Report actual eligibility from invoice tools, including existing receipts and denial reasons.
Use get_invoice for a known ID and search_policy for applicable rules or troubleshooting. Pass a complete policy question. For follow-ups, use the conversation context and previous turns rather than sending a vague one-word query.
An eligibility question is not a request to execute a refund. When the customer asks to proceed, issue_mock_refund stages a known invoice; if several qualify, ask which one. A separate exact confirmation button is always required. Never issue money, invent an invoice or claim a new receipt before backend confirmation.
Create a human ticket only with clear authorization. Returned error_category denotes an execution failure; a ticket category is an ordinary business field. Explain real limitations in everyday language, with short answers."""

class AgentState(MessagesState):
    results: Annotated[list[dict],operator.add]
    context: dict

def text_content(message):
    c=message.content
    return c if isinstance(c,str) else ''.join(p.get('text','') for p in c if isinstance(p,dict))

def build_graph(api_key,chat_id,trace_id):
    tools=tools_for_chat(chat_id); by_name={t.name:t for t in tools}; simulated=False
    model=ChatGoogleGenerativeAI(model=os.getenv('GEMINI_TEXT_MODEL','gemini-3.5-flash-lite'),google_api_key=api_key,timeout=40,max_retries=0).bind_tools(tools)

    async def call_model(state:AgentState):
        writer=get_stream_writer(); results=state.get('results',[])
        policies=[r['result'] for r in results if r['name']=='search_policy' and 'decision' in r['result']]
        latest=next((text_content(m) for m in reversed(state['messages']) if m.type=='human'),'')
        policy_intent=bool(re.search(r'polic(?:y|ies)|refund|contract|cancel|login|activation|trial|limit|upgrade|downgrade|suspend|reactivat|correction|dispute|payment fail',latest,re.I))
        messages=[SystemMessage(content=SYSTEM_PROMPT),AIMessage(content="Backend conversation context: "+json.dumps(state.get("context",{}),default=str)),*state['messages']]
        writer({'type':'prompt_trace','node':'agent','messages':[{'role':m.type,'content':text_content(m),'tool_calls':getattr(m,'tool_calls',[])} for m in messages]})
        deadline=time.monotonic()+float(os.getenv('MODEL_REQUEST_BUDGET_SECONDS','45'))
        for attempt in range(3):
            started=time.perf_counter(); aggregate=None; delivered=False
            try:
                async with asyncio.timeout(max(.01,deadline-time.monotonic())):
                    async for chunk in model.astream(messages):
                        aggregate=chunk if aggregate is None else aggregate+chunk
                        text=text_content(chunk)
                        if text and results and not policies and not policy_intent and not any('error' in r['result'] for r in results):
                            delivered=True;writer({'type':'delta','text':text,'stream_origin':'model'})
                if aggregate is None: raise RuntimeError('Empty provider response')
                reply=message_chunk_to_message(aggregate)
                usage=getattr(reply,'usage_metadata',None)
                cost=text_cost(os.getenv('GEMINI_TEXT_MODEL','gemini-3.5-flash-lite'),usage)
                writer({'type':'model_completed','duration_ms':round((time.perf_counter()-started)*1000),'usage':usage,'estimated_cost_usd':cost,'cost_scope':'text_only_estimate','pricing_source':PRICING_SOURCE if os.getenv('GEMINI_TEXT_MODEL','gemini-3.5-flash-lite')=='gemini-3.5-flash-lite' else None,'model':os.getenv('GEMINI_TEXT_MODEL','gemini-3.5-flash-lite')})
                if reply.tool_calls:
                    if delivered: writer({'type':'answer_reset'})
                else:
                    # A personal refund answer requires fresh owned invoice evidence.
                    # If the model omits that read, enforce the dependency before replying.
                    personal_refund=bool(re.search(r'refund',latest,re.I) and re.search(r'\b(i|my|me|any|eligible|go ahead|give)\b',latest,re.I) and not re.search(r'\b(policy|rules|terms|window)\b',latest,re.I))
                    invoice_checked=any(r['name']=='issue_mock_refund' or (r['name'] in ('list_invoices','get_invoice') and not r['result'].get('error')) for r in results)
                    if personal_refund and not invoice_checked and state.get('context',{}).get('account'):
                        if delivered:writer({'type':'answer_reset'})
                        return {'messages':[AIMessage(content='',tool_calls=[{'name':'list_invoices','args':{},'id':'required_'+uuid4().hex}])]}
                    requested_action=bool(re.search(r'go ahead|give.{0,30}refund|refund\s+(?:it|that|this|INV-)',latest,re.I))
                    has_stage=any(r['name']=='issue_mock_refund' for r in results)
                    listed=next((r['result'] for r in reversed(results) if r['name']=='list_invoices'),None)
                    eligible=[item for item in (listed or {}).get('invoices',[]) if item.get('eligible')]
                    if not listed:
                        invoice=next((r['result'] for r in reversed(results) if r['name']=='get_invoice' and not r['result'].get('error')),None)
                        requested_ids={value.upper() for value in re.findall(r'\bINV-\d+\b',latest,re.I)}
                        eligible=[invoice] if invoice and invoice.get('eligible') and (not requested_ids or invoice['invoice_id'] in requested_ids) else []
                    if requested_action and not has_stage and len(eligible)==1:
                        if delivered:writer({'type':'answer_reset'})
                        return {'messages':[AIMessage(content='',tool_calls=[{'name':'issue_mock_refund','args':{'invoice_id':eligible[0]['invoice_id']},'id':'requested_'+uuid4().hex}])]}
                    answer=text_content(reply)
                    if policies:
                        evidence=policies[-1]
                        customer=await asyncio.to_thread(verified_customer,chat_id)
                        account=await asyncio.to_thread(account_by_customer,customer) if customer else None
                        tier_changed=bool(account and evidence.get('applicable_tier') not in (None,account['tier']))
                        if tier_changed:
                            evidence=decision('ESCALATION_REQUIRED','The account tier changed while retrieving policy evidence. Please retry the lookup.',account['tier'])
                        if evidence['decision']=='GROUNDED' and not await asyncio.to_thread(validate_evidence,evidence):
                            evidence=decision('ESCALATION_REQUIRED','The retrieved evidence changed, conflicts, or is no longer applicable.',evidence.get('applicable_tier'),top=evidence.get('top_similarity'))
                            writer({'type':'grounding','result':evidence})
                            await asyncio.to_thread(offer_escalation,chat_id,evidence['verification_reason'])
                            writer({'type':'escalation_offered','message':'Would you like a human support ticket?'})
                        refunds=[r['result'] for r in results if r['name']=='issue_mock_refund']
                        answer=await asyncio.to_thread(grounded_answer,evidence,latest,1 if refunds else 3)
                        if refunds:
                            basis=answer if evidence['decision']=='GROUNDED' else ''
                            answer=refund_reply(refunds[-1],basis)
                        # Only deterministic backend action facts are appended to policy excerpts.
                        for r in results:
                            value=r['result']
                            if r['name']=='create_escalation_ticket':
                                answer+='\n\n'+(value.get('error') or f"Ticket {value.get('ticket_id')} is {value.get('status')}.")
                    elif policy_intent or not results:
                        answer="I'd be happy to help. What would you like me to check about your account, an invoice, or a support policy?" if state.get('context',{}).get('account') else "I'd be happy to look into that. What's the email on your fictional demo account?"
                    prior_receipts=[r['result'] for r in results if r['name']=='issue_mock_refund' and r['result'].get('status')=='already_refunded']
                    if prior_receipts: answer=refund_reply(prior_receipts[-1])
                    listings=[r['result'] for r in results if r['name']=='list_invoices']
                    checks=[r['result'] for r in results if r['name']=='issue_mock_refund']
                    if listings and not checks:answer=invoice_list_reply(listings[-1])
                    if checks and not policies:answer=refund_reply(checks[-1])
                    tickets=[r['result'] for r in results if r['name']=='create_escalation_ticket' and r['result'].get('ticket_id')]
                    if tickets:answer=f"Ticket {tickets[-1]['ticket_id']} is {tickets[-1]['status']}. Your request has been saved for human review."
                    failures=[r for r in results if r['result'].get('error_category')]
                    if failures:
                        deferred=next((r['result'] for r in failures if r['result'].get('retry_later')),None)
                        answer=deferred['error'] if deferred else 'A support tool is unavailable, so I cannot verify that answer or complete the request. Would you like a human support ticket?'
                    writer({'type':'final_answer','answer':answer})
                return {'messages':[reply]}
            except Exception as error:
                category,retry_after,retryable=classify(error)
                if delivered or not retryable:raise
                if attempt==2:raise RetryLater(category,retry_after) from error
                delay=retry_delay(error,attempt,deadline-time.monotonic())
                writer({'type':'retry_scheduled','attempt':attempt+1,'delay_ms':round(delay*1000),'category':category,'message':'The provider is temporarily unavailable; retrying before any output or action.'})
                await asyncio.sleep(delay)

    async def call_tools(state:AgentState):
        nonlocal simulated
        writer=get_stream_writer(); output=[]; results=[]
        for call in state['messages'][-1].tool_calls:
            name=call['name']; started=time.perf_counter(); tool_id=call['id']
            writer({'type':'tool_started','name':name,'tool_id':tool_id})
            try:
                if os.getenv('APP_ENV','local')=='local' and os.getenv('DEMO_FAIL_TOOL')==name and not simulated:
                    simulated=True;raise RuntimeError('Controlled local-only tool failure')
                if any(r['name']==name and r['result'].get('error_category') for r in state.get('results',[])):
                    raise RuntimeError('Permanent tool failure already reported this turn')
                async with asyncio.timeout(65):
                    result=await by_name[name].ainvoke(call['args'])
                result=json.loads(json.dumps(result,default=str))
            except Exception as error:
                category,_,_=classify(error); result={'error':error.user_message if isinstance(error,RetryLater) else 'The tool is unavailable. I can offer human assistance.','error_category':category,'outcome':'error'}
                if isinstance(error,RetryLater):result.update(retry_later=True,retry_after_seconds=error.retry_after_seconds)
            result.setdefault('outcome','error' if result.get('error_category') else 'blocked' if result.get('error') else 'success')
            failed=result['outcome']=='error' or bool(result.get('error_category'))
            blocked=bool(result.get('error')) and not failed
            writer({'type':'tool_failed' if failed else 'tool_blocked' if blocked else 'tool_completed','name':name,'tool_id':tool_id,'result':result,'duration_ms':round((time.perf_counter()-started)*1000)})
            if result.get('decision'): writer({'type':'grounding','result':result})
            if (result.get('error_category') and not result.get('retry_later')) or result.get('decision')=='ESCALATION_REQUIRED' or (name=='issue_mock_refund' and blocked and 'review' in result.get('error','')):
                reason=result.get('verification_reason') or result.get('error')
                escalation=await asyncio.to_thread(offer_escalation,chat_id,reason,result.get('sources',[]))
                writer({'type':'escalation_offered','message':'Would you like a human support ticket?','result':escalation})
            results.append({'name':name,'result':result})
            output.append(ToolMessage(content=json.dumps(result),tool_call_id=tool_id,name=name))
        return {'messages':output,'results':results,'context':await asyncio.to_thread(conversation_context,chat_id)}

    def route(state:AgentState):
        last=state['messages'][-1]
        return 'tools' if isinstance(last,AIMessage) and last.tool_calls else END
    graph=StateGraph(AgentState);graph.add_node('agent',call_model);graph.add_node('tools',call_tools)
    graph.add_edge(START,'agent');graph.add_conditional_edges('agent',route,{'tools':'tools',END:END});graph.add_edge('tools','agent')
    return graph.compile()
