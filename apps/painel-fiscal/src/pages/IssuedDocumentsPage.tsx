import React, { useRef, useState } from 'react';
import { FiscalDocument, RecipientSuggestion } from '@ctrl-fiscal/api-client';
import { Alert, Button, Icon, Modal } from '@ctrl-fiscal/ui';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { DocumentsWorkspace } from '../components/documents/DocumentsWorkspace.js';

type Problem = { status?: number; code?: string; detail?: string; trace_id?: string; errors?: { field?: string; message?: string }[] };

function problemOf(err: unknown): Problem {
  return ((err as { problem?: Problem })?.problem) || {};
}

/** Mensagem compreensível; em falha inesperada (5xx/rede) inclui o identificador de correlação. */
function friendly(err: unknown, fallback: string): string {
  const p = problemOf(err);
  if (p.status && p.status < 500 && p.detail) return p.detail;
  return `${fallback}${p.trace_id ? ` (código para suporte: ${p.trace_id})` : ''}`;
}

function newKey(prefix: string): string {
  const r = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : Math.random().toString(16).slice(2);
  return `${prefix}-${r}`;
}

const MISSING_LABEL: Record<string, string> = {
  document: 'CNPJ/CPF', name: 'nome', municipality_code: 'município (IBGE)', postal_code: 'CEP',
  'address.street': 'logradouro', 'address.number': 'número', 'address.district': 'bairro',
};

/**
 * "Usar como referência": cria um rascunho NOVO a partir da nota (sem número, chave, protocolo, assinatura, eventos
 * ou situação fiscal; a nota original não muda). Uma chave de idempotência por ação: clique repetido ou nova
 * tentativa após timeout devolvem o MESMO rascunho. Se o tomador ainda não estiver em Clientes, mostra os dados
 * lidos do XML oficial para o usuário conferir e cadastrar (nada é inventado); depois abre o rascunho no editor.
 */
const DuplicateAction: React.FC<{ doc: FiscalDocument; onNavigate: (r: string) => void }> = ({ doc, onNavigate }) => {
  const { clients, currentCompany, showToast, refreshCounters } = useFiscal();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recipient, setRecipient] = useState<RecipientSuggestion | null>(null);
  const [registering, setRegistering] = useState(false);
  const key = useRef(newKey(`ref-${doc.id}`));

  const openDraft = async (draftId: string) => {
    showToast('Rascunho criado a partir desta nota. Revise as pendências antes de agendar.', 'success');
    await refreshCounters();
    onNavigate('rascunhos');
    const url = new URL(window.location.href);
    url.searchParams.set('draft', draftId);
    window.history.replaceState({}, '', url.toString());
  };

  const create = async () => {
    if (!currentCompany || busy) return;
    setBusy(true);
    setError(null);
    try {
      const d = await clients.fiscal.createDraftFromDocument(currentCompany.id, { source_document_id: doc.id }, key.current);
      await openDraft(d.id);
    } catch (err) {
      logSafe('duplicar', err);
      if (problemOf(err).code === 'CUSTOMER_NOT_LINKED') {
        try {
          setRecipient(await clients.fiscal.getDocumentRecipient(currentCompany.id, doc.id));
        } catch (err2) {
          logSafe('tomador', err2);
          setError(friendly(err2, 'Não foi possível ler o tomador desta nota.'));
        }
      } else {
        setError(friendly(err, 'Não foi possível criar o rascunho a partir desta nota.'));
      }
    } finally {
      setBusy(false);
    }
  };

  const registerAndCreate = async () => {
    if (!currentCompany || !recipient || !recipient.document || !recipient.document_type || !recipient.name) return;
    setRegistering(true);
    setError(null);
    try {
      const a = recipient.address;
      const hasAddress = !!(a.street || a.number || a.complement || a.district);
      await clients.core.createCustomer(currentCompany.id, {
        name: recipient.name, document_type: recipient.document_type, document: recipient.document,
        email: recipient.email, phone: recipient.phone, municipal_registration: recipient.municipal_registration,
        municipality_code: recipient.municipality_code, postal_code: recipient.postal_code,
        address: hasAddress ? { street: a.street, number: a.number, complement: a.complement, district: a.district } : null,
      });
      setRecipient(null);
      await create();
    } catch (err) {
      logSafe('cadastrar-tomador', err);
      const p = problemOf(err);
      const fields = (p.errors || []).map((e) => e.message).filter(Boolean).join(' ');
      setError(`${friendly(err, 'Não foi possível cadastrar o tomador.')} ${fields}`.trim()
        + ' Você pode cadastrá-lo manualmente em Clientes e tentar de novo.');
    } finally {
      setRegistering(false);
    }
  };

  return (
    <section aria-label="Usar como referência" style={{ borderTop: '1px solid var(--color-border)', paddingTop: '0.75rem' }}>
      <p style={{ fontSize: '0.8125rem', color: 'var(--color-text-muted)', marginBottom: '0.5rem' }}>
        Cria um rascunho novo com cliente, serviço e valores desta nota. Número, chave, eventos e situação fiscal não são
        copiados, e esta nota não é alterada.
      </p>
      <Button variant="secondary" isLoading={busy} disabled={busy || registering} onClick={create}>
        <Icon name="draft" size={16} /> Usar como referência para novo rascunho
      </Button>
      {error && <div style={{ marginTop: '0.5rem' }}><Alert variant="error">{error}</Alert></div>}
      {recipient && (
        <Modal isOpen onClose={() => !registering && setRecipient(null)} title="Tomador ainda não cadastrado"
          description="Os dados abaixo foram lidos do XML oficial desta nota. Confira; o cadastro fica em Clientes e pode ser editado depois."
          footer={
            <>
              <Button variant="outline" onClick={() => setRecipient(null)} disabled={registering}>Cancelar</Button>
              <Button variant="primary" onClick={registerAndCreate} isLoading={registering}
                disabled={registering || !recipient.document || !recipient.name || recipient.foreign}>
                Cadastrar tomador e criar rascunho
              </Button>
            </>
          }>
          <dl className="pf-dl">
            <dt>{recipient.document_type || 'Documento'}</dt><dd className="ui-mono">{recipient.document || '—'}</dd>
            <dt>Nome</dt><dd>{recipient.name || '—'}</dd>
            <dt>Inscrição municipal</dt><dd>{recipient.municipal_registration || '—'}</dd>
            <dt>Município (IBGE)</dt><dd>{recipient.municipality_code || '—'}</dd>
            <dt>CEP</dt><dd>{recipient.postal_code || '—'}</dd>
            <dt>Endereço</dt>
            <dd>{[recipient.address.street, recipient.address.number, recipient.address.complement, recipient.address.district]
              .filter(Boolean).join(', ') || '—'}</dd>
            <dt>E-mail / telefone</dt><dd>{[recipient.email, recipient.phone].filter(Boolean).join(' / ') || '—'}</dd>
          </dl>
          {recipient.foreign && (
            <Alert variant="warning">Tomador do exterior: cadastre-o manualmente em Clientes.</Alert>
          )}
          {recipient.missing.length > 0 && (
            <Alert variant="warning">
              Não constam no XML: {recipient.missing.map((m) => MISSING_LABEL[m] || m).join(', ')}. O rascunho será criado
              com essas pendências para preenchimento.
            </Alert>
          )}
        </Modal>
      )}
    </section>
  );
};

export const IssuedDocumentsPage: React.FC<{ onNavigate: (route: string) => void }> = ({ onNavigate }) => {
  const { currentCompany } = useFiscal();
  return (
    <DocumentsWorkspace
      direction="ISSUED"
      title="Notas emitidas"
      description={<>NFS-e de serviços prestados por <strong>{currentCompany?.name}</strong></>}
      counterpartLabel="Tomador"
      primaryAction={<Button variant="primary" onClick={() => onNavigate('nova-nfse')}><Icon name="plus" size={16} /> Nova NFS-e</Button>}
      renderDetailExtras={(doc) => <DuplicateAction key={doc.id} doc={doc} onNavigate={onNavigate} />}
    />
  );
};
