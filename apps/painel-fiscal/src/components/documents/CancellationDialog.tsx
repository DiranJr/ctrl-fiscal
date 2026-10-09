import React, { useMemo, useRef, useState } from 'react';
import { formatCurrencyPtBr } from '@ctrl-fiscal/api-client';
import type { CancellationInfo, CancellationKind, CancellationRequestOut } from '@ctrl-fiscal/api-client';
import { Alert, Badge, Button, Input, Modal, Select, Textarea } from '@ctrl-fiscal/ui';
import { useFiscal, logSafe } from '../../context/FiscalContext.js';

const ENV_LABEL: Record<string, { label: string; status: string }> = {
  production: { label: 'Produção', status: 'AUTHORIZED' },
  restricted: { label: 'Produção Restrita (homologação)', status: 'SCHEDULED' },
  mock: { label: 'Simulação (sem validade fiscal)', status: 'DRAFT' },
};

// Mesmas regras do leiaute (TSMotivo/TSString): 15–255, sem espaço nas pontas, sem quebra de linha, Latin-1.
// A validação definitiva é do servidor; aqui só orienta o preenchimento.
export function justificativaErros(texto: string): string[] {
  const erros: string[] = [];
  if (texto.length < 15 || texto.length > 255) erros.push('Use de 15 a 255 caracteres.');
  if (/[\r\n]/.test(texto)) erros.push('Sem quebra de linha.');
  if (texto !== texto.trim()) erros.push('Sem espaço no início ou no fim.');
  if (/[^ -ÿ]/.test(texto)) erros.push('Há caracteres fora do conjunto aceito pelo leiaute (ex.: emoji, aspas curvas).');
  return erros;
}

function newKey(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Confirmação EXPLÍCITA do pedido de cancelamento (evento 101101) ou de análise fiscal (101103).
 * - Mostra empresa, ambiente e a nota (número, chave, tomador, valor) que serão enviados;
 * - exige motivo do leiaute, justificativa e a digitação do número da nota;
 * - a chave de idempotência é criada UMA vez por abertura do diálogo: clique duplo ou nova tentativa após falha
 *   de rede não geram dois pedidos;
 * - nada muda na nota aqui: a situação só muda quando o evento oficial for registrado.
 */
const CancellationDialog: React.FC<{
  info: CancellationInfo;
  initialKind: CancellationKind;
  onClose: () => void;
  onRequested: (r: CancellationRequestOut) => void;
}> = ({ info, initialKind, onClose, onRequested }) => {
  const { clients } = useFiscal();
  const idemKey = useRef(newKey());
  const [kind, setKind] = useState<CancellationKind>(initialKind);
  const [reason, setReason] = useState<'1' | '2' | '9' | ''>('');
  const [text, setText] = useState('');
  const [typed, setTyped] = useState('');
  const [ack, setAck] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const d = info.document;
  const env = ENV_LABEL[d.environment] || { label: d.environment, status: 'UNKNOWN' };
  const textErrors = useMemo(() => (text ? justificativaErros(text) : []), [text]);
  const number = d.number || '';
  const ready = info.eligible[kind] && !!reason && text.length > 0 && textErrors.length === 0 && ack
    && typed.trim() === number;

  const submit = async () => {
    if (!ready || sending) return;
    setSending(true);
    setError(null);
    try {
      const r = await clients.fiscal.requestCancellation(info.company.id, d.id, {
        kind, reason_code: reason as '1' | '2' | '9', reason_text: text,
        confirm: { access_key: d.access_key, environment: d.environment, company_cnpj: info.company.cnpj, document_number: number },
      }, idemKey.current);
      onRequested(r);
    } catch (err) {
      logSafe('cancellation.request', err);
      const p = (err as { problem?: { detail?: string; errors?: { message?: string }[] } })?.problem;
      setError([p?.detail, ...(p?.errors || []).map((e) => e.message)].filter(Boolean).join(' ') || 'Falha ao registrar o pedido.');
    } finally {
      setSending(false);
    }
  };

  return (
    <Modal isOpen onClose={sending ? () => undefined : onClose} maxWidth="min(680px, 96vw)"
      title={kind === 'CANCEL' ? 'Cancelar NFS-e' : 'Solicitar análise fiscal para cancelamento'}
      description="Confira empresa, ambiente e nota. O pedido é enviado ao Sistema Nacional NFS-e; a situação da nota só muda quando o evento oficial for registrado."
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={sending}>Voltar sem enviar</Button>
          <Button variant="danger" onClick={submit} isLoading={sending} disabled={!ready || sending}>
            {kind === 'CANCEL' ? 'Enviar pedido de cancelamento' : 'Enviar solicitação de análise'}
          </Button>
        </>
      }>
      <div className="pf-stack">
        <section aria-label="Identificação" style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '0.75rem' }}>
          <dl className="pf-dl">
            <dt>Empresa (emitente)</dt><dd>{info.company.name} — CNPJ {info.company.cnpj}</dd>
            <dt>Ambiente</dt><dd><Badge status={env.status} customLabel={env.label} /></dd>
            <dt>NFS-e nº</dt><dd><strong>{number || '—'}</strong></dd>
            <dt>Chave de acesso</dt><dd className="ui-mono" style={{ wordBreak: 'break-all' }}>{d.access_key}</dd>
            <dt>Tomador</dt><dd>{d.recipient_name || '—'} {d.recipient_doc || ''}</dd>
            <dt>Valor do serviço</dt><dd>{d.amount ? formatCurrencyPtBr(d.amount) : '—'}</dd>
            <dt>Emissão</dt><dd>{d.issued_at ? new Date(d.issued_at).toLocaleString('pt-BR') : '—'}</dd>
          </dl>
        </section>

        {d.environment === 'mock' && (
          <Alert variant="info">Ambiente de simulação: nada é enviado ao governo; o evento gerado é SIMULADO.</Alert>
        )}
        {d.environment === 'production' && (
          <Alert variant="warning" title="Produção: efeito fiscal real">
            O pedido será assinado com o certificado da empresa e enviado à Sefin Nacional. O cancelamento aceito não pode
            ser desfeito. A situação da nota só muda depois da confirmação oficial; a solicitação de análise fiscal fica
            pendente até a decisão do município.
          </Alert>
        )}

        <fieldset style={{ border: 'none', padding: 0, margin: 0 }}>
          <legend style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.5rem' }}>Tipo de pedido</legend>
          {info.kinds.map((k) => (
            <label key={k.code} style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start', fontSize: '0.875rem', marginBottom: '0.375rem' }}>
              <input type="radio" name="cancel-kind" value={k.code} checked={kind === k.code}
                disabled={!info.eligible[k.code]} onChange={() => setKind(k.code)} />
              <span>
                {k.label}
                {!info.eligible[k.code] && info.blockers[k.code][0] && (
                  <span style={{ display: 'block', fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>
                    Indisponível: {info.blockers[k.code][0].message}
                  </span>
                )}
              </span>
            </label>
          ))}
          <p style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>{info.substitution_note}</p>
        </fieldset>

        {info.warnings.map((w) => <Alert key={w.code} variant="warning">{w.message}</Alert>)}

        <Select label="Motivo (código do leiaute)" required value={reason}
          onChange={(e) => setReason(e.target.value as '1' | '2' | '9' | '')}
          options={[{ value: '', label: 'Selecione…' }, ...info.reasons.map((r) => ({ value: r.code, label: `${r.code} — ${r.label}` }))]} />
        <Textarea label="Justificativa" required rows={3} maxLength={255} value={text}
          onChange={(e) => setText(e.target.value)}
          error={textErrors.length ? textErrors.join(' ') : undefined}
          helperText={`${text.length}/255 — mínimo 15 caracteres, uma linha.`} />

        <label style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start', fontSize: '0.875rem' }}>
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
          <span>Confirmo que a empresa, o ambiente e a nota acima estão corretos e que o pedido será enviado ao
            Sistema Nacional NFS-e{d.environment === 'mock' ? ' (simulado)' : ''}.</span>
        </label>
        <Input label={`Digite o número da NFS-e (${number}) para confirmar`} value={typed} inputMode="numeric"
          autoComplete="off" onChange={(e) => setTyped(e.target.value)}
          error={typed && typed.trim() !== number ? 'O número não confere.' : undefined} />

        {error && <Alert variant="error" title="Pedido não registrado">{error}</Alert>}
      </div>
    </Modal>
  );
};

export default CancellationDialog;
