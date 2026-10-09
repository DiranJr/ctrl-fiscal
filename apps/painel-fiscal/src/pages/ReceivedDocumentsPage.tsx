import React, { useEffect, useState } from 'react';
import { FiscalDocument, ProblemDetailsException, ReviewStatus } from '@ctrl-fiscal/api-client';
import { Alert, Button, Select, Textarea } from '@ctrl-fiscal/ui';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { DocumentsWorkspace } from '../components/documents/DocumentsWorkspace.js';

/** Conferência do serviço tomado: PATCH com expected_version (409 = alterado por outra pessoa/sincronização). */
const ReviewForm: React.FC<{ doc: FiscalDocument; reload: () => void; replaceDoc: (d: FiscalDocument) => void }> = ({ doc, reload, replaceDoc }) => {
  const { clients, currentCompany, showToast, refreshCounters } = useFiscal();
  const [status, setStatus] = useState<ReviewStatus>(doc.review_status && doc.review_status !== 'PENDING' ? doc.review_status : 'REVIEWED');
  const [note, setNote] = useState(doc.review_note || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStatus(doc.review_status && doc.review_status !== 'PENDING' ? doc.review_status : 'REVIEWED');
    setNote(doc.review_note || '');
    setError(null);
  }, [doc.id, doc.version]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <form aria-label="Conferência" style={{ borderTop: '1px solid var(--color-border)', paddingTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}
      onSubmit={async (e) => {
        e.preventDefault();
        if (!currentCompany) return;
        setBusy(true);
        setError(null);
        try {
          const updated = await clients.fiscal.reviewDocument(currentCompany.id, doc.id, {
            review_status: status, note: note.trim() || undefined, expected_version: doc.version,
          });
          replaceDoc(updated);
          showToast('Conferência registrada.', 'success');
          reload();
          await refreshCounters();
        } catch (err) {
          logSafe('conferência', err);
          setError(err instanceof ProblemDetailsException && err.problem.status === 409
            ? 'Esta nota foi alterada por outra pessoa ou pela sincronização. Feche e abra novamente para ver a versão atual; o que você digitou continua aqui.'
            : 'Não foi possível salvar a conferência. Verifique a conexão; o que você digitou continua aqui.');
        } finally {
          setBusy(false);
        }
      }}>
      <h3 style={{ fontSize: '0.875rem' }}>Conferência</h3>
      <Select label="Resultado" value={status} onChange={(e) => setStatus(e.target.value as ReviewStatus)}
        options={[{ value: 'REVIEWED', label: 'Conferida' }, { value: 'DIVERGENT', label: 'Divergente' }, { value: 'PENDING', label: 'Pendente' }]} />
      <Textarea label="Observação" value={note} onChange={(e) => setNote(e.target.value)} rows={3} maxLength={1000} />
      {error && <Alert variant="error">{error}</Alert>}
      <div><Button type="submit" variant="primary" isLoading={busy}>Salvar conferência</Button></div>
    </form>
  );
};

export const ReceivedDocumentsPage: React.FC<{ onNavigate: (route: string) => void }> = () => {
  const { currentCompany } = useFiscal();
  return (
    <DocumentsWorkspace
      direction="RECEIVED"
      title="Notas recebidas"
      description={<>NFS-e de serviços tomados por <strong>{currentCompany?.name}</strong></>}
      counterpartLabel="Prestador"
      showReviewColumn
      renderDetailExtras={(doc, reload, replaceDoc) => <ReviewForm doc={doc} reload={reload} replaceDoc={replaceDoc} />}
    />
  );
};
