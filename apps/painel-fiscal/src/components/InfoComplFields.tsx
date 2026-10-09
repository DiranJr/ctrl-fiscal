import React, { useEffect, useState } from 'react';
import type { AdditionalInfo, DraftService } from '@ctrl-fiscal/api-client';
import { Button, Input, Textarea } from '@ctrl-fiscal/ui';

/** serv/infoCompl (TCInfoCompl) — todos os campos são opcionais. */
export interface InfoComplState {
  technical_doc: string;
  document_reference: string;
  order_number: string;
  order_items: string[];
  notes: string;
}
export const EMPTY_INFO_COMPL: InfoComplState = { technical_doc: '', document_reference: '', order_number: '', order_items: [], notes: '' };

export const MAX_ORDER_ITEMS = 99;

export const infoComplHasValue = (s: InfoComplState) =>
  !!(s.technical_doc.trim() || s.document_reference.trim() || s.order_number.trim() || s.notes.trim()
    || s.order_items.some((i) => i.trim()));

export function infoComplFromService(s?: DraftService | null): InfoComplState {
  const a = s?.additional_info;
  return {
    technical_doc: a?.technical_doc || '',
    document_reference: a?.document_reference || '',
    order_number: a?.order_number || '',
    order_items: a?.order_items ? [...a.order_items] : [],
    notes: a?.notes || '',
  };
}

/** Grupo sem nenhum valor → null (o chamador omite no POST e envia null no PATCH). Brancos nunca são enviados. */
export function infoComplToPayload(s: InfoComplState): AdditionalInfo | null {
  const out: AdditionalInfo = {};
  if (s.technical_doc.trim()) out.technical_doc = s.technical_doc.trim();
  if (s.document_reference.trim()) out.document_reference = s.document_reference.trim();
  if (s.order_number.trim()) out.order_number = s.order_number.trim();
  const items = s.order_items.map((i) => i.trim()).filter(Boolean);
  if (items.length) out.order_items = items;
  if (s.notes.trim()) out.notes = s.notes.trim();
  return Object.keys(out).length ? out : null;
}

export function validateInfoCompl(s: InfoComplState): Record<string, string> {
  const out: Record<string, string> = {};
  const p = 'service.additional_info';
  if (s.technical_doc.trim().length > 40) out[`${p}.technical_doc`] = 'Até 40 caracteres.';
  if (s.document_reference.trim().length > 255) out[`${p}.document_reference`] = 'Até 255 caracteres.';
  if (s.order_number.trim().length > 60) out[`${p}.order_number`] = 'Até 60 caracteres.';
  const items = s.order_items.map((i) => i.trim()).filter(Boolean);
  if (items.length > MAX_ORDER_ITEMS) out[`${p}.order_items`] = `No máximo ${MAX_ORDER_ITEMS} itens.`;
  if (items.some((i) => i.length > 60)) out[`${p}.order_items`] = 'Cada item com até 60 caracteres.';
  if (s.notes.trim().length > 2000) out[`${p}.notes`] = 'Até 2000 caracteres.';
  return out;
}

/** Informações complementares do serviço (ART/RRT, referência, pedido, itens do pedido e observações). */
export const InfoComplFields: React.FC<{
  value: InfoComplState;
  onChange: (v: InfoComplState) => void;
  errors?: Record<string, string | undefined>;
}> = ({ value, onChange, errors = {} }) => {
  const has = infoComplHasValue(value);
  const [open, setOpen] = useState(has);
  useEffect(() => { if (has) setOpen(true); }, [has]);
  const p = 'service.additional_info';
  const err = (k: string) => errors[`${p}.${k}`];
  const itemsErr = err('order_items') || Object.entries(errors).find(([k, v]) => k.startsWith(`${p}.order_items.`) && v)?.[1];
  const setItem = (idx: number, v: string) => onChange({ ...value, order_items: value.order_items.map((x, i) => (i === idx ? v : x)) });
  const removeItem = (idx: number) => onChange({ ...value, order_items: value.order_items.filter((_, i) => i !== idx) });
  const addItem = () => onChange({ ...value, order_items: [...value.order_items, ''] });

  return (
    <details className="pf-subpanel pf-disclosure" open={open} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>Informações complementares (opcional)</summary>
      <p className="pf-help">
        Use quando houver documento técnico (ART/RRT/DRT), referência a outro documento, número de pedido do tomador ou
        observações. Nada aqui é obrigatório.
      </p>
      <div className="pf-form-grid">
        <Input label="Documento técnico — ART, RRT ou DRT (idDocTec)" value={value.technical_doc} maxLength={40}
          onChange={(e) => onChange({ ...value, technical_doc: e.target.value })} error={err('technical_doc')}
          helperText="Quando houver responsável técnico registrado. Até 40 caracteres." />
        <Input label="Referência a documento (docRef)" value={value.document_reference} maxLength={255}
          onChange={(e) => onChange({ ...value, document_reference: e.target.value })} error={err('document_reference')}
          helperText="Contrato ou outro documento relacionado. Até 255 caracteres." />
        <Input label="Número do pedido ou ordem de compra (xPed)" value={value.order_number} maxLength={60}
          onChange={(e) => onChange({ ...value, order_number: e.target.value })} error={err('order_number')}
          helperText="Quando o tomador exige o pedido/ordem/projeto. Até 60 caracteres." />
      </div>

      <fieldset className="pf-fieldset pf-form-stack">
        <legend>Itens do pedido (xItemPed)</legend>
        <p className="pf-help">Opcional: até {MAX_ORDER_ITEMS} itens com até 60 caracteres cada, normalmente junto do número do pedido.</p>
        {value.order_items.map((item, idx) => (
          <div key={idx} className="pf-item-row">
            <Input label={`Item ${idx + 1} do pedido`} value={item} maxLength={60}
              onChange={(e) => setItem(idx, e.target.value)} helperText={`${item.trim().length}/60 caracteres`} />
            <Button size="sm" variant="ghost" onClick={() => removeItem(idx)} aria-label={`Remover item ${idx + 1} do pedido`}>
              Remover
            </Button>
          </div>
        ))}
        {itemsErr && <span role="alert" className="pf-help" style={{ color: 'var(--color-danger)', fontWeight: 500 }}>{itemsErr}</span>}
        <div>
          <Button size="sm" variant="outline" onClick={addItem} disabled={value.order_items.length >= MAX_ORDER_ITEMS}>
            Adicionar item do pedido
          </Button>
        </div>
      </fieldset>

      <Textarea label="Informações complementares (xInfComp)" rows={3} value={value.notes} maxLength={2000}
        onChange={(e) => onChange({ ...value, notes: e.target.value })} error={err('notes')}
        helperText={`Opcional. ${value.notes.length}/2000 caracteres.`} />
    </details>
  );
};
