import React, { useEffect } from 'react';
import { Badge } from '@ctrl-fiscal/ui';
import { useFiscal } from '../../context/FiscalContext.js';

const ENV_LABEL: Record<string, string> = { production: 'Produção', restricted: 'Produção Restrita' };

/** Histórico de certificados do workspace — só metadados (impressão digital abreviada, validade, substituição).
 *  O material (PFX e senha) dos certificados substituídos é expurgado no servidor. */
export const CertificateHistory: React.FC<{ reloadKey?: number }> = ({ reloadKey = 0 }) => {
  const { certificateStatus, refreshCertificateStatus } = useFiscal();

  useEffect(() => {
    refreshCertificateStatus();
  }, [reloadKey, refreshCertificateStatus]);

  const rows = certificateStatus?.history || [];
  if (rows.length === 0) {
    return <p className="pf-help">Nenhum certificado registrado neste workspace.</p>;
  }
  const fmt = (v?: string | null) => (v ? new Date(v).toLocaleDateString('pt-BR') : '—');
  return (
    <section className="pf-subpanel" aria-labelledby="cert-hist-title">
      <h3 id="cert-hist-title" className="pf-subtitle">Histórico de certificados</h3>
      <p className="pf-help">
        Somente um certificado fica ativo por workspace. A renovação mantém o workspace, o cadastro, a numeração e o
        histórico; o arquivo e a senha do certificado substituído são descartados.
      </p>
      <div className="ui-table-wrap" style={{ overflowX: 'auto' }}>
        <table className="ui-table" style={{ width: '100%', fontSize: '0.8125rem' }}>
          <thead>
            <tr>
              <th scope="col">Situação</th>
              <th scope="col">Ambiente</th>
              <th scope="col">Impressão digital</th>
              <th scope="col">Validade</th>
              <th scope="col">Instalado em</th>
              <th scope="col">Substituído em</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.fingerprint_sha256}-${r.environment}-${i}`}>
                <td><Badge status={r.is_active ? 'VALID' : 'WITHDRAWN'} customLabel={r.is_active ? 'Ativo' : 'Substituído'} size="sm" /></td>
                <td>{ENV_LABEL[r.environment] || r.environment}</td>
                <td><span className="ui-mono" title={r.fingerprint_sha256}>{r.fingerprint_sha256.slice(0, 12)}…</span></td>
                <td>{fmt(r.not_before)} a {fmt(r.not_after)}</td>
                <td>{fmt(r.created_at)}</td>
                <td>{r.replaced_at ? fmt(r.replaced_at) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
};
