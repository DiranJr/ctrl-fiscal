import React, { useEffect, useState } from 'react';
import type { FiscalDocument } from '@ctrl-fiscal/api-client';
import { Alert, Button, ErrorState, Icon, Modal, Skeleton } from '@ctrl-fiscal/ui';
import { useFiscal, logSafe } from '../../context/FiscalContext.js';
import { saveBlob } from './saveBlob.js';

export type ViewerMode = 'pdf' | 'xml';
const XML_PREVIEW_MAX = 1_500_000; // caracteres exibidos; acima disso, só download

function problemMessage(err: unknown, fallback: string): string {
  const p = (err as { problem?: { code?: string; detail?: string } })?.problem;
  if (p?.code === 'DANFSE_SIMULATED') return 'Documento de simulação não gera DANFSe.';
  if (p?.code === 'XML_UNAVAILABLE') return 'XML original indisponível para esta nota; o DANFSe é gerado somente a partir dele.';
  return p?.detail || fallback;
}

/**
 * Visualização dentro do sistema:
 * - PDF: DANFSe GERADO PELA PLATAFORMA a partir do XML oficial (NT SE/CGNFS-e 008 v1.02), obtido por requisição
 *   autenticada e exibido por URL local (blob:), sem URL pública e sem token na query string.
 * - XML: texto escapado (renderizado como texto por React), nunca interpretado como HTML/SVG.
 */
const DocumentViewer: React.FC<{ doc: FiscalDocument; mode: ViewerMode; onClose: () => void }> = ({ doc, mode, onClose }) => {
  const { clients, currentCompany } = useFiscal();
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfBlob, setPdfBlob] = useState<Blob | null>(null);
  const [xml, setXml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!currentCompany) return;
    let cancelled = false;
    let created: string | null = null;
    setError(null);
    setPdfUrl(null);
    setXml(null);
    (async () => {
      try {
        if (mode === 'pdf') {
          const blob = await clients.fiscal.getDanfsePdf(currentCompany.id, doc.id);
          if (cancelled) return;
          created = URL.createObjectURL(blob);
          setPdfBlob(blob);
          setPdfUrl(created);
        } else {
          const text = await clients.fiscal.getDocumentXml(currentCompany.id, doc.id);
          if (!cancelled) setXml(text);
        }
      } catch (err) {
        logSafe(mode === 'pdf' ? 'danfse' : 'xml', err);
        if (!cancelled) setError(problemMessage(err, mode === 'pdf' ? 'Falha ao gerar o DANFSe.' : 'Falha ao obter o XML.'));
      }
    })();
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [mode, doc.id, currentCompany, clients.fiscal, attempt]);

  const key = doc.access_key || doc.id;
  const title = mode === 'pdf' ? `DANFSe — NFS-e ${doc.number || ''}` : `XML original — NFS-e ${doc.number || ''}`;

  return (
    <Modal isOpen onClose={onClose} title={title} maxWidth="min(1100px, 96vw)"
      description={mode === 'pdf'
        ? 'PDF gerado pela plataforma a partir do XML oficial, conforme o modelo nacional (NT 008 v1.02). Não é um arquivo baixado do governo.'
        : 'Conteúdo exibido como texto; nada do arquivo é executado.'}
      footer={
        <>
          {mode === 'pdf' && pdfBlob && (
            <Button variant="outline" onClick={() => saveBlob(pdfBlob, `DANFSe_${key}.pdf`)}>
              <Icon name="download" size={16} /> Baixar PDF
            </Button>
          )}
          {mode === 'xml' && xml !== null && (
            <Button variant="outline" onClick={async () => {
              try {
                saveBlob(await clients.fiscal.getDocumentXmlBlob(currentCompany!.id, doc.id), `NFSe_${key}.xml`);
              } catch (err) {
                logSafe('xml', err);
                setError(problemMessage(err, 'Falha ao baixar o XML.'));
              }
            }}>
              <Icon name="download" size={16} /> Baixar XML original
            </Button>
          )}
          <Button variant="primary" onClick={onClose}>Fechar</Button>
        </>
      }>
      {error ? (
        <ErrorState message={error} onRetry={() => setAttempt((a) => a + 1)} />
      ) : mode === 'pdf' ? (
        pdfUrl ? (
          <iframe title={`DANFSe da NFS-e ${doc.number || key}`} src={pdfUrl}
            style={{ width: '100%', height: '72vh', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)' }} />
        ) : (
          <div aria-busy="true" aria-label="Gerando o DANFSe"><Skeleton height="72vh" /></div>
        )
      ) : xml === null ? (
        <div aria-busy="true" aria-label="Carregando o XML"><Skeleton height="50vh" /></div>
      ) : (
        <>
          {xml.length > XML_PREVIEW_MAX && (
            <div style={{ marginBottom: '0.75rem' }}>
              <Alert variant="warning">Arquivo grande: exibidos os primeiros {XML_PREVIEW_MAX.toLocaleString('pt-BR')} caracteres. Use “Baixar XML original” para o conteúdo completo.</Alert>
            </div>
          )}
          <pre className="pf-xml" tabIndex={0} aria-label="Conteúdo do XML original">{xml.slice(0, XML_PREVIEW_MAX)}</pre>
        </>
      )}
    </Modal>
  );
};

export default DocumentViewer;
