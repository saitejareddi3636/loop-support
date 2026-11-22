"""Focused isolated-PostgreSQL checks. RUN_DB_TESTS=1 enables database creation."""
import asyncio
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import psycopg
from psycopg import sql
from app import storage
from app.db import connect
from app.policy import document_chunks,seed,search,validate_evidence,grounded_answer
from app.refunds import stage_refund,confirm_refund,create_ticket
from app.resilience import classify
from app.tools.customer import get_invoice,get_account,verify_customer
from app.main import app,execute_voice_request
from fastapi.testclient import TestClient
from migrate import migrate
from seed_data import seed as seed_records

@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1 for isolated PostgreSQL tests')
class HardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_url=os.environ['DATABASE_URL']
        cls.dbname='loopp_test_'+uuid4().hex[:10]
        cls.admin=psycopg.connect(cls.original_url,autocommit=True)
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.dbname)))
        from psycopg.conninfo import make_conninfo
        os.environ['DATABASE_URL']=make_conninfo(cls.original_url,dbname=cls.dbname)
        migrate();seed_records()
        with patch('app.policy.embed',return_value=[1.0]+[0.0]*767): seed()

    @classmethod
    def tearDownClass(cls):
        os.environ['DATABASE_URL']=cls.original_url
        cls.admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(cls.dbname)))
        cls.admin.close()

    def setUp(self):
        class DemoDate(date):
            @classmethod
            def today(cls): return cls(2026,11,21)
        self.clock=patch('app.refunds.date',DemoDate)
        self.clock.start();self.addCleanup(self.clock.stop)
        self.chat=storage.create_chat(storage.session_id(storage.new_session()))['id']
        storage.set_verified_customer(self.chat,'cus_1001')

    def test_dataset_relations_and_owned_tools(self):
        with connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM customers').fetchone()['n'],20)
            self.assertEqual(db.execute('SELECT count(*) AS n FROM invoices').fetchone()['n'],81)
            self.assertEqual(db.execute('SELECT count(DISTINCT tier) AS n FROM subscriptions').fetchone()['n'],4)
        c=verify_customer.invoke({'email':'alex.morgan@example.test'})
        account=get_account.invoke({'customer_id':c['customer_id']})
        self.assertEqual(account['contract_status'],'active')
        self.assertEqual(float(get_invoice.invoke({'customer_id':c['customer_id'],'invoice_id':'INV-1001'})['amount']),29)
        self.assertIn('error',get_invoice.invoke({'customer_id':c['customer_id'],'invoice_id':'INV-2001'}))

    def test_fresh_chat_and_locked_customer(self):
        storage.add_message(self.chat,'user','Hello')
        second=storage.create_chat(storage.session_id(storage.new_session()))['id']
        self.assertEqual(storage.get_chat(second)['messages'],[])
        with self.assertRaises(ValueError):storage.set_verified_customer(self.chat,'cus_1002')

    def test_refund_exact_confirmation_and_concurrent_replay(self):
        self.assertIn('error',stage_refund(self.chat,'INV-2001'))
        self.assertIn('error',stage_refund(self.chat,'INV-1002'))
        p=stage_refund(self.chat,'INV-1001');self.assertEqual(p['status'],'confirmation_required')
        self.assertEqual(stage_refund(self.chat,'INV-1001')['token'],p['token'])
        self.assertIn('error',confirm_refund(self.chat,p['token'],'INV-1001',1))
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:confirm_refund(self.chat,p['token'],'INV-1001',2900),range(2)))
        self.assertEqual(results[0]['receipt_id'],results[1]['receipt_id'])
        with patch('app.refunds.eligibility',side_effect=AssertionError('An existing receipt must not issue a new refund')):
            prior=stage_refund(self.chat,'INV-1001')
        self.assertEqual(prior['status'],'already_refunded')
        self.assertEqual(prior['receipt_id'],results[0]['receipt_id'])
        self.assertNotIn('error',prior)
        other=storage.create_chat(storage.session_id(storage.new_session()))['id']
        storage.set_verified_customer(other,'cus_1002')
        self.assertIn('error',stage_refund(other,'INV-1001'))
        self.assertNotIn('receipt_id',stage_refund(other,'INV-1001'))

        with connect() as db:
            self.assertEqual(db.execute("SELECT count(*) AS n FROM refund_receipts WHERE invoice_id='INV-1001'").fetchone()['n'],1)

    def test_subscription_contract_and_confirmation_expiry(self):
        with connect() as db:db.execute("UPDATE subscriptions SET status='suspended' WHERE account_id='acc_1001'")
        self.assertIn('error',stage_refund(self.chat,'INV-1003'))
        with connect() as db:
            db.execute("UPDATE subscriptions SET status='active' WHERE account_id='acc_1001'")
            db.execute("UPDATE contracts SET status='expired' WHERE account_id='acc_1001'")
        self.assertIn('error',stage_refund(self.chat,'INV-1003'))
        with connect() as db:db.execute("UPDATE contracts SET status='active' WHERE account_id='acc_1001'")
        p=stage_refund(self.chat,'INV-1003')
        with connect() as db:db.execute("UPDATE refund_pending SET expires_at=now()-interval '1 minute' WHERE token=%s",(p['token'],))
        self.assertIn('error',confirm_refund(self.chat,p['token'],'INV-1003',1900))

    def test_tier_limits_and_manual_enterprise(self):
        with connect() as db:
            db.execute("UPDATE subscriptions SET tier='Enterprise' WHERE account_id='acc_1001'")
        self.assertIn('error',stage_refund(self.chat,'INV-1003'))
        with connect() as db:
            db.execute("UPDATE subscriptions SET tier='Business' WHERE account_id='acc_1001'")
            db.execute("UPDATE invoices SET amount_cents=60000 WHERE invoice_id='INV-1003'")
        self.assertIn('error',stage_refund(self.chat,'INV-1003'))
        with connect() as db:
            db.execute("UPDATE subscriptions SET tier='Pro' WHERE account_id='acc_1001'")
            db.execute("UPDATE invoices SET amount_cents=1900 WHERE invoice_id='INV-1003'")

    def test_ticket_consent_and_paraphrase_idempotency(self):
        storage.add_message(self.chat,'user','No ticket please')
        self.assertIn('error',create_ticket(self.chat,'billing','Cannot identify this charge'))
        storage.add_message(self.chat,'user','Please create a support ticket')
        first=create_ticket(self.chat,'billing','Cannot identify this charge')
        second=create_ticket(self.chat,'other','A paraphrased description of the same issue')
        self.assertEqual(first['ticket_id'],second['ticket_id'])
        self.assertEqual(second['status'],'open')
        self.assertIn('recent_messages',second['context'])

    def test_followup_consent(self):
        storage.offer_escalation(self.chat,'Evidence unavailable')
        storage.add_message(self.chat,'assistant','Would you like a human support ticket?')
        storage.add_message(self.chat,'user','Yes please')
        self.assertEqual(create_ticket(self.chat,'policy','The policy evidence is unavailable')['status'],'open')

    def test_cookie_session_isolation_and_websocket_rejection(self):
        with TestClient(app) as a,TestClient(app) as b:
            self.assertEqual(a.get('/api/chats').status_code,401)
            a.post('/api/session');b.post('/api/session')
            chat=a.post('/api/chats').json()['id']
            self.assertEqual(b.get('/api/chats/'+chat).status_code,404)
            self.assertEqual(b.get('/api/chats/'+chat+'/traces').status_code,404)
            self.assertEqual(b.get('/api/chats/'+chat+'/refund-pending').status_code,404)
            self.assertEqual(a.post('/api/session',headers={'origin':'https://attacker.test'}).status_code,403)
            with b.websocket_connect('/api/voice?chat_id='+chat) as ws:
                self.assertEqual(ws.receive_json()['type'],'error')

    def test_rag_applicability_archival_and_citation_tampering(self):
        with patch('app.policy.embed',return_value=[1.0]+[0.0]*767):
            result=search('Explain current refund eligibility for my account','Pro')
        self.assertEqual(result['decision'],'GROUNDED')
        self.assertTrue(validate_evidence(result))
        for s in result['sources']:
            self.assertNotEqual(s['document_id'],'refunds-archive')
            self.assertTrue('all' in s['tiers'] or 'Pro' in s['tiers'])
        result['sources'][0]['excerpt']='An invented unsupported entitlement'
        self.assertFalse(validate_evidence(result))
        self.assertIn('cannot verify',grounded_answer(result))
        self.assertEqual(search('refund',None)['decision'],'NEEDS_CLARIFICATION')

    def test_chunk_quality_stability_and_embedding_reuse(self):
        docs=json.loads(Path('policies.json').read_text())
        cs=[c for d in docs for c in document_chunks(d)]
        self.assertTrue(all(len(c['content'].split())>40 for c in cs))
        self.assertEqual(document_chunks(docs[0]),document_chunks(docs[0]))
        with patch('app.policy.embed',side_effect=AssertionError('Unchanged chunks must not embed')):
            self.assertEqual(seed()['embeddings_updated'],0)

    def test_voice_duplicate_and_failed_reservation(self):
        calls=[]
        async def fake(*args):
            calls.append(args)
            yield {'type':'complete','answer':'Invoice found.'}
        async def emit(event): pass
        async def run():
            with patch('app.main.run_turn',fake):
                a=await execute_voice_request(self.chat,'call-1','Find invoice','test',emit)
                b=await execute_voice_request(self.chat,'call-1','Find invoice','test',emit)
                self.assertEqual(a,b)
        asyncio.run(run());self.assertEqual(len(calls),1)

    def test_interrupted_tool_persists_failure_without_partial_assistant(self):
        from app.agent.runner import run_turn
        class Graph:
            async def astream(self,*args,**kwargs):
                yield {'type':'tool_started','name':'get_invoice','tool_id':'interrupted-tool'}
                raise TimeoutError('Controlled mid-turn timeout')
        async def run():
            with patch('app.agent.runner.build_graph',return_value=Graph()):
                return [event async for event in run_turn(self.chat,'Get invoice','fake','interrupted-request')]
        events=asyncio.run(run())
        self.assertTrue(any(e['type']=='tool_failed' for e in events))
        self.assertTrue(any(e['type']=='error' and e['category']=='timeout' for e in events))
        self.assertFalse(any(e['type']=='complete' for e in events))
        self.assertEqual([m['role'] for m in storage.get_chat(self.chat)['messages']],['user'])
        with connect() as db:
            trace=db.execute('SELECT events FROM traces WHERE chat_id=%s',(self.chat,)).fetchone()['events']
            self.assertTrue(any(e['type']=='tool_failed' for e in trace))

    def test_session_bound_discovery_and_invoice_choice_survive_turns(self):
        from app.tools.support import tools_for_chat
        other=storage.create_chat(storage.session_id(storage.new_session()))['id']
        storage.set_verified_customer(other,'cus_1003')
        tools={tool.name:tool for tool in tools_for_chat(other)}
        self.assertEqual(tools['get_account'].invoke({})['customer_id'],'cus_1003')
        self.assertIn('error',tools['get_account'].invoke({'customer_id':'cus_1001'}))
        storage.update_context(other,{'selected_invoice_id':'INV-3001'})
        listing=tools['list_invoices'].invoke({})
        self.assertIsNone(storage.conversation_context(other)['references']['selected_invoice_id'])
        self.assertEqual(listing['eligible_count'],3)
        from app.agent.runner import run_turn
        from langchain_core.messages import AIMessageChunk
        class Model:
            def bind_tools(self,tools):return self
            async def astream(self,messages):yield AIMessageChunk(content='Generic policy answer must not replace an invoice check.')
        async def followup():
            with patch('app.agent.graph.ChatGoogleGenerativeAI',return_value=Model()):
                return [e async for e in run_turn(other,'Go ahead and give the refund','fake','context-guard')]
        events=asyncio.run(followup())
        self.assertTrue(any(e.get('name')=='list_invoices' and e['type']=='tool_completed' for e in events))
        self.assertIn('Which invoice',next(e['answer'] for e in events if e['type']=='complete'))

        self.assertTrue(all(i['customer_id']=='cus_1003' for i in listing['invoices']))
        self.assertEqual(tools['issue_mock_refund'].invoke({})['status'],'invoice_choice_required')
        self.assertIsNone(__import__('app.refunds',fromlist=['pending_refund']).pending_refund(other))
        tools['get_invoice'].invoke({'invoice_id':'INV-3002'})
        next_turn={tool.name:tool for tool in tools_for_chat(other)}
        pending=next_turn['issue_mock_refund'].invoke({})
        self.assertEqual(pending['invoice_id'],'INV-3002')
        self.assertEqual(pending['status'],'confirmation_required')
        self.assertIn('error',next_turn['get_invoice'].invoke({'invoice_id':'INV-1001'}))

    def test_successful_billing_ticket_is_persisted_and_reported(self):
        from app.agent.runner import run_turn
        from langchain_core.messages import AIMessageChunk
        class Model:
            calls=0
            def bind_tools(self,tools):return self
            async def astream(self,messages):
                self.calls+=1
                if self.calls==1:
                    yield AIMessageChunk(content='',tool_calls=[{'name':'create_escalation_ticket','args':{'category':'billing','reason':'Please review the disputed billing charge'},'id':'billing-call'}])
                else:yield AIMessageChunk(content='The support ticket is open.')
        async def run():
            with patch('app.agent.graph.ChatGoogleGenerativeAI',return_value=Model()):
                return [e async for e in run_turn(self.chat,'Please create a human support ticket for my billing charge','fake','billing-regression')]
        events=asyncio.run(run())
        result=next(e['result'] for e in events if e['type']=='tool_completed' and e.get('name')=='create_escalation_ticket')
        answer=next(e['answer'] for e in events if e['type']=='complete')
        self.assertIn(result['ticket_id'],answer)
        self.assertIn('open',answer)
        self.assertNotIn('unavailable',answer)
        self.assertFalse(any(e['type']=='escalation_offered' for e in events))
        with connect() as db:
            self.assertEqual(db.execute('SELECT category,status FROM escalation_tickets WHERE ticket_id=%s',(result['ticket_id'],)).fetchone(),{'category':'billing','status':'open'})

    def test_new_session_quotas_do_not_break_existing_sessions(self):
        limits={'SESSION_CREATION_PER_MINUTE':'2','SESSION_CREATION_PER_DAY':'2','SESSION_CREATION_GLOBAL_PER_MINUTE':'1000','SESSION_CREATION_GLOBAL_PER_DAY':'10000'}
        with patch.dict(os.environ,limits),TestClient(app,client=('quota-regression',51000)) as existing:
            self.assertEqual(existing.post('/api/session').status_code,200)
            token=existing.cookies['loopp_session']
            for _ in range(3):self.assertEqual(existing.post('/api/session').status_code,200)
            self.assertEqual(existing.cookies['loopp_session'],token)
            with TestClient(app,client=('quota-regression',51001)) as fresh:
                self.assertEqual(fresh.post('/api/session').status_code,200)
            with TestClient(app,client=('quota-regression',51002)) as churn:
                blocked=churn.post('/api/session',headers={'X-Forwarded-For':'a-different-address'})
                self.assertEqual(blocked.status_code,429)
                self.assertGreater(int(blocked.headers['Retry-After']),0)
            self.assertEqual(existing.get('/api/chats').status_code,200)
            self.assertEqual(existing.post('/api/session').status_code,200)

class RecoveryTests(unittest.TestCase):
    def test_rate_limit_retry_after_timeout_and_5xx(self):
        class Error(Exception):status_code=429;headers={'Retry-After':'2'}
        self.assertEqual(classify(Error()),('rate_limit',2,True))
        self.assertTrue(classify(TimeoutError())[2])
        e=Error();e.status_code=503;self.assertTrue(classify(e)[2])
        e.status_code=403;self.assertFalse(classify(e)[2])
        from app.resilience import RetryLater,retry_delay
        e.status_code=429;e.headers={'Retry-After':'120'}
        self.assertEqual(classify(e)[1],120)
        with self.assertRaises(RetryLater) as deferred:retry_delay(e,0,45)
        self.assertEqual(deferred.exception.retry_after_seconds,120)
        from email.utils import format_datetime
        e.headers={'Retry-After':format_datetime(datetime.now(timezone.utc)+timedelta(seconds=120))}
        self.assertGreater(classify(e)[1],110)
        from app.config import validate_runtime_config
        settings={'APP_ENV':'production','GEMINI_API_KEY':'test-key-not-used-for-network','SESSION_RATE_LIMIT_SECRET':'x'*40,'DATABASE_URL':'postgresql://demo:unused-config-test-password@db.example.test/loopp?sslmode=verify-full','ALLOWED_ORIGINS':'https://demo.example.test'}
        with patch.dict(os.environ,settings):
            validate_runtime_config()
            with patch.dict(os.environ,{'SESSION_RATE_LIMIT_SECRET':''}):
                with self.assertRaises(RuntimeError):validate_runtime_config()


class GraphRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_429_retries_once_before_output(self):
        from app.agent.graph import build_graph
        from langchain_core.messages import AIMessageChunk,HumanMessage
        class Busy(Exception):status_code=429;headers={'Retry-After':'0'}
        class Model:
            calls=0
            def bind_tools(self,tools):return self
            async def astream(self,messages):
                self.calls+=1
                if self.calls==1:raise Busy()
                yield AIMessageChunk(content='Please clarify your request.')
        model=Model()
        with patch('app.agent.graph.ChatGoogleGenerativeAI',return_value=model),patch('app.agent.graph.asyncio.sleep',return_value=None):
            graph=build_graph('fake','not-persisted','trace')
            events=[event async for event in graph.astream({'messages':[HumanMessage('Hello')],'results':[]},stream_mode='custom')]
        self.assertEqual(model.calls,2)
        self.assertEqual(sum(e['type']=='retry_scheduled' for e in events),1)
        self.assertTrue(any(e['type']=='final_answer' for e in events))

    async def test_tool_failure_emits_failure_and_escalation(self):
        from app.agent.graph import build_graph
        from langchain_core.messages import AIMessageChunk,HumanMessage
        class Model:
            calls=0
            def bind_tools(self,tools):return self
            async def astream(self,messages):
                self.calls+=1
                if self.calls==1:
                    yield AIMessageChunk(content='',tool_calls=[{'name':'get_invoice','args':{'customer_id':'cus_1001','invoice_id':'INV-1002'},'id':'test-call'}])
                else:yield AIMessageChunk(content='This must not become a fabricated invoice answer.')
        with patch('app.agent.graph.ChatGoogleGenerativeAI',return_value=Model()),patch('app.agent.graph.offer_escalation',return_value={'status':'eligible'}),patch.dict(os.environ,{'APP_ENV':'local','DEMO_FAIL_TOOL':'get_invoice'}):
            events=[event async for event in build_graph('fake','not-persisted','trace').astream({'messages':[HumanMessage('Find the invoice')],'results':[]},stream_mode='custom')]
        self.assertTrue(any(e['type']=='tool_failed' for e in events))
        self.assertFalse(any(e['type']=='tool_completed' for e in events))
        self.assertTrue(any(e['type']=='escalation_offered' for e in events))
        self.assertIn('unavailable',next(e['answer'] for e in events if e['type']=='final_answer'))

    async def test_long_retry_after_returns_later_without_early_retry(self):
        from app.agent.graph import build_graph
        from app.resilience import RetryLater
        from langchain_core.messages import HumanMessage
        class Busy(Exception):status_code=429;headers={'Retry-After':'120'}
        class Model:
            calls=0
            def bind_tools(self,tools):return self
            async def astream(self,messages):
                self.calls+=1
                raise Busy()
                yield
        model=Model()
        with patch('app.agent.graph.ChatGoogleGenerativeAI',return_value=model),patch('app.agent.graph.asyncio.sleep') as sleep:
            with self.assertRaises(RetryLater):
                async for _ in build_graph('fake','not-persisted','retry-check').astream({'messages':[HumanMessage('Help')],'results':[]},stream_mode='custom'):pass
        self.assertEqual(model.calls,1)
        sleep.assert_not_called()

class MetricsTests(unittest.TestCase):
    def test_cost_is_actual_usage_at_documented_rates(self):
        from app.metrics import text_cost
        usage={'input_tokens':1000,'output_tokens':100,'input_token_details':{'cache_read':0}}
        self.assertAlmostEqual(text_cost('gemini-3.5-flash-lite',usage),.00055)
        self.assertIsNone(text_cost('unknown-model',usage))
        self.assertIsNone(text_cost('gemini-3.5-flash-lite',None))
