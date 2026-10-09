import React, { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Skeleton } from '@ctrl-fiscal/ui';
import { Readiness } from '@ctrl-fiscal/api-client';
import { useFiscal, logSafe } from '../../context/FiscalContext.js';
import { ScreenSection } from '../screen/ScreenSection.js';
import { Notice } from '../screen/Notice.js';

const ENV_LABEL: Record<string, string> = { production: 'Produção', restricted: 'Produção Restrita' };
const OP_LABEL: Record<string, string> = { consulta: 'Consulta (ADN)', emissao: 'Emissão', cancelamento: 'Cancelamento' };
const WS_LABEL: Record<string, string> = { PREPARING: 'Em preparação', ACTIVE: 'Ativo', SUSPENDED: 'Suspenso' };

/** Prontidão do workspace por ambiente × operação: o que falta, exatamente, para cada operação. */
export const WorkspaceReadiness: React.FC = () => {
  const { currentCompany, clients } = useFiscal();
  const [data, setData] = useState<Readiness | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!currentCompany) return;
    const id = currentCompany.id;
    setLoading(true);
    setError(null);
    try {
      const r = await clients.core.getReadiness(id);
      setData(r);
    } catch (err) {
      logSafe('prontidão', err);
      setError('Não foi possível consultar a prontidão do workspace.');
    } finally {
      setLoading(false);
    }
  }, [currentCompany, clients.core]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <ScreenSection
      title="Prontidão do workspace"
      description="Situação por ambiente e operação. Pendências de um workspace não afetam outros. Concluir o cadastro não libera produção: emissão e cancelamento têm controles próprios."
      actions={<Button variant="outline" size="sm" onClick={load} isLoading={loading}>Atualizar</Button>}
    >
      {error && <Notice variant="error" title="Falha na consulta">{error}</Notice>}
      {!data && loading && <Skeleton height="8rem" />}
      {data && (
        <>
          {data.workspace && (
            <div className="pf-actions-row">
              <span className="pf-muted">
                Workspace <strong>{data.workspace.name}</strong> — situação{' '}
                <Badge status={data.workspace.status === 'ACTIVE' ? 'VALID' : 'PROCESSING'}
                  customLabel={WS_LABEL[data.workspace.status] || data.workspace.status} size="sm" />
              </span>
              <span className="pf-help">Conferido em {new Date(data.checked_at).toLocaleString('pt-BR')}</span>
            </div>
          )}
          {data.sections && (
            <div className="pf-secoes">
              {data.sections.map((sec) => (
                <section key={sec.id} className="pf-secao" data-ok={sec.ok ? 'true' : 'false'} aria-label={sec.title}>
                  <div className="pf-ready-op-head">
                    <span>{sec.title}</span>
                    <Badge status={sec.ok ? 'VALID' : 'DIVERGENT'} customLabel={sec.ok ? 'Sem bloqueio' : 'Com pendência'} size="sm" />
                  </div>
                  <ul className="pf-ready-issues">
                    {sec.items.map((i, k) => <li key={`${i.code}-${k}`} data-severity={i.severity}>{i.message}</li>)}
                  </ul>
                </section>
              ))}
            </div>
          )}
          <h3 className="pf-subtitle">Por ambiente e operação</h3>
          {data.environments.map((env) => (
            <section key={env.environment} className="pf-subpanel" aria-label={`Ambiente ${ENV_LABEL[env.environment] || env.environment}`}>
              <div className="pf-actions-row">
                <h3 className="pf-subtitle">{ENV_LABEL[env.environment] || env.environment}</h3>
                <span className="pf-help">
                  Certificado: {env.certificate?.present
                    ? `${env.certificate.subject_cnpj_matches === false ? 'CNPJ divergente' : 'presente'}${env.certificate.not_after ? ` · válido até ${new Date(env.certificate.not_after).toLocaleDateString('pt-BR')}` : ''}`
                    : 'não instalado'}
                </span>
              </div>
              <div className="pf-ready-grid">
                {env.operations.map((op) => (
                  <div key={op.operation} className="pf-ready-op" data-ready={op.ready ? 'true' : undefined}>
                    <div className="pf-ready-op-head">
                      <span>{OP_LABEL[op.operation] || op.operation}</span>
                      <Badge status={op.ready ? 'VALID' : 'DIVERGENT'} customLabel={op.ready ? 'Empresa pronta' : 'Empresa com pendências'} size="sm" />
                    </div>
                    {/* Funcionalidade habilitada na instalação ≠ empresa pronta para usá-la. */}
                    {op.enabled !== undefined && (
                      <span className="pf-help">
                        Funcionalidade: {op.enabled ? 'habilitada na instalação' : 'desligada na instalação'}
                      </span>
                    )}
                    {op.issues.length > 0 ? (
                      <ul className="pf-ready-issues">
                        {op.issues.map((i, k) => (
                          <li key={`${i.code}-${k}`} data-severity={i.severity}>{i.message}</li>
                        ))}
                      </ul>
                    ) : (
                      <span className="pf-help">Nenhuma pendência.</span>
                    )}
                  </div>
                ))}
              </div>
            </section>
          ))}
        </>
      )}
    </ScreenSection>
  );
};
