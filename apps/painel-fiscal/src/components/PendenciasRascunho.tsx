import React from 'react';
import { Button } from '@ctrl-fiscal/ui';
import { ValidationIssue } from '@ctrl-fiscal/api-client';
import { Notice } from './screen/Notice.js';

/** Pendências da validação do rascunho, como o servidor as devolve (nada é resumido a uma contagem nem reescrito). */
export interface PendenciasRascunhoProps {
  issues: ValidationIssue[];
  companyId?: string | null;
  /** Versão a que as pendências se referem e a versão atual do rascunho. */
  validatedVersion?: number | null;
  currentVersion?: number | null;
  validatedAt?: string | null;
  /** O rascunho foi validado alguma vez? (sem validação não há o que listar). */
  validated?: boolean;
  /** Editor com alterações ainda não salvas. */
  dirty?: boolean;
  /** Ir ao campo do rascunho (só no editor). */
  onGoToField?: (field: string, label?: string | null) => void;
  /** Validar de novo (versão salva). */
  onRevalidate?: () => void;
  isRevalidating?: boolean;
  /** Falha da chamada de validação (mantém o que já se sabia). */
  error?: string | null;
  compact?: boolean;
}

const GRUPOS: { sev: ValidationIssue['severity']; titulo: string; dica: string }[] = [
  { sev: 'error', titulo: 'Bloqueios', dica: 'impedem emitir ou agendar' },
  { sev: 'warning', titulo: 'Avisos', dica: 'não impedem, mas confira' },
  { sev: 'info', titulo: 'Informações', dica: '' },
];

function linkCadastro(i: ValidationIssue, companyId?: string | null): { href: string; texto: string } | null {
  const t = i.target;
  if (!t || !companyId) return null;
  const base = `/fiscal/?company_id=${encodeURIComponent(companyId)}`;
  switch (t.kind) {
    case 'customer':
      return { href: `${base}&view=clientes${t.customer_id ? `&customer_id=${encodeURIComponent(t.customer_id)}` : ''}`,
        texto: 'Abrir cadastro do cliente' };
    case 'service_profile':
      return { href: `${base}&view=configuracoes`, texto: 'Abrir perfis de serviço' };
    case 'company':
      return { href: `${base}&view=configuracoes`, texto: 'Abrir cadastro da empresa' };
    case 'certificate':
      return { href: `${base}&view=configuracoes`, texto: 'Abrir certificado digital' };
    default:
      return null;
  }
}

const Item: React.FC<{ i: ValidationIssue; companyId?: string | null; onGoToField?: PendenciasRascunhoProps['onGoToField'] }> = ({
  i, companyId, onGoToField,
}) => {
  const link = linkCadastro(i, companyId);
  const campo = i.target?.kind === 'draft_field' ? i.target.field : null;
  return (
    <li className="pf-pend-item" data-severity={i.severity}>
      <div className="pf-pend-onde">
        <span className="pf-pend-tag">{i.severity === 'error' ? 'Bloqueio' : i.severity === 'warning' ? 'Aviso' : 'Informação'}</span>
        {' '}<strong>{i.section || 'Rascunho'}</strong>{i.label ? <> › {i.label}</> : null}
      </div>
      <p className="pf-pend-msg">{i.message}</p>
      {i.reason ? (
        <p className="pf-pend-motivo"><strong>Motivo (texto oficial):</strong> {i.reason}</p>
      ) : null}
      {i.reason_source && <p className="pf-help pf-pend-fonte">Origem: {i.reason_source}</p>}
      {i.action && (
        <p className="pf-pend-acao">
          <strong>{i.severity === 'error' ? 'Como corrigir:' : i.severity === 'warning' ? 'O que conferir:' : 'Orientação:'}</strong> {i.action}
        </p>
      )}
      {(campo && onGoToField) || link ? (
        <div className="pf-pend-botoes">
          {campo && onGoToField && (
            <Button size="sm" variant="outline" onClick={() => onGoToField(campo, i.label)}>Ir ao campo</Button>
          )}
          {link && (
            <a className="pf-pend-link" href={link.href} target="_blank" rel="noopener noreferrer">
              {link.texto} (nova aba) ↗
            </a>
          )}
        </div>
      ) : null}
      <details className="pf-pend-tecnico">
        <summary>Detalhes técnicos</summary>
        <dl className="pf-dl">
          <dt>Código</dt><dd><code>{i.code}</code></dd>
          {i.rule && <><dt>Regra</dt><dd><code>{i.rule}</code></dd></>}
          <dt>Campo</dt><dd><code>{i.field}</code></dd>
          {i.xml_path && <><dt>Caminho XML</dt><dd><code>{i.xml_path}</code></dd></>}
          {i.official_message && <><dt>Mensagem oficial</dt><dd>{i.official_message}</dd></>}
        </dl>
      </details>
    </li>
  );
};

export const PendenciasRascunho: React.FC<PendenciasRascunhoProps> = ({
  issues, companyId, validatedVersion, currentVersion, validatedAt, validated = true, dirty, onGoToField,
  onRevalidate, isRevalidating, error, compact,
}) => {
  const versaoMudou = validatedVersion != null && currentVersion != null && validatedVersion !== currentVersion;
  const desatualizada = !!dirty || versaoMudou;
  const nunca = !validated && validatedVersion == null;
  const contagem = (s: string) => issues.filter((i) => i.severity === s).length;
  return (
    <section className="pf-pend" aria-label="Pendências da validação" data-compact={compact ? 'true' : undefined}>
      <div className="pf-pend-cabecalho">
        <div>
          <strong>Pendências da validação</strong>
          {validatedVersion != null && (
            <span className="pf-help">
              {' '}· validado na versão v{validatedVersion}
              {validatedAt ? ` em ${new Date(validatedAt).toLocaleString('pt-BR')}` : ''}
              {currentVersion != null ? ` · versão atual v${currentVersion}` : ''}
            </span>
          )}
          {!nunca && !versaoMudou && (
            <div className="pf-help" aria-live="polite">
              {contagem('error')} bloqueio(s) · {contagem('warning')} aviso(s) · {contagem('info')} informação(ões)
            </div>
          )}
        </div>
        {onRevalidate && (
          <Button size="sm" variant="secondary" onClick={onRevalidate} isLoading={isRevalidating} disabled={!!dirty}
            title={dirty ? 'Salve o rascunho antes de validar.' : undefined}>
            {nunca ? 'Validar' : 'Validar novamente'}
          </Button>
        )}
      </div>
      {error && <Notice variant="error" title="Não foi possível validar agora">{error} As pendências abaixo são da última validação concluída.</Notice>}
      {nunca ? (
        <p className="pf-help">Este rascunho ainda não foi validado. Valide para ver as pendências apontadas pelo servidor.</p>
      ) : versaoMudou ? (
        <Notice variant="warning" title="Nova validação necessária">
          O rascunho foi alterado depois da validação (validado na v{validatedVersion}; atual v{currentVersion}). As
          pendências da versão anterior não valem para o conteúdo atual: valide de novo.
        </Notice>
      ) : issues.length === 0 ? (
        dirty ? (
          <Notice variant="warning" title="Nova validação necessária">Há alterações não salvas: salve o rascunho e valide de novo.</Notice>
        ) : (

        <Notice variant="success" title="Nenhuma pendência">A validação local não encontrou pendências nesta versão.</Notice>
        )
      ) : (
        <>
        {dirty && (
          <Notice variant="warning" title="Nova validação necessária">
            Há alterações não salvas. A lista abaixo é da versão salva v{validatedVersion}: salve e valide de novo.
          </Notice>
        )}
        {GRUPOS.filter((g) => contagem(g.sev) > 0).map((g) => (
          <div key={g.sev} className="pf-pend-grupo" data-severity={g.sev}>
            <h4 className="pf-pend-titulo">{g.titulo} ({contagem(g.sev)}){g.dica ? <span className="pf-help"> — {g.dica}</span> : null}</h4>
            <ul className="pf-pend-lista">
              {issues.filter((i) => i.severity === g.sev).map((i, k) => (
                <Item key={`${i.code}-${i.field}-${k}`} i={i} companyId={companyId} onGoToField={onGoToField} />
              ))}
            </ul>
          </div>
        ))}
        </>
      )}
    </section>
  );
};
