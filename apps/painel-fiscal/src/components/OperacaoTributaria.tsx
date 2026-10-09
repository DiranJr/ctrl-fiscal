import React, { useEffect, useState } from 'react';
import { Alert, Badge, Button } from '@ctrl-fiscal/ui';
import type { Draft, ServiceProfile } from '@ctrl-fiscal/api-client';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { OrigemTag } from './workspace/ServiceProfilesSection.js';

const RESOLUCAO: Record<string, string> = {
  REUSED_APPROVED: 'Configuração aprovada reutilizada (operação equivalente).',
  LINKED_PENDING: 'Vinculada a uma configuração equivalente que ainda aguarda revisão.',
  CREATED_VARIANT: 'Nova configuração registrada para revisão (variante desta operação).',
  INACTIVE_EQUIVALENT: 'A configuração equivalente foi inativada.',
  NOT_REGISTERED: 'Ainda não foi possível registrar a configuração.',
};
const CAMPOS: [string, string][] = [
  ['national_code', 'cTribNac'], ['nbs_code', 'cNBS'], ['issqn_taxation', 'tribISSQN'],
  ['pis_cofins_cst', 'CST PIS/COFINS'], ['ibs_cbs', 'cIndOp · CST · cClassTrib'],
];

function valorCampo(p: ServiceProfile, k: string): string {
  if (k === 'ibs_cbs') {
    const i = p.ibs_cbs;
    return i ? `${i.operation_indicator || '—'} · ${i.cst || '—'} · ${i.class_code || '—'}` : '—';
  }
  return ((p as unknown as Record<string, string | null>)[k]) || '—';
}

/**
 * Configuração tributária da OPERAÇÃO do rascunho — a mesma nos dois fluxos ("Usar nota como referência" e "Nova
 * NFS-e do zero"). Escolher uma nota não aprova o tratamento tributário: a configuração fica pendente até a aprovação
 * explícita de quem administra o workspace; operações equivalentes reutilizam a configuração aprovada.
 */
export const OperacaoTributaria: React.FC<{
  draft: Draft | null;
  busy: boolean;
  onRegister: () => Promise<void>;
  onApprove: (p: ServiceProfile) => Promise<void>;
  /** Motivo para bloquear as ações (ex.: alterações não salvas no editor). */
  blockedReason?: string | null;
}> = ({ draft, busy, onRegister, onApprove, blockedReason }) => {
  const { currentCompany, clients } = useFiscal();
  const perms = currentCompany?.permissions || [];
  const canRegister = perms.includes('prepare');
  const canApprove = perms.includes('admin');
  const [profile, setProfile] = useState<ServiceProfile | null>(null);
  const [checked, setChecked] = useState(false);
  const origem = draft?.origin;
  const op = origem?.operation;
  const pid = draft?.service_profile_id || null;

  useEffect(() => {
    setChecked(false);
    if (!currentCompany || !pid) { setProfile(null); return; }
    let vivo = true;
    clients.core.listServiceProfiles(currentCompany.id)
      .then((ps) => { if (vivo) setProfile(ps.find((p) => p.id === pid) || null); })
      .catch((err) => logSafe('configuração da operação', err));
    return () => { vivo = false; };
  }, [currentCompany, clients.core, pid, draft?.service_profile_version]);

  const desatualizada = !!profile && !!draft?.service_profile_version && profile.version !== draft.service_profile_version;
  const bloqueios = (profile?.pending || []).filter((i) => i.severity === 'error');
  const avisos = (profile?.pending || []).filter((i) => i.severity === 'warning');

  return (
    // data-no-dirty: as marcações deste painel não editam o rascunho (o editor não deve tratá-las como alteração).
    <section className="pf-subpanel" aria-label="Configuração tributária da operação" data-no-dirty="">
      <h3 className="pf-subtitle">Configuração tributária da operação</h3>
      <p className="pf-help">
        {origem?.kind === 'document' && origem.document
          ? <>Origem: NFS-e nº <strong>{origem.document.number || '—'}</strong> (competência {origem.document.competence || '—'}),
            dados lidos do XML oficial. Número, chave, protocolo, assinatura, eventos e situação da nota de origem não foram copiados.</>
          : <>Origem: preenchimento manual (Nova NFS-e do zero).</>}
      </p>
      {origem?.kind === 'document' && (
        <details className="pf-evid">
          <summary>O que veio da nota de origem e o que não foi copiado</summary>
          <ul className="pf-bullets">
            {Object.entries(origem.fields || {}).map(([campo, f]) => (
              <li key={campo}>{campo} ← {f.tag || f.origem}{f.confirmar ? ' (classificação da nota de origem: confirme para a nova operação)' : ''}</li>
            ))}
          </ul>
          <p className="pf-help"><strong>Não copiado:</strong></p>
          <ul className="pf-bullets">
            {(origem.not_copied || []).map((n) => <li key={n.campo}>{n.campo}: {n.motivo}</li>)}
          </ul>
        </details>
      )}

      {!draft && <p className="pf-help">Salve o rascunho para registrar a configuração desta operação.</p>}
      {op && <p className="pf-help">{RESOLUCAO[op.resolution] || op.resolution}</p>}
      {op?.resolution === 'NOT_REGISTERED' && (op.errors || []).length > 0 && (
        <Alert variant="warning" title="Corrija para registrar">
          <ul className="pf-bullets">{(op.errors || []).map((e, i) => <li key={i}>{e.field}: {e.message}</li>)}</ul>
        </Alert>
      )}

      {profile && (
        <div className="pf-perfil-op">
          <div className="pf-actions-row">
            <strong>{profile.name}</strong>
            <Badge status={profile.status === 'APPROVED' ? 'VALID' : 'DIVERGENT'} size="sm"
              customLabel={profile.status === 'APPROVED' ? 'Aprovada' : profile.status === 'INACTIVE' ? 'Inativa' : 'Pendente de revisão'} />
            <span className="pf-help">versão {profile.version}</span>
          </div>
          <dl className="pf-dl">
            {CAMPOS.map(([k, rot]) => (
              <React.Fragment key={k}>
                <dt>{rot}</dt>
                <dd>{valorCampo(profile, k)} <OrigemTag src={profile.field_sources?.[k]} /></dd>
              </React.Fragment>
            ))}
          </dl>
          {(op?.divergent_from || []).map((d) => (
            <p key={d.id} className="pf-help">Difere do perfil aprovado “{d.name}” em {d.fields.join(', ')}; o aprovado não foi alterado.</p>
          ))}
          {bloqueios.length > 0 && (
            <div className="pf-perfil-pend" data-severity="error">
              <strong>Impede a aprovação:</strong>
              <ul>{bloqueios.map((i, k) => <li key={k}>{i.label}: {i.message}</li>)}</ul>
            </div>
          )}
          {avisos.length > 0 && (
            <div className="pf-perfil-pend" data-severity="warning">
              <strong>Confira antes de aprovar:</strong>
              <ul>{avisos.map((i, k) => <li key={k}>{i.label}: {i.message}</li>)}</ul>
            </div>
          )}
          {desatualizada && (
            <p className="pf-help">A configuração mudou depois que foi vinculada a este rascunho: registre de novo para revisar a versão atual.</p>
          )}
        </div>
      )}

      {blockedReason && <p className="pf-help">{blockedReason}</p>}
      <div className="pf-actions-row">
        {canRegister && draft?.editable !== false && (
          <Button variant="secondary" isLoading={busy} disabled={!!blockedReason} onClick={() => onRegister()}>
            {pid ? 'Registrar novamente a configuração desta operação' : 'Registrar configuração desta operação'}
          </Button>
        )}
      </div>
      {profile && profile.status === 'PENDING_REVIEW' && (
        canApprove ? (
          profile.can_approve ? (
            <div>
              <label className="pf-confirm">
                <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
                <span>Conferi os códigos desta operação (cTribNac, NBS, tributação do ISSQN, PIS/COFINS, cIndOp, CST e
                  cClassTrib) e aprovo esta configuração a partir de {draft?.competence ? `01/${draft.competence.slice(5)}/${draft.competence.slice(0, 4)}` : 'hoje'}.</span>
              </label>
              <Button variant="primary" disabled={!checked || !!blockedReason} isLoading={busy} onClick={() => onApprove(profile)}>
                Aprovar configuração
              </Button>
            </div>
          ) : <p className="pf-help">Resolva os itens que impedem a aprovação (no rascunho, registrando de novo, ou em Configurações → Perfis de serviço).</p>
        ) : <p className="pf-help">A aprovação é feita por quem administra o workspace (Configurações → Perfis de serviço).</p>
      )}
    </section>
  );
};
