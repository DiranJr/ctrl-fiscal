import React, { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Input, Modal, Select, Textarea } from '@ctrl-fiscal/ui';
import {
  FieldSource, ProblemDetailsException, ProfileEvidence, ProfileProposal, ProfileSuggestion, ServiceProfile,
  ServiceProfileInput, ServiceProfileVersionOut, TaxClassification,
} from '@ctrl-fiscal/api-client';
import { useFiscal, logSafe } from '../../context/FiscalContext.js';
import { ScreenSection, TableEmpty } from '../screen/ScreenSection.js';
import { Notice } from '../screen/Notice.js';

const ISSQN: Record<string, string> = { TAXABLE: 'Tributável', IMMUNE: 'Imunidade', EXPORT: 'Exportação', NOT_INCIDENT: 'Não incidência' };
const STATUS: Record<string, { label: string; badge: string }> = {
  PENDING_REVIEW: { label: 'Pendente de revisão', badge: 'PROCESSING' },
  APPROVED: { label: 'Aprovado', badge: 'VALID' },
  INACTIVE: { label: 'Inativo', badge: 'WITHDRAWN' },
};
/** Origem do dado — diferenciada visualmente (texto + cor + ícone, nunca só cor). */
const ORIGEM: Record<string, { label: string; className: string; title: string }> = {
  xml: { label: 'XML oficial', className: 'pf-origem pf-origem-xml', title: 'Lido de forma consistente dos XMLs oficiais das notas emitidas.' },
  xml_historico: { label: 'Nota anterior', className: 'pf-origem pf-origem-hist', title: 'Usado em notas anteriores: evidência histórica, não garante o enquadramento de nova operação.' },
  tabela: { label: 'Tabela oficial', className: 'pf-origem pf-origem-tabela', title: 'Sugestão baseada em tabela oficial.' },
  usuario: { label: 'Informado pelo usuário', className: 'pf-origem pf-origem-usuario', title: 'Preenchido ou alterado por pessoa autorizada.' },
};
const ACOES: Record<string, string> = {
  prepared: 'Preparado das notas', created: 'Criado', updated: 'Editado', approved: 'Aprovado', inactivated: 'Inativado',
  reactivated: 'Reativado (volta à revisão)', migrated: 'Migrado',
  variant_prepared: 'Variante registrada a partir de um rascunho',
  proposal_registered: 'Proposta de revisão registrada (campos não alterados)',
  proposal_applied: 'Alternativa da proposta aplicada (continua pendente de revisão)',
  proposal_discarded: 'Proposta de revisão descartada',
};
const ehProposta = (s: ProfileSuggestion | ProfileProposal): s is ProfileProposal => s.tipo === 'proposta_revisao';
const PROPOSTA_STATUS: Record<string, { label: string; badge: string }> = {
  ABERTA: { label: 'Em aberto', badge: 'PROCESSING' },
  APLICADA: { label: 'Aplicada', badge: 'VALID' },
  DESCARTADA: { label: 'Descartada', badge: 'WITHDRAWN' },
};
/** Seções da análise registrada com a proposta (análise das notas e das tabelas oficiais). */
const ANALISE: [string, string][] = [
  ['servico', 'Serviço descrito nas notas'], ['variacoes', 'Variações encontradas'],
  ['codigos', 'cTribNac e NBS atuais'], ['incompatibilidades', 'Incompatibilidades'],
  ['historico', 'Dados históricos das notas (não confirmados)'], ['confirmado', 'Configuração confirmada'],
  ['obra', 'Obra ou imóvel'], ['por_emissao', 'Dados próprios de cada emissão (não entram no perfil)'],
  ['pendencias', 'Pendências'],
];
const fmtCampos = (c: ProfileProposal['alternativas'][number]['campos']) => [
  c.national_code && `cTribNac ${c.national_code}`, c.nbs_code && `NBS ${c.nbs_code}`,
  c.issqn_taxation && `ISSQN ${ISSQN[c.issqn_taxation] || c.issqn_taxation}`,
  c.pis_cofins_cst && `CST PIS/COFINS ${c.pis_cofins_cst}`,
  c.ibs_cbs && `cIndOp ${c.ibs_cbs.operation_indicator} · CST ${c.ibs_cbs.cst} · cClassTrib ${c.ibs_cbs.class_code}`,
].filter(Boolean).join(' · ');

const PropostaRevisao: React.FC<{
  prop: ProfileProposal; canEdit: boolean; onResolve: (prop: ProfileProposal, decision: 'APPLY' | 'DISCARD', alt?: string) => void;
}> = ({ prop, canEdit, onResolve }) => {
  const st = PROPOSTA_STATUS[prop.status] || { label: prop.status, badge: 'PROCESSING' };
  const aberta = prop.status === 'ABERTA';
  const lista = (v: unknown) => (Array.isArray(v) ? v : v ? [v] : []) as string[];
  return (
    <div className="pf-perfil-pend" data-severity={aberta ? 'warning' : 'info'}>
      <div><strong>Proposta de revisão {prop.id}</strong> <Badge status={st.badge} customLabel={st.label} size="sm" />
        {prop.alternativa_aplicada && <span className="pf-help"> · alternativa {prop.alternativa_aplicada}</span>}</div>
      <p>{prop.resumo}</p>
      {prop.alternativas.length === 0 ? (
        <p className="pf-help">Nenhuma alternativa de classificação registrada: o enquadramento depende das respostas abaixo e não é presumido.</p>
      ) : (
        <ol className="pf-bullets">
          {prop.alternativas.map((a) => (
            <li key={a.id}>
              <strong>{a.id}. {a.rotulo}</strong> — {fmtCampos(a.campos)}
              <div className="pf-help">Condição: {a.condicao}</div>
              <div className="pf-help">Fundamento: {a.fundamento.join('; ')}</div>
              {canEdit && aberta && (
                <Button variant="outline" size="sm" onClick={() => onResolve(prop, 'APPLY', a.id)}>Aplicar alternativa {a.id}</Button>
              )}
            </li>
          ))}
        </ol>
      )}
      {prop.perguntas.length > 0 && (
        <>
          <strong>Perguntas a responder</strong>
          <ul className="pf-bullets">
            {prop.perguntas.map((q) => <li key={q.id}><strong>{q.id}</strong> ({q.responsavel}): {q.texto}</li>)}
          </ul>
        </>
      )}
      <details className="pf-perfil-info">
        <summary>Análise das notas e tabelas</summary>
        {ANALISE.filter(([k]) => prop.analise?.[k]).map(([k, label]) => (
          <div key={k}>
            <strong>{label}</strong>
            <ul className="pf-bullets">{lista(prop.analise[k]).map((x, i) => <li key={i}>{x}</li>)}</ul>
          </div>
        ))}
      </details>
      <div className="pf-help">Próxima ação: {prop.proxima_acao}</div>
      {prop.nota && <div className="pf-help">Resolução: {prop.nota}</div>}
      {canEdit && aberta && (
        <div className="pf-actions-end">
          <Button variant="ghost" size="sm" onClick={() => onResolve(prop, 'DISCARD')}>Descartar proposta</Button>
        </div>
      )}
    </div>
  );
};

export const OrigemTag: React.FC<{ src?: FieldSource }> = ({ src }) => {
  if (!src) return null;
  const o = ORIGEM[src.origem] || { label: src.origem, className: 'pf-origem', title: '' };
  return (
    <span className={o.className} title={src.nota || o.title}>
      {o.label}{src.confirmado_por ? ' · confirmado' : ''}
    </span>
  );
};

const fmtCtn = (c?: string | null) => (c && c.length === 6 ? `${c.slice(0, 2)}.${c.slice(2, 4)}.${c.slice(4)}` : c || '—');
const fmtData = (v?: string | null) => (v ? new Date(v.length === 10 ? `${v}T12:00:00` : v).toLocaleDateString('pt-BR') : '—');

interface FormState {
  name: string; national_code: string; municipal_code: string; nbs_code: string; description: string;
  issqn_taxation: string; pis_cofins_cst: string; operation_indicator: string; class_code: string; cst: string;
  personal_use: string; reg_class_code: string; reg_cst: string; source: string; valid_from: string; valid_to: string;
}
const EMPTY: FormState = {
  name: '', national_code: '', municipal_code: '', nbs_code: '', description: '', issqn_taxation: '', pis_cofins_cst: '',
  operation_indicator: '', class_code: '', cst: '', personal_use: '', reg_class_code: '', reg_cst: '', source: '',
  valid_from: '', valid_to: '',
};

function toForm(p: ServiceProfile): FormState {
  return {
    name: p.name, national_code: p.national_code, municipal_code: p.municipal_code || '', nbs_code: p.nbs_code || '',
    description: p.description || '', issqn_taxation: p.issqn_taxation || '', pis_cofins_cst: p.pis_cofins_cst || '',
    operation_indicator: p.ibs_cbs?.operation_indicator || '', class_code: p.ibs_cbs?.class_code || '',
    cst: p.ibs_cbs?.cst || '', personal_use: p.ibs_cbs?.personal_use || '',
    reg_class_code: p.ibs_cbs?.regular?.class_code || '', reg_cst: p.ibs_cbs?.regular?.cst || '',
    source: p.source || '', valid_from: p.valid_from || '', valid_to: p.valid_to || '',
  };
}

function toPayload(f: FormState): ServiceProfileInput {
  const blank = (v: string) => (v.trim() ? v.trim() : null);
  const ibs = f.operation_indicator || f.class_code
    ? { operation_indicator: f.operation_indicator, cst: f.cst, class_code: f.class_code,
        personal_use: (f.personal_use || null) as '0' | '1' | null,
        regular: f.reg_class_code ? { cst: f.reg_cst, class_code: f.reg_class_code } : null }
    : null;
  return {
    name: f.name.trim(), national_code: f.national_code.trim(), municipal_code: blank(f.municipal_code),
    nbs_code: blank(f.nbs_code), description: blank(f.description),
    issqn_taxation: (f.issqn_taxation || null) as ServiceProfileInput['issqn_taxation'],
    pis_cofins_cst: blank(f.pis_cofins_cst), ibs_cbs: ibs, source: blank(f.source),
    valid_from: blank(f.valid_from), valid_to: blank(f.valid_to),
  };
}

/** Evidência das notas emitidas que sustentam o perfil (tudo lido do XML oficial). */
const Evidencia: React.FC<{ p: ServiceProfile }> = ({ p }) => {
  const ev = p.evidence;
  if (!ev) return <p className="pf-help">Perfil manual: sem notas de referência.</p>;
  if ('tipo' in ev && ev.tipo === 'variante_operacao') {
    // Variante de operação: nasce de UM rascunho (nota de referência ou preenchimento manual), nunca aprovada sozinha.
    const doc = ev.origem.document;
    return (
      <div className="pf-evid">
        <p className="pf-help">
          {ev.origem.kind === 'document' && doc
            ? <>Variante a partir da NFS-e nº <strong>{doc.number || '—'}</strong> (competência {doc.competence || '—'}), lida do XML oficial.</>
            : <>Operação preenchida manualmente em um rascunho de Nova NFS-e.</>}
        </p>
        {ev.diverge_de.length > 0 && (
          <ul className="pf-bullets">
            {ev.diverge_de.map((x) => (
              <li key={x.id}>Difere do perfil aprovado “{x.name}” em: {x.fields.join(', ')} (o aprovado não foi alterado).</li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  const e = ev as ProfileEvidence;
  const sit = Object.entries(e.situacao).map(([k, v]) => `${v} ${k === 'AUTHORIZED' ? 'autorizada(s)' : k === 'CANCELLED' ? 'cancelada(s)' : k}`).join(', ');
  return (
    <details className="pf-evid">
      <summary>
        Notas de referência: <strong>{e.total_notas}</strong> ({sit}) · competências {e.periodo.de || '—'} a {e.periodo.ate || '—'}
        <span className="pf-origem pf-origem-xml" style={{ marginLeft: '0.375rem' }}>XML oficial</span>
      </summary>
      <dl className="pf-dl pf-evid-dl">
        <dt>Descrição oficial do código (xTribNac)</dt><dd>{e.descricao_cTribNac || '—'}</dd>
        <dt>Descrição NBS (xNBS)</dt><dd>{e.descricao_cNBS || '—'}</dd>
        <dt>Descrições usadas nas notas</dt>
        <dd>{e.descricoes_usadas.map((d, i) => <div key={i}>“{d.texto}” — {d.notas} nota(s)</div>)}</dd>
        <dt>Município da prestação → incidência</dt>
        <dd>{e.locais.map((l, i) => <div key={i}>{l.prestacao || l.cLocPrestacao} → {l.incidencia || l.cLocIncid} — {l.notas}</div>)}</dd>
        <dt>ISS</dt>
        <dd>{e.iss.map((x, i) => <div key={i}>{x.tributacao} · {x.retencao} · alíquota aplicada {x.aliquota_aplicada || '—'}% — {x.notas}</div>)}</dd>
        <dt>PIS/COFINS (CST · tpRetPisCofins)</dt>
        <dd>{e.pis_cofins.map((x, i) => <div key={i}>{x.CST || 'não informado'} · {x.tpRetPisCofins ?? '—'} — {x.notas}</div>)}</dd>
        <dt>Retenções federais</dt>
        <dd>INSS em {e.retencoes_federais.INSS} · IRRF em {e.retencoes_federais.IRRF} · CSLL/PIS/COFINS em {e.retencoes_federais.CSLL_PIS_COFINS} nota(s)</dd>
        <dt>cTribMun</dt><dd>{e.cTribMun.map((x) => `${x.valor || 'não informado'} (${x.notas})`).join(' · ')}</dd>
        <dt>Grupo obra</dt><dd>{Object.entries(e.obra).map(([k, v]) => `${k === 'ausente' ? 'sem obra' : k} (${v})`).join(' · ')}</dd>
        <dt>IBS/CBS nas notas</dt>
        <dd>
          {e.ibs_cbs.length === 0 ? 'Nenhuma nota com o grupo IBS/CBS.' : e.ibs_cbs.map((x, i) => (
            <div key={i}>cIndOp {x.cIndOp} ({x.cIndOp_descricao || '—'}) · CST {x.CST} · cClassTrib {x.cClassTrib} — {x.notas} nota(s) de {x.de} a {x.ate}</div>
          ))}
          {e.ibs_cbs_ausente > 0 && <div className="pf-help">{e.ibs_cbs_ausente} nota(s) sem o grupo IBS/CBS.</div>}
        </dd>
        <dt>Exemplos</dt>
        <dd className="pf-chips-inline">
          {e.exemplos.map((x) => (
            <a key={x.document_id} href={`?view=emitidas&doc=${x.document_id}`} title={x.chave}>
              nº {x.numero} ({x.competencia}{x.situacao === 'CANCELLED' ? ', cancelada' : ''})
            </a>
          ))}
        </dd>
      </dl>
    </details>
  );
};

export const ServiceProfilesSection: React.FC = () => {
  const { currentCompany, clients, showToast, user } = useFiscal();
  const canEdit = !!currentCompany?.permissions?.includes('admin');
  const [items, setItems] = useState<ServiceProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [editing, setEditing] = useState<ServiceProfile | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [classes, setClasses] = useState<TaxClassification[]>([]);
  const [approving, setApproving] = useState<ServiceProfile | null>(null);
  const [approveFrom, setApproveFrom] = useState('');
  const [approveNote, setApproveNote] = useState('');
  const [approveChecked, setApproveChecked] = useState(false);
  const [approveErr, setApproveErr] = useState<string[]>([]);
  const [history, setHistory] = useState<{ p: ServiceProfile; rows: ServiceProfileVersionOut[] } | null>(null);
  const [resolving, setResolving] = useState<{ p: ServiceProfile; prop: ProfileProposal; decision: 'APPLY' | 'DISCARD'; alt?: string } | null>(null);
  const [resolveNote, setResolveNote] = useState('');
  const [resolveChecked, setResolveChecked] = useState(false);
  const [resolveErr, setResolveErr] = useState<string[]>([]);
  const [filtro, setFiltro] = useState<'todos' | 'PENDING_REVIEW' | 'APPROVED' | 'INACTIVE'>('todos');

  const load = useCallback(async () => {
    if (!currentCompany) return;
    setLoading(true);
    try {
      setItems(await clients.core.listServiceProfiles(currentCompany.id));
    } catch (err) {
      logSafe('perfis de serviço', err);
    } finally {
      setLoading(false);
    }
  }, [currentCompany, clients.core]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!open || !currentCompany || classes.length) return;
    const hoje = new Date().toLocaleDateString('sv-SE', { timeZone: currentCompany.timezone || 'America/Sao_Paulo' });
    clients.fiscal.getTaxClassifications(currentCompany.id, hoje).then((p) => setClasses(p.items))
      .catch((err) => logSafe('cclasstrib', err));
  }, [open, currentCompany, clients.fiscal, classes.length]);

  const preparar = async () => {
    if (!currentCompany) return;
    const id = currentCompany.id;
    setPreparing(true);
    try {
      const r = await clients.fiscal.prepareServiceProfiles(id);
      if (currentCompany?.id !== id) return;
      showToast(`${r.groups} serviço(s) identificado(s) nas notas emitidas: ${r.created.length} perfil(is) criado(s) para revisão; `
        + `${r.existing.length} já existente(s), sem alteração.`, 'success');
      await load();
    } catch (err) {
      logSafe('preparar perfis', err);
      showToast('Não foi possível preparar os perfis a partir das notas.', 'error');
    } finally {
      setPreparing(false);
    }
  };

  const start = (p: ServiceProfile | null) => {
    setEditing(p);
    setForm(p ? toForm(p) : EMPTY);
    setErrors({});
    setOpen(true);
  };

  const problemas = (err: unknown): Record<string, string> => {
    const fe: Record<string, string> = {};
    if (err instanceof ProblemDetailsException) {
      Object.entries(err.formatFieldErrors()).forEach(([k, v]) => { fe[k] = v.join(' '); });
    }
    return fe;
  };

  const save = async () => {
    if (!currentCompany) return;
    const id = currentCompany.id;
    const local: Record<string, string> = {};
    if (form.name.trim().length < 2) local.name = 'Informe um nome (mínimo 2 caracteres).';
    if (!/^\d{6}$/.test(form.national_code)) local.national_code = 'cTribNac: 6 dígitos.';
    if (form.nbs_code && !/^\d{9}$/.test(form.nbs_code)) local.nbs_code = 'cNBS: 9 dígitos.';
    if ((form.operation_indicator || form.class_code) && !(/^\d{6}$/.test(form.operation_indicator) && form.class_code)) {
      local['ibs_cbs.class_code'] = 'Para o IBS/CBS informe cIndOp (6 dígitos) e cClassTrib.';
    }
    setErrors(local);
    if (Object.keys(local).length) return;
    setSaving(true);
    try {
      const payload = toPayload(form);
      if (editing) await clients.core.updateServiceProfile(id, editing.id, { ...payload, expected_version: editing.version });
      else await clients.core.createServiceProfile(id, payload);
      if (currentCompany?.id !== id) return;
      setOpen(false);
      showToast(editing?.status === 'APPROVED' ? 'Perfil alterado: voltou para revisão e precisa de nova aprovação.' : 'Perfil salvo.', 'success');
      await load();
    } catch (err) {
      logSafe('perfil de serviço', err);
      setErrors(problemas(err));
      showToast((err as Error)?.message || 'Erro ao salvar o perfil de serviço.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const aprovar = async () => {
    if (!currentCompany || !approving) return;
    const id = currentCompany.id;
    try {
      await clients.core.approveServiceProfile(id, approving.id, approving.version, approveFrom || null, approveNote || null);
      if (currentCompany?.id !== id) return;
      setApproving(null);
      showToast(`Perfil “${approving.name}” aprovado.`, 'success');
      await load();
    } catch (err) {
      logSafe('aprovar perfil', err);
      const msgs = err instanceof ProblemDetailsException
        ? Object.entries(err.formatFieldErrors()).map(([k, v]) => `${k}: ${v.join(' ')}`) : [];
      setApproveErr(msgs.length ? msgs : [(err as Error)?.message || 'Não foi possível aprovar.']);
    }
  };

  const ativar = async (p: ServiceProfile, ativo: boolean) => {
    if (!currentCompany) return;
    try {
      await clients.core.setServiceProfileActive(currentCompany.id, p.id, ativo, p.version);
      await load();
    } catch (err) {
      logSafe('perfil de serviço', err);
      showToast('Não foi possível alterar a situação do perfil (recarregue e tente de novo).', 'error');
    }
  };

  const abrirResolucao = (p: ServiceProfile) => (prop: ProfileProposal, decision: 'APPLY' | 'DISCARD', alt?: string) => {
    setResolving({ p, prop, decision, alt });
    setResolveNote('');
    setResolveChecked(false);
    setResolveErr([]);
  };

  const resolver = async () => {
    if (!currentCompany || !resolving) return;
    const id = currentCompany.id;
    try {
      await clients.core.resolveServiceProfileProposal(id, resolving.p.id, resolving.prop.id, {
        expected_version: resolving.p.version, decision: resolving.decision, alternative: resolving.alt ?? null, note: resolveNote.trim(),
      });
      if (currentCompany?.id !== id) return;
      setResolving(null);
      showToast(resolving.decision === 'APPLY' ? 'Alternativa aplicada. O perfil continua pendente de revisão.' : 'Proposta descartada.', 'success');
      await load();
    } catch (err) {
      logSafe('resolver proposta', err);
      const msgs = err instanceof ProblemDetailsException
        ? Object.entries(err.formatFieldErrors()).map(([k, v]) => `${k}: ${v.join(' ')}`) : [];
      setResolveErr(msgs.length ? msgs : [(err as Error)?.message || 'Não foi possível resolver a proposta.']);
    }
  };

  const verHistorico = async (p: ServiceProfile) => {
    if (!currentCompany) return;
    try {
      setHistory({ p, rows: await clients.core.getServiceProfileVersions(currentCompany.id, p.id) });
    } catch (err) {
      logSafe('histórico do perfil', err);
    }
  };

  const set = (k: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));
  const quem = (uid?: string | null) => (!uid ? '—' : uid === user?.id ? 'você' : `usuário ${uid.slice(0, 8)}`);
  const contagem = (s: string) => items.filter((p) => p.status === s).length;
  const visiveis = items.filter((p) => filtro === 'todos' || p.status === filtro);
  const selClass = classes.find((c) => c.codigo === form.class_code);

  return (
    <ScreenSection
      title="Perfis de serviço"
      description="Serviços prestados pelo workspace, preparados a partir dos XMLs oficiais das notas EMITIDAS. Cada perfil precisa de revisão e aprovação explícitas; perfis pendentes ajudam a preparar rascunhos, mas não liberam transmissão."
      actions={canEdit ? (
        <>
          <Button variant="primary" size="sm" onClick={preparar} isLoading={preparing}>Preparar perfis a partir das notas</Button>
          <Button variant="outline" size="sm" onClick={() => start(null)}>Novo perfil manual</Button>
        </>
      ) : undefined}
    >
      {!canEdit && <p className="pf-help">Somente administradores do workspace preparam, editam e aprovam perfis.</p>}
      <div className="pf-origem-legenda" aria-label="Legenda da origem dos dados">
        <span className="pf-help">Origem dos dados:</span>
        {Object.entries(ORIGEM).map(([k, o]) => <span key={k} className={o.className} title={o.title}>{o.label}</span>)}
      </div>
      {items.length > 0 && (
        <div className="pf-filtro" role="group" aria-label="Filtrar perfis por situação">
          {(['todos', 'PENDING_REVIEW', 'APPROVED', 'INACTIVE'] as const).map((s) => (
            <button key={s} type="button" className="pf-filtro-btn" aria-pressed={filtro === s} onClick={() => setFiltro(s)}>
              {s === 'todos' ? `Todos (${items.length})` : `${STATUS[s].label} (${contagem(s)})`}
            </button>
          ))}
        </div>
      )}
      {items.length === 0 && !loading ? (
        <TableEmpty title="Nenhum perfil de serviço"
          description="Use “Preparar perfis a partir das notas” para identificar os serviços já prestados (notas emitidas)." />
      ) : (
        <ul className="pf-perfis" aria-busy={loading || undefined}>
          {visiveis.map((p) => {
            const st = STATUS[p.status] || { label: p.status, badge: 'UNKNOWN' };
            const erros = p.pending.filter((i) => i.severity === 'error');
            const avisos = p.pending.filter((i) => i.severity === 'warning');
            const infos = p.pending.filter((i) => i.severity === 'info');
            const d = p.descriptions || {};
            return (
              <li key={p.id} className="pf-perfil" data-status={p.status}>
                <div className="pf-perfil-head">
                  <div style={{ minWidth: 0 }}>
                    <h3 className="pf-subtitle">{p.name}</h3>
                    <div className="pf-help">
                      Versão {p.version}
                      {p.status === 'APPROVED' && <> · aprovado por {quem(p.approved_by)} em {fmtData(p.approved_at)}</>}
                      {' '}· vigência {fmtData(p.valid_from)} a {p.valid_to ? fmtData(p.valid_to) : 'sem término'}
                    </div>
                  </div>
                  <Badge status={st.badge} customLabel={st.label} size="sm" />
                </div>

                <dl className="pf-perfil-campos">
                  <div>
                    <dt>Código de tributação nacional <OrigemTag src={p.field_sources.national_code} /></dt>
                    <dd><strong className="ui-mono">{fmtCtn(p.national_code)}</strong> — {d.national_code?.texto || '—'}</dd>
                  </div>
                  <div>
                    <dt>NBS <OrigemTag src={p.field_sources.nbs_code} /></dt>
                    <dd>{p.nbs_code ? <><strong className="ui-mono">{p.nbs_code}</strong> — {d.nbs_code?.texto || '—'}</> : <em>não definido</em>}</dd>
                  </div>
                  <div>
                    <dt>Tributação do ISSQN <OrigemTag src={p.field_sources.issqn_taxation} /></dt>
                    <dd>{p.issqn_taxation ? ISSQN[p.issqn_taxation] : <em>não definida</em>}
                      {d.incidencia?.texto && <span className="pf-help"> · incidência (Anexo I): {d.incidencia.texto}</span>}</dd>
                  </div>
                  <div>
                    <dt>CST do PIS/COFINS <OrigemTag src={p.field_sources.pis_cofins_cst} /></dt>
                    <dd>{p.pis_cofins_cst || <em>não definido</em>}</dd>
                  </div>
                  <div className="pf-span-all">
                    <dt>Classificação IBS/CBS <OrigemTag src={p.field_sources.ibs_cbs} /></dt>
                    <dd>
                      {p.ibs_cbs ? (
                        <>
                          <div>cIndOp <strong className="ui-mono">{p.ibs_cbs.operation_indicator}</strong>
                            {d.operation_indicator && <> — {d.operation_indicator.texto} <span className="pf-help">(local da operação: {d.operation_indicator.local_operacao}; no DF-e: {d.operation_indicator.local_dfe})</span></>}</div>
                          <div>cClassTrib <strong className="ui-mono">{p.ibs_cbs.class_code}</strong> · CST {p.ibs_cbs.cst}
                            {d.class_code && <> — {d.class_code.texto} <span className="pf-help">(redução IBS {d.class_code.reducao_ibs}% / CBS {d.class_code.reducao_cbs}%)</span></>}</div>
                          {p.ibs_cbs.regular && <div>Tributação regular: CSTReg {p.ibs_cbs.regular.cst} · cClassTribReg {p.ibs_cbs.regular.class_code}</div>}
                        </>
                      ) : <em>não definida</em>}
                    </dd>
                  </div>
                </dl>

                {erros.length > 0 && (
                  <div className="pf-perfil-pend" data-severity="error">
                    <strong>Falta para aprovar:</strong>
                    <ul>{erros.map((i, k) => <li key={k}><strong>{i.label}</strong>: {i.message} <span className="pf-help">({i.code})</span></li>)}</ul>
                  </div>
                )}
                {avisos.length > 0 && (
                  <div className="pf-perfil-pend" data-severity="warning">
                    <strong>Conferir:</strong>
                    <ul>{avisos.map((i, k) => <li key={k}><strong>{i.label}</strong>: {i.message}</li>)}</ul>
                  </div>
                )}
                {p.evidence && 'divergencias' in p.evidence && (p.evidence.divergencias?.length || 0) > 0 && (
                  <div className="pf-perfil-pend" data-severity="warning">
                    <strong>Divergências entre as notas:</strong>
                    <ul>{p.evidence.divergencias.map((x, k) => <li key={k}>{x.message}</li>)}</ul>
                  </div>
                )}
                {p.suggestions.filter(ehProposta).map((prop) => (
                  <PropostaRevisao key={prop.id} prop={prop} canEdit={canEdit && p.status === 'PENDING_REVIEW'} onResolve={abrirResolucao(p)} />
                ))}
                {(infos.length > 0 || p.suggestions.some((s) => !ehProposta(s))) && (
                  <details className="pf-perfil-info">
                    <summary>O que cada nota exigirá e sugestões ({infos.length + p.suggestions.filter((s) => !ehProposta(s)).length})</summary>
                    <ul>
                      {infos.map((i, k) => <li key={`i${k}`}>{i.message} <span className="pf-help">({i.code})</span></li>)}
                      {p.suggestions.filter((s): s is ProfileSuggestion => !ehProposta(s)).map((s, k) => (
                        <li key={`s${k}`}>
                          <span className={s.tipo === 'tabela_oficial' ? 'pf-origem pf-origem-tabela' : 'pf-origem pf-origem-xml'}>
                            {s.tipo === 'tabela_oficial' ? 'Tabela oficial' : 'XML oficial'}
                          </span>{' '}{s.texto} <span className="pf-help">Fonte: {s.fonte}</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                <Evidencia p={p} />

                <div className="pf-actions-end">
                  <Button variant="ghost" size="sm" onClick={() => verHistorico(p)}>Histórico</Button>
                  {canEdit && p.status !== 'INACTIVE' && (
                    <Button variant="ghost" size="sm" onClick={() => ativar(p, false)}>Inativar</Button>
                  )}
                  {canEdit && p.status === 'INACTIVE' && (
                    <Button variant="outline" size="sm" onClick={() => ativar(p, true)}>Reativar para revisão</Button>
                  )}
                  {canEdit && p.status !== 'INACTIVE' && (
                    <Button variant="outline" size="sm" onClick={() => start(p)}>Revisar e editar</Button>
                  )}
                  {canEdit && p.status === 'PENDING_REVIEW' && (
                    <Button variant="primary" size="sm" disabled={!p.can_approve}
                      title={p.can_approve ? undefined : 'Preencha o que falta para aprovar.'}
                      onClick={() => { setApproving(p); setApproveFrom(''); setApproveNote(''); setApproveChecked(false); setApproveErr([]); }}>
                      Aprovar
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <Modal isOpen={open} onClose={() => setOpen(false)} maxWidth="820px"
        title={editing ? `Revisar perfil: ${editing.name}` : 'Novo perfil de serviço'}
        description="Os códigos são conferidos nas tabelas oficiais (Anexos B e C e tabela cClassTrib para NFS-e). Alterar um perfil aprovado devolve-o à revisão."
        footer={
          <div className="pf-actions-end">
            <Button variant="outline" onClick={() => setOpen(false)}>Cancelar</Button>
            <Button variant="primary" onClick={save} isLoading={saving}>Salvar perfil</Button>
          </div>
        }>
        <div className="pf-form-grid">
          <Input label="Nome do perfil" required value={form.name} onChange={set('name')} error={errors.name} />
          <Input label="Código de tributação nacional (cTribNac)" required inputMode="numeric" maxLength={6}
            value={form.national_code} onChange={set('national_code')} error={errors.national_code}
            helperText={editing?.descriptions?.national_code?.texto ? `Atual: ${String(editing.descriptions.national_code.texto).slice(0, 90)}` : '6 dígitos da lista nacional.'} />
          <Input label="Código NBS (cNBS)" inputMode="numeric" maxLength={9} value={form.nbs_code} onChange={set('nbs_code')}
            error={errors.nbs_code} helperText="9 dígitos; obrigatório com IBS/CBS (E0322)." />
          <Input label="Código de tributação municipal (cTribMun)" value={form.municipal_code} maxLength={20}
            onChange={set('municipal_code')} error={errors.municipal_code} helperText="Opcional; depende do município de incidência." />
          <Select label="Tributação do ISSQN (tribISSQN)" value={form.issqn_taxation} onChange={set('issqn_taxation')}
            options={[{ value: '', label: 'Não definir' }, ...Object.entries(ISSQN).map(([value, label]) => ({ value, label }))]} />
          <Input label="CST do PIS/COFINS" inputMode="numeric" maxLength={2} value={form.pis_cofins_cst}
            onChange={set('pis_cofins_cst')} error={errors.pis_cofins_cst} helperText="Opcional (2 dígitos)." />
          <div className="pf-span-all">
            <Textarea label="Descrição padrão do serviço (xDescServ)" rows={2} maxLength={2000} value={form.description}
              onChange={set('description')} helperText="Opcional; editável em cada nota." />
          </div>
          <div className="pf-span-all pf-subpanel">
            <h3 className="pf-subtitle">Classificação IBS/CBS</h3>
            <p className="pf-help">Classificação usada em nota anterior é evidência histórica: confirme se vale para as novas operações. O CST é derivado do cClassTrib (E0959).</p>
            <div className="pf-form-grid">
              <Input label="Indicador da operação (cIndOp)" inputMode="numeric" maxLength={6} value={form.operation_indicator}
                onChange={(e) => setForm((f) => ({ ...f, operation_indicator: e.target.value.replace(/\D/g, '') }))}
                error={errors['ibs_cbs.operation_indicator']} helperText="Anexo C (6 dígitos)." />
              <Select label="Classificação (cClassTrib)" value={form.class_code}
                onChange={(e) => {
                  const c = classes.find((x) => x.codigo === e.target.value);
                  setForm((f) => ({ ...f, class_code: e.target.value, cst: c ? c.cst : '', reg_class_code: c?.exige_tributacao_regular ? f.reg_class_code : '', reg_cst: c?.exige_tributacao_regular ? f.reg_cst : '' }));
                }}
                error={errors['ibs_cbs.class_code'] || errors['ibs_cbs.cst']}
                options={[{ value: '', label: classes.length ? 'Não definir' : 'Carregando tabela oficial…' },
                  ...(form.class_code && !classes.some((c) => c.codigo === form.class_code)
                    ? [{ value: form.class_code, label: `${form.class_code} (fora da tabela vigente)` }] : []),
                  ...classes.map((c) => ({ value: c.codigo, label: `${c.codigo} — ${c.nome}` }))]} />
              <Input label="CST do IBS/CBS (derivado)" value={form.cst} readOnly helperText="3 primeiros dígitos do cClassTrib." />
              <Select label="Uso/consumo pessoal (indFinal)" value={form.personal_use} onChange={set('personal_use')}
                options={[{ value: '', label: 'Não informar' }, { value: '0', label: '0 — Não' }, { value: '1', label: '1 — Sim' }]} />
              {(selClass?.exige_tributacao_regular || form.reg_class_code) && (
                <>
                  <Select label="Tributação regular (cClassTribReg)" value={form.reg_class_code}
                    onChange={(e) => setForm((f) => ({ ...f, reg_class_code: e.target.value, reg_cst: e.target.value.slice(0, 3) }))}
                    helperText="Exigida por esta classificação (E0965)."
                    options={[{ value: '', label: 'Selecione…' }, ...classes.map((c) => ({ value: c.codigo, label: `${c.codigo} — ${c.nome}` }))]} />
                  <Input label="Situação tributária regular (derivada)" value={form.reg_cst} readOnly />
                </>
              )}
            </div>
          </div>
          <Input label="Início da vigência" type="date" value={form.valid_from} onChange={set('valid_from')}
            helperText="Opcional; na aprovação, o padrão é hoje." />
          <Input label="Fim da vigência" type="date" value={form.valid_to} onChange={set('valid_to')} error={errors.valid_to}
            helperText="Opcional." />
          <Input label="Fonte da definição" value={form.source} maxLength={300} onChange={set('source')}
            helperText="Ex.: orientação da contabilidade de 09/10/2026." />
        </div>
        {Object.keys(errors).length > 0 && (
          <Notice variant="error" title="Revise os campos destacados">O perfil não foi salvo.</Notice>
        )}
      </Modal>

      <Modal isOpen={!!approving} onClose={() => setApproving(null)} maxWidth="640px"
        title={approving ? `Aprovar perfil: ${approving.name}` : 'Aprovar perfil'}
        description="A aprovação confirma todos os campos preenchidos (inclusive os vindos de notas anteriores) e libera o uso do perfil em notas transmitidas."
        footer={
          <div className="pf-actions-end">
            <Button variant="outline" onClick={() => setApproving(null)}>Cancelar</Button>
            <Button variant="primary" disabled={!approveChecked} onClick={aprovar}>Aprovar perfil</Button>
          </div>
        }>
        {approving && (
          <div className="pf-stack">
            {approving.pending.filter((i) => i.severity === 'warning').length > 0 && (
              <Notice variant="warning" title="Itens a conferir">
                <ul className="pf-bullets">
                  {approving.pending.filter((i) => i.severity === 'warning').map((i, k) => <li key={k}><strong>{i.label}</strong>: {i.message}</li>)}
                </ul>
              </Notice>
            )}
            <div className="pf-form-grid">
              <Input label="Início da vigência" type="date" value={approveFrom} onChange={(e) => setApproveFrom(e.target.value)}
                helperText="Em branco: hoje (ou o início já definido)." />
              <Input label="Observação (opcional)" value={approveNote} maxLength={500} onChange={(e) => setApproveNote(e.target.value)} />
            </div>
            <label className="pf-check">
              <input type="checkbox" checked={approveChecked} onChange={(e) => setApproveChecked(e.target.checked)} />
              Conferi os códigos, a classificação IBS/CBS e as notas de referência deste perfil.
            </label>
            {approveErr.length > 0 && <Notice variant="error" title="Não aprovado"><ul className="pf-bullets">{approveErr.map((m, k) => <li key={k}>{m}</li>)}</ul></Notice>}
          </div>
        )}
      </Modal>

      <Modal isOpen={!!resolving} onClose={() => setResolving(null)} maxWidth="640px"
        title={resolving ? (resolving.decision === 'APPLY' ? `Aplicar alternativa ${resolving.alt} da proposta ${resolving.prop.id}` : `Descartar proposta ${resolving.prop.id}`) : 'Proposta'}
        description="Aplicar copia os códigos da alternativa para o perfil como dados informados por você; o perfil continua pendente de revisão e a aprovação é um passo separado."
        footer={
          <div className="pf-actions-end">
            <Button variant="outline" onClick={() => setResolving(null)}>Cancelar</Button>
            <Button variant="primary" disabled={!resolveChecked || resolveNote.trim().length < 5} onClick={resolver}>
              {resolving?.decision === 'APPLY' ? 'Aplicar alternativa' : 'Descartar proposta'}
            </Button>
          </div>
        }>
        {resolving && (
          <div className="pf-stack">
            {resolving.decision === 'APPLY' && (() => {
              const a = resolving.prop.alternativas.find((x) => x.id === resolving.alt);
              return a ? (
                <Notice variant="warning" title={`${a.id}. ${a.rotulo}`}>
                  <div>{fmtCampos(a.campos)}</div>
                  <div className="pf-help">Condição: {a.condicao}</div>
                </Notice>
              ) : null;
            })()}
            {resolving.prop.perguntas.length > 0 && (
              <Notice variant="info" title="Perguntas desta proposta">
                <ul className="pf-bullets">{resolving.prop.perguntas.map((q) => <li key={q.id}><strong>{q.id}</strong>: {q.texto}</li>)}</ul>
              </Notice>
            )}
            <Textarea label={resolving.decision === 'APPLY' ? 'Confirmação recebida (contrato, contabilidade)' : 'Motivo do descarte'}
              value={resolveNote} maxLength={1000} onChange={(e) => setResolveNote(e.target.value)}
              helperText="Obrigatório (mínimo de 5 caracteres). Fica registrado no histórico e na auditoria." />
            <label className="pf-check">
              <input type="checkbox" checked={resolveChecked} onChange={(e) => setResolveChecked(e.target.checked)} />
              {resolving.decision === 'APPLY'
                ? 'A condição desta alternativa foi confirmada. Entendo que o perfil NÃO será aprovado por esta ação.'
                : 'Esta proposta não se aplica a este serviço.'}
            </label>
            {resolveErr.length > 0 && <Notice variant="error" title="Não resolvida"><ul className="pf-bullets">{resolveErr.map((m, k) => <li key={k}>{m}</li>)}</ul></Notice>}
          </div>
        )}
      </Modal>

      <Modal isOpen={!!history} onClose={() => setHistory(null)} maxWidth="760px"
        title={history ? `Histórico: ${history.p.name}` : 'Histórico'}
        description="Cada versão guarda o conteúdo completo do perfil; rascunhos e lotes registram a versão aplicada.">
        {history && (
          <ol className="pf-historico">
            {history.rows.map((h) => (
              <li key={h.version}>
                <div><strong>v{h.version}</strong> · {ACOES[h.action] || h.action} · {new Date(h.created_at).toLocaleString('pt-BR')} ·{' '}
                  {h.actor_type === 'system' ? `sistema (${h.actor_id || '—'})` : quem(h.actor_id)}</div>
                <div className="pf-help">
                  Situação {STATUS[h.snapshot.status]?.label || h.snapshot.status} · cTribNac {h.snapshot.national_code} · NBS {h.snapshot.nbs_code || '—'}
                  {' '}· IBS/CBS {h.snapshot.ibs_cbs ? `${h.snapshot.ibs_cbs.operation_indicator}/${h.snapshot.ibs_cbs.class_code}` : '—'}
                </div>
              </li>
            ))}
          </ol>
        )}
      </Modal>
    </ScreenSection>
  );
};
