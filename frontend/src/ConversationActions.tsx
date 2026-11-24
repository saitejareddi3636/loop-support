import { useState } from 'react';
import type { ToolRun, Receipt } from './Activity';

export type InvoiceOption = { invoice_id: string; amount_cents: number; currency: string; eligible: boolean };
export function invoiceOptions(tools: ToolRun[], onlyRefundChoice = false): InvoiceOption[] {
  const listing = [...tools].reverse().find(tool => {
    if (!tool.result || typeof tool.result !== 'object' || !('invoices' in tool.result)) return false;
    if (tool.name === 'issue_mock_refund') {
      const status = 'status' in tool.result ? tool.result.status : null;
      return status === 'invoice_choice_required' || onlyRefundChoice;
    }
    // Ordinary list_invoices reviews must not open refund choice cards.
    return false;
  });
  const items = (listing?.result as { invoices?: unknown[] } | undefined)?.invoices;
  return (items || []).filter((item): item is InvoiceOption => !!item && typeof item === 'object' && 'invoice_id' in item && typeof item.invoice_id === 'string' && 'amount_cents' in item && typeof item.amount_cents === 'number' && 'currency' in item && typeof item.currency === 'string' && 'eligible' in item && item.eligible === true);
}
export function InvoiceChoices({ options, busy, onSelect }: { options: InvoiceOption[]; busy: boolean; onSelect: (id: string) => void }) {
  if (!options.length) return null;
  return <section className="refund-card invoice-choices" aria-label="Choose an invoice"><span className="refund-overline">Choose on screen or say an option number</span><h3>Your eligible invoices</h3><ol>{options.map((invoice, index) => <li key={invoice.invoice_id}><button type="button" className="button-jade invoice-choice" disabled={busy} onClick={() => onSelect(invoice.invoice_id)}><strong>Option {index + 1} · {(invoice.amount_cents / 100).toFixed(2)} {invoice.currency}</strong><small>{invoice.invoice_id}</small><span>Review refund →</span></button></li>)}</ol><p>Selecting an invoice prepares its confirmation. Only the separate confirmation button issues a mock receipt.</p></section>;
}
export function EmailCorrection({ busy, onSubmit }: { busy: boolean; onSubmit: (email: string) => void }) {
  const [email, setEmail] = useState('');
  return <form className="refund-card email-correction" onSubmit={event => { event.preventDefault(); if (email.trim()) onSubmit(email.trim()); }}><h3>Check your email address</h3><p>Spell the full email slowly, including dots and the domain, or type it here. We won't guess a similar address.</p><label>Demo account email<input type="email" value={email} onChange={event => setEmail(event.target.value)} required disabled={busy} autoCapitalize="none" autoCorrect="off" autoComplete="off" spellCheck={false} /></label><button type="submit" className="button-jade" disabled={busy || !email.trim()}>Verify this email</button></form>;
}

export function receiptFromResult(value: unknown): Receipt | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Partial<Receipt> & { status?: string };
  if (item.status !== 'already_refunded' || typeof item.receipt_id !== 'string' || typeof item.invoice_id !== 'string' || typeof item.amount_cents !== 'number' || typeof item.currency !== 'string') return null;
  return { receipt_id:item.receipt_id, invoice_id:item.invoice_id, amount_cents:item.amount_cents, currency:item.currency };
}
