import React, { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react';
import {
  FiscalDocument,
  RetentionType,
  formatCurrencyPtBr,
  getDocumentEnvironmentBadgeConfig,
  getFiscalAuthorizationBadgeConfig,
  getReviewStatusBadgeConfig,
} from '@ctrl-fiscal/api-client';
import {
  Badge, Button, Column, Drawer, ErrorState, FilterPanel, Icon, Input, PageHeader, Select, Table, useDebouncedValue,
} from '@ctrl-fiscal/ui';
import { useFiscal, logSafe } from '../../context/FiscalContext.js';
import { BundleDialog, BundleScope } from './BundleDialog.js';
import { DocFilters, EMPTY, activeAdvancedCount, toApiFilter, useDocumentUrlState, useMediaQuery } from './urlState.js';
import { NotaSelecionada, resumo, rotuloNota, selecaoNotas, useSelecaoNotas } from './selecaoNotas.js';
import type { ViewerMode } from './DocumentViewer.js';
import { saveBlob } from './saveBlob.js';
import { IbsCbsOficialSection } from './IbsCbsOficialSection.js';

const DocumentViewer = lazy(() => import('./DocumentViewer.js'));
import { CancellationPanel } from './CancellationPanel.js';
const PAGE_SIZE = 25;
const RET_TYPES: { id: RetentionType; label: string }[] = [
  { id: 'ISS', label: 'ISS retido' },
  { id: 'INSS', label: 'INSS / CP' },
  { id: 'IRRF', label: 'IRRF' },
  { id: 'CONTRIB', label: 'Contribuições (PIS/COFINS/CSLL)' },
];

export interface DocumentsWorkspaceProps {
  direction: 'ISSUED' | 'RECEIVED';
  title: string;
  description: React.ReactNode;
  primaryAction?: React.ReactNode;
  counterpartLabel: string;
  /** Ações extras no painel da nota (duplicar, conferir…). Recebe a nota e um callback para recarregar. */
  renderDetailExtras?: (doc: FiscalDocument, reload: () => void, replaceDoc: (d: FiscalDocument) => void) => React.ReactNode;
  showReviewColumn?: boolean;
}

const money = (v?: string | null) => (v ? formatCurrencyPtBr(v) : '—');

export const DocumentsWorkspace: React.FC<DocumentsWorkspaceProps> = ({
  direction, title, description, primaryAction, counterpartLabel, renderDetailExtras, showReviewColumn,
}) => {
  const { clients, currentCompany } = useFiscal();
  const { filters, setFilters, openDoc, openDocument } = useDocumentUrlState(
    () => (currentCompany ? (selecaoNotas.ultimaConsulta(currentCompany.id, direction) as DocFilters | null) : null),
  );
  // Seleção acumulada entre pesquisas e páginas, pelo ID da nota; vale para este workspace, direção e ambiente.
  const sel = useSelecaoNotas({ company: currentCompany?.id || '', direction, env: filters.env });
  const isMobile = useMediaQuery('(max-width: 767px)');
  const [queryInput, setQueryInput] = useState(filters.q);
  const debouncedQuery = useDebouncedValue(queryInput, 400);
  const [docs, setDocs] = useState<FiscalDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [verSelecionadas, setVerSelecionadas] = useState(false);
  const [bundleScope, setBundleScope] = useState<BundleScope | null>(null);
  const [viewer, setViewer] = useState<{ doc: FiscalDocument; mode: ViewerMode } | null>(null);
  const [filtersDrawer, setFiltersDrawer] = useState(false);
  const [detail, setDetail] = useState<FiscalDocument | null>(null);

  useEffect(() => {
    if (debouncedQuery !== filters.q) setFilters({ q: debouncedQuery });
  }, [debouncedQuery]); // eslint-disable-line react-hooks/exhaustive-deps

  const apiFilter = useMemo(() => toApiFilter(filters, direction), [filters, direction]);
  const filterKey = JSON.stringify({ ...apiFilter, env: filters.env });

  // Busca, filtros e página NÃO apagam a seleção; ficam guardados para retomar a consulta ao voltar à tela.
  useEffect(() => { selecaoNotas.guardarFiltros(sel.chave, filters); }, [sel.chave, filters]);
  // Outro workspace, direção ou ambiente: a seleção é outra; diálogos do contexto anterior são fechados.
  useEffect(() => {
    setBundleScope(null);
    setVerSelecionadas(false);
  }, [sel.chave]);

  useEffect(() => {
    if (!currentCompany) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    clients.fiscal
      .getDocuments(currentCompany.id, { ...apiFilter, page: filters.page, page_size: PAGE_SIZE, environment: filters.env || undefined })
      .then((res) => {
        if (cancelled) return;
        setDocs(res.items);
        setTotal(res.total);
      })
      .catch((err) => {
        logSafe('documentos', err);
        if (!cancelled) {
          const p = (err as { problem?: { detail?: string } })?.problem;
          setLoadError(p?.detail || 'Verifique a conexão e tente novamente. Os filtros foram mantidos.');
        }
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [currentCompany, clients.fiscal, filterKey, filters.page, reloadTick]); // eslint-disable-line react-hooks/exhaustive-deps

  // Nota aberta pela URL (?doc=…): usa a linha carregada ou busca o detalhe.
  useEffect(() => {
    if (!openDoc || !currentCompany) {
      setDetail(null);
      return;
    }
    const inPage = docs.find((d) => d.id === openDoc);
    if (inPage) {
      setDetail(inPage);
      return;
    }
    let cancelled = false; // resposta atrasada (outra nota ou outro workspace) é descartada
    clients.fiscal.getDocument(currentCompany.id, openDoc).then((d) => !cancelled && setDetail(d)).catch((err) => {
      logSafe('documento', err);
      if (!cancelled) openDocument(null);
    });
    return () => {
      cancelled = true;
    };
  }, [openDoc, docs, currentCompany, clients.fiscal, openDocument]);

  const reload = useCallback(() => setReloadTick((t) => t + 1), []);
  // Depois de um evento oficial (ex.: cancelamento registrado), recarrega a nota aberta e a lista.
  const refreshDetail = useCallback((id: string) => {
    if (!currentCompany) return;
    clients.fiscal.getDocument(currentCompany.id, id).then(setDetail).catch((err) => logSafe('document', err));
    setReloadTick((t) => t + 1);
  }, [clients.fiscal, currentCompany]);

  const download = async (doc: FiscalDocument, kind: 'pdf' | 'xml') => {
    if (!currentCompany) return;
    const key = doc.access_key || doc.id;
    try {
      if (kind === 'pdf') saveBlob(await clients.fiscal.getDanfsePdf(currentCompany.id, doc.id), `DANFSe_${key}.pdf`);
      else saveBlob(await clients.fiscal.getDocumentXmlBlob(currentCompany.id, doc.id), `NFSe_${key}.xml`);
    } catch (err) {
      logSafe(kind, err);
      setViewer({ doc, mode: kind }); // o visualizador mostra o motivo e permite tentar de novo
    }
  };

  const counterpart = (d: FiscalDocument) => (direction === 'ISSUED' ? d.recipient : d.issuer);

  const columns: Column<FiscalDocument>[] = [
    {
      key: 'number', header: 'NFS-e',
      render: (d) => (
        <div>
          <button type="button" className="pf-linkbtn" onClick={(e) => { e.stopPropagation(); openDocument(d.id); }}
            aria-label={`Abrir NFS-e ${d.number || ''}`}>
            {d.number ? `Nº ${d.number}` : 'Sem número'}
          </button>
          <div className="ui-mono" style={{ fontSize: '0.6875rem', color: 'var(--color-text-subtle)' }}>
            {d.access_key ? `${d.access_key.slice(0, 10)}…${d.access_key.slice(-6)}` : ''}
          </div>
        </div>
      ),
    },
    { key: 'issued_at', header: 'Emissão', render: (d) => (d.issued_at ? new Date(d.issued_at).toLocaleDateString('pt-BR') : '—') },
    {
      key: 'counterpart', header: counterpartLabel,
      render: (d) => (
        <div style={{ minWidth: '10rem' }}>
          <div style={{ fontWeight: 600 }}>{counterpart(d)?.name || '—'}</div>
          <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>{counterpart(d)?.cnpj || ''}</div>
        </div>
      ),
    },
    { key: 'competence', header: 'Competência', hideOnMobile: true, render: (d) => d.competence || '—' },
    { key: 'amount', header: 'Valor', numeric: true, render: (d) => money(d.amount) },
    {
      key: 'withheld', header: 'Retido', numeric: true, hideOnMobile: true,
      render: (d) => {
        const c = d.retention_summary?.classification;
        if (c === 'UNKNOWN') return <span title="Retenção indicada sem valor ou não informada no documento">Desconhecido</span>;
        if (c === 'WITHOUT') return <span style={{ color: 'var(--color-text-subtle)' }}>Sem retenção</span>;
        return money(d.total_retained);
      },
    },
    {
      key: 'status', header: 'Situação',
      render: (d) => {
        const b = getFiscalAuthorizationBadgeConfig(d.status);
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', alignItems: 'flex-start' }}>
            <Badge status={d.status} customLabel={b.label} />
            {d.danfse_watermark === 'SUBSTITUÍDA' && <Badge status="WITHDRAWN" customLabel="Substituída" icon="↩" />}
            {d.status_code === '101' && <Badge status="INFO" customLabel="Substituta" />}
          </div>
        );
      },
    },
    ...(showReviewColumn ? [{
      key: 'review', header: 'Conferência', hideOnMobile: true,
      render: (d: FiscalDocument) => {
        const r = getReviewStatusBadgeConfig(d.review_status);
        return <Badge status={d.review_status || 'PENDING'} customLabel={r.label} />;
      },
    }] : []),
    {
      key: 'env', header: 'Ambiente', hideOnMobile: true,
      render: (d) => {
        const e = getDocumentEnvironmentBadgeConfig(d.environment, d.simulated);
        return <Badge status={e.status} customLabel={e.label} icon="" />;
      },
    },
  ];

  const selected = useMemo(() => new Set(sel.itens.keys()), [sel.itens]);
  const selectionCount = sel.itens.size;
  const visibleSelected = docs.filter((d) => selected.has(d.id)).length;
  const notas = (n: number) => `${n} ${n === 1 ? 'nota' : 'notas'}`;
  const abrirDownload = () => setBundleScope({
    kind: 'selection', companyId: currentCompany!.id, count: selectionCount, ids: Array.from(sel.itens.keys()),
    itens: Array.from(sel.itens.values()), environment: filters.env || undefined,
  });
  const advCount = activeAdvancedCount(filters);

  const advanced = (
    <div className="pf-filterbar">
      <Select label="Situação" value={filters.status} onChange={(e) => setFilters({ status: e.target.value })}
        options={[{ value: '', label: 'Todas' }, { value: 'AUTHORIZED', label: 'Autorizada' }, { value: 'CANCELLED', label: 'Cancelada' }]} />
      {showReviewColumn && (
        <Select label="Conferência" value={filters.review} onChange={(e) => setFilters({ review: e.target.value })}
          options={[{ value: '', label: 'Todas' }, { value: 'PENDING', label: 'Pendente' }, { value: 'REVIEWED', label: 'Conferida' },
            { value: 'DIVERGENT', label: 'Divergente' }]} />
      )}
      <Select label="Retenção" value={filters.ret} onChange={(e) => setFilters({ ret: e.target.value as typeof filters.ret })}
        options={[{ value: '', label: 'Todas' }, { value: 'WITH', label: 'Com retenção' }, { value: 'WITHOUT', label: 'Sem retenção' },
          { value: 'UNKNOWN', label: 'Informação desconhecida' }]} />
      <Input label="Total retido de (R$)" inputMode="decimal" placeholder="0.00" value={filters.wmin}
        onChange={(e) => setFilters({ wmin: e.target.value.replace(',', '.') })} />
      <Input label="Total retido até (R$)" inputMode="decimal" placeholder="0.00" value={filters.wmax}
        onChange={(e) => setFilters({ wmax: e.target.value.replace(',', '.') })} />
      <fieldset style={{ border: 'none', gridColumn: '1 / -1' }}>
        <legend style={{ fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.375rem' }}>Tipos de retenção efetiva</legend>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem 1.25rem', fontSize: '0.875rem' }}>
          {RET_TYPES.map((t) => (
            <label key={t.id} style={{ display: 'flex', gap: '0.375rem', alignItems: 'center' }}>
              <input type="checkbox" checked={filters.rtype.includes(t.id)} onChange={(e) => setFilters({
                rtype: e.target.checked ? [...filters.rtype, t.id] : filters.rtype.filter((x) => x !== t.id),
              })} />
              {t.label}
            </label>
          ))}
        </div>
        {filters.rtype.length > 1 && (
          <div role="radiogroup" aria-label="Combinar tipos de retenção" style={{ display: 'flex', gap: '1rem', marginTop: '0.5rem', fontSize: '0.875rem' }}>
            <span style={{ color: 'var(--color-text-muted)' }}>Combinar:</span>
            {(['ANY', 'ALL'] as const).map((m) => (
              <label key={m} style={{ display: 'flex', gap: '0.375rem', alignItems: 'center' }}>
                <input type="radio" name={`rmatch-${direction}`} checked={(filters.rmatch || 'ANY') === m} onChange={() => setFilters({ rmatch: m })} />
                {m === 'ANY' ? 'Qualquer um dos tipos' : 'Todos os tipos'}
              </label>
            ))}
          </div>
        )}
        <p style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)', marginTop: '0.5rem' }}>
          Retenção efetiva ≠ imposto informado: PIS/COFINS de apuração própria informados não contam como retenção, exceto na modalidade 1 (PIS/COFINS retidos), em que a NT 008 v1.02 os soma às contribuições retidas. As contribuições vêm como valor consolidado; o XML não separa PIS, COFINS e CSLL e não há rateio.
        </p>
      </fieldset>
    </div>
  );

  return (
    <div className="pf-stack">
      <PageHeader title={title} description={description} primaryAction={primaryAction} />

      <section aria-label="Filtros" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        <div className="pf-filterbar">
          <Input label="Buscar" placeholder="Número, chave, CNPJ, nome ou descrição" value={queryInput}
            onChange={(e) => setQueryInput(e.target.value)} type="search" />
          <Input label="Competência" type="month" value={filters.comp} onChange={(e) => setFilters({ comp: e.target.value })} />
          <Input label="Emissão de" type="date" value={filters.from} max={filters.to || undefined}
            onChange={(e) => setFilters({ from: e.target.value })} />
          <Input label="Emissão até" type="date" value={filters.to} min={filters.from || undefined}
            onChange={(e) => setFilters({ to: e.target.value })} />
          <Select label="Ambiente" value={filters.env} onChange={(e) => setFilters({ env: e.target.value })}
            options={[{ value: '', label: 'Padrão (produção, se lida)' }, { value: 'restricted', label: 'Produção Restrita (homologação)' },
              { value: 'production', label: 'Produção (leitura ADN)' }, { value: 'mock', label: 'Simulação' }]} />
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'end' }}>
            {isMobile && (
              <Button variant="outline" onClick={() => setFiltersDrawer(true)} aria-haspopup="dialog">
                <Icon name="filter" size={16} /> Filtros{advCount ? ` (${advCount})` : ''}
              </Button>
            )}
            {/* Limpa só a pesquisa e os filtros: a seleção e o ambiente (contexto da seleção) continuam. */}
            <Button variant="ghost" onClick={() => { setQueryInput(''); setFilters({ ...EMPTY, env: filters.env }); }}>Limpar filtros</Button>
          </div>
        </div>
        {!isMobile && <FilterPanel activeCount={advCount}>{advanced}</FilterPanel>}
      </section>

      {selectionCount > 0 && (
        <div className="pf-bulkbar" role="region" aria-label="Seleção">
          <div aria-live="polite">
            <strong>{selectionCount}</strong> {selectionCount === 1 ? 'nota selecionada' : 'notas selecionadas'}
            {selectionCount > visibleSelected
              ? <span style={{ color: 'var(--color-text-muted)' }}> ({visibleSelected} nesta página; as demais vêm de outras pesquisas ou páginas)</span>
              : null}.{' '}
            <button type="button" className="pf-linkbtn" onClick={() => setVerSelecionadas(true)} aria-haspopup="dialog">Ver selecionadas</button>{' · '}
            <button type="button" className="pf-linkbtn" onClick={sel.limpar}>Limpar seleção</button>
          </div>
          <Button variant="primary" size="sm" onClick={abrirDownload}>
            <Icon name="download" size={16} /> Baixar {notas(selectionCount)}…
          </Button>
        </div>
      )}

      {loadError ? (
        <ErrorState message={loadError} onRetry={reload} />
      ) : (
        <Table
          caption={`${title}: ${total} resultados`}
          columns={columns}
          data={docs}
          keyExtractor={(d) => d.id}
          isLoading={loading}
          page={filters.page}
          pageSize={PAGE_SIZE}
          total={total}
          onPageChange={(p) => setFilters({ page: p })}
          onRowClick={(d) => openDocument(d.id)}
          emptyState={
            <div>
              <div style={{ fontWeight: 600, color: 'var(--color-text-main)' }}>Nenhuma nota encontrada</div>
              <div style={{ fontSize: '0.8125rem', marginTop: '0.25rem' }}>Ajuste ou limpe os filtros para ver outros resultados.</div>
            </div>
          }
          selection={{
            selected,
            rowLabel: (d: FiscalDocument) => `NFS-e ${d.number || d.access_key || ''}`,
            onToggle: (id) => {
              const d = docs.find((x) => x.id === id);
              if (d) sel.alternar(resumo(d, direction));
            },
            // Só a página exibida; o que foi marcado em outras pesquisas e páginas continua.
            onTogglePage: (checked) => sel.marcarPagina(docs.map((d) => resumo(d, direction)), checked),
          }}
        />
      )}
      {!loading && !loadError && total > 0 && (
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          {/* Independente da seleção: usa o filtro atual (todas as páginas). */}
          <Button variant="outline" size="sm"
            onClick={() => setBundleScope({ kind: 'all', companyId: currentCompany!.id, count: total, filters: { ...apiFilter },
              environment: filters.env || undefined })}>
            <Icon name="download" size={16} /> Baixar todos os {total} resultados do filtro…
          </Button>
        </div>
      )}

      <Drawer isOpen={verSelecionadas} onClose={() => setVerSelecionadas(false)} title={`Notas selecionadas (${selectionCount})`}
        subtitle="De todas as pesquisas e páginas desta consulta" width={isMobile ? '100%' : '560px'}
        footer={<>
          <Button variant="outline" onClick={() => setVerSelecionadas(false)}>Fechar</Button>
          <Button variant="primary" disabled={!selectionCount} onClick={() => { setVerSelecionadas(false); abrirDownload(); }}>
            <Icon name="download" size={16} /> Baixar {notas(selectionCount)}…
          </Button>
        </>}>
        <ListaSelecionadas itens={Array.from(sel.itens.values())} counterpartLabel={counterpartLabel}
          onAbrir={(id) => { setVerSelecionadas(false); openDocument(id); }} onRemover={(id) => sel.remover([id])} />
      </Drawer>

      <Drawer isOpen={filtersDrawer} onClose={() => setFiltersDrawer(false)} title="Filtros avançados" side="right" width="100%"
        footer={<Button variant="primary" onClick={() => setFiltersDrawer(false)}>Ver {total} resultados</Button>}>
        {advanced}
      </Drawer>

      {detail && (
        <Drawer isOpen onClose={() => openDocument(null)} width="560px"
          title={`NFS-e ${detail.number ? `nº ${detail.number}` : ''}`}
          subtitle={detail.issued_at ? `Emissão: ${new Date(detail.issued_at).toLocaleString('pt-BR')}` : undefined}>
          <DocumentDetail doc={detail} direction={direction}
            onView={(mode) => setViewer({ doc: detail, mode })} onDownload={(k) => download(detail, k)}
            extras={<>
              {renderDetailExtras?.(detail, reload, (d) => setDetail(d))}
              {direction === 'ISSUED' && (
                <CancellationPanel doc={detail} onDocumentChanged={() => refreshDetail(detail.id)} />
              )}
            </>} />
        </Drawer>
      )}

      {viewer && (
        <Suspense fallback={null}>
          <DocumentViewer doc={viewer.doc} mode={viewer.mode} onClose={() => setViewer(null)} />
        </Suspense>
      )}
      {bundleScope && <BundleDialog scope={bundleScope} onClose={() => setBundleScope(null)} onRemover={sel.remover} />}
    </div>
  );
};

const ListaSelecionadas: React.FC<{
  itens: NotaSelecionada[];
  counterpartLabel: string;
  onAbrir: (id: string) => void;
  onRemover: (id: string) => void;
}> = ({ itens, counterpartLabel, onAbrir, onRemover }) => {
  if (!itens.length) return <p style={{ fontSize: '0.875rem' }}>Nenhuma nota selecionada.</p>;
  return (
    <ul aria-label="Notas selecionadas" style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      {itens.map((n) => {
        const faltas = [!n.xml_available && 'sem XML original', !n.danfse_available && 'sem DANFSe (PDF)'].filter(Boolean);
        return (
          <li key={n.id} style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '0.625rem 0.75rem',
            display: 'flex', gap: '0.75rem', alignItems: 'flex-start', justifyContent: 'space-between', minWidth: 0 }}>
            <div style={{ minWidth: 0, fontSize: '0.8125rem' }}>
              <button type="button" className="pf-linkbtn" onClick={() => onAbrir(n.id)}>{rotuloNota(n)}</button>
              <div className="ui-mono" style={{ fontSize: '0.6875rem', color: 'var(--color-text-subtle)', overflowWrap: 'anywhere' }}>{n.access_key || ''}</div>
              <div>{counterpartLabel}: {n.counterpart || '—'}{n.counterpart_doc ? ` (${n.counterpart_doc})` : ''}</div>
              <div style={{ color: 'var(--color-text-muted)' }}>
                {n.issued_at ? `Emissão ${new Date(n.issued_at).toLocaleDateString('pt-BR')}` : 'Sem data de emissão'} · {money(n.amount)}
              </div>
              {faltas.length > 0 && (
                <div>Atenção: {faltas.join(' e ')}. Entra no ZIP como pendência no manifesto.</div>
              )}
            </div>
            <Button variant="ghost" size="sm" onClick={() => onRemover(n.id)} aria-label={`Remover ${rotuloNota(n)} da seleção`}>Remover</Button>
          </li>
        );
      })}
    </ul>
  );
};

const DocumentDetail: React.FC<{
  doc: FiscalDocument;
  direction: 'ISSUED' | 'RECEIVED';
  onView: (m: ViewerMode) => void;
  onDownload: (k: 'pdf' | 'xml') => void;
  extras?: React.ReactNode;
}> = ({ doc, direction, onView, onDownload, extras }) => {
  const r = doc.retention_summary;
  const canPdf = !!doc.danfse_available;
  return (
    <div className="pf-stack">
      <div role="group" aria-label="Arquivos da nota" style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
        <Button variant="primary" size="sm" disabled={!canPdf} onClick={() => onView('pdf')}><Icon name="eye" size={16} /> Visualizar PDF</Button>
        <Button variant="outline" size="sm" disabled={!canPdf} onClick={() => onDownload('pdf')}><Icon name="download" size={16} /> Baixar PDF</Button>
        <Button variant="outline" size="sm" disabled={!doc.xml_available} onClick={() => onView('xml')}><Icon name="code" size={16} /> Visualizar XML</Button>
        <Button variant="outline" size="sm" disabled={!doc.xml_available} onClick={() => onDownload('xml')}><Icon name="download" size={16} /> Baixar XML original</Button>
      </div>
      <p style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>
        {canPdf
          ? 'O PDF (DANFSe) é gerado pela plataforma a partir do XML oficial, conforme a NT 008 v1.02; não é arquivo baixado do governo.'
          : doc.simulated ? 'Documento de simulação: sem DANFSe.' : 'XML original indisponível: sem DANFSe.'}
        {doc.danfse_watermark ? ` Marca d’água: ${doc.danfse_watermark}.` : ''}
      </p>

      <dl className="pf-dl">
        <dt>Chave de acesso</dt><dd className="ui-mono">{doc.access_key || '—'}</dd>
        <dt>Situação</dt><dd>{doc.status_code_label || doc.status}</dd>
        <dt>{direction === 'ISSUED' ? 'Tomador' : 'Prestador'}</dt>
        <dd>{(direction === 'ISSUED' ? doc.recipient : doc.issuer)?.name || '—'} {(direction === 'ISSUED' ? doc.recipient : doc.issuer)?.cnpj || ''}</dd>
        <dt>Competência</dt><dd>{doc.competence || '—'}</dd>
        <dt>Valor do serviço</dt><dd className="ui-num" style={{ textAlign: 'left' }}>{money(doc.amount)}</dd>
        <dt>Valor líquido</dt><dd className="ui-num" style={{ textAlign: 'left' }}>{money(doc.net_amount)}</dd>
        {doc.substitution?.substitutes_access_key && <><dt>Substitui</dt><dd className="ui-mono">{doc.substitution.substitutes_access_key}</dd></>}
        {doc.substitution?.substituted_by_access_key && <><dt>Substituída por</dt><dd className="ui-mono">{doc.substitution.substituted_by_access_key}</dd></>}
      </dl>

      {r && (
        <section aria-labelledby="ret-title" style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '0.75rem' }}>
          <h3 id="ret-title" style={{ fontSize: '0.875rem', marginBottom: '0.5rem' }}>
            Retenções{' '}
            <Badge status={r.classification === 'WITH' ? 'INFO' : r.classification === 'WITHOUT' ? 'VALID' : 'UNKNOWN'}
              customLabel={r.classification === 'WITH' ? 'Com retenção' : r.classification === 'WITHOUT' ? 'Sem retenção' : 'Informação desconhecida'} />
          </h3>
          <dl className="pf-dl">
            <dt>ISS retido</dt><dd>{r.iss.retained ? money(r.iss.amount) : 'Não retido'}</dd>
            <dt>INSS / CP</dt><dd>{money(r.inss)}</dd>
            <dt>IRRF</dt><dd>{money(r.irrf)}</dd>
            <dt>Contribuições retidas</dt>
            <dd>{money(r.contributions.amount)}{r.contributions.label ? ` — ${r.contributions.label}` : ''}
              <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>{r.contributions.note}</div></dd>
            <dt>Total retido (vTotalRet)</dt><dd><strong>{money(r.total_withheld)}</strong></dd>
            {r.contributions.code === '1' ? (
              <><dt>PIS/COFINS (modalidade 1)</dt><dd>Incluídos nas contribuições retidas, conforme a NT 008 v1.02.</dd></>
            ) : (
              <><dt>PIS informado (não retido)</dt><dd>{money(r.informed_not_withheld.pis)}</dd>
                <dt>COFINS informado (não retido)</dt><dd>{money(r.informed_not_withheld.cofins)}</dd></>
            )}
          </dl>
        </section>
      )}
      {doc.ibs_cbs_official && <IbsCbsOficialSection of={doc.ibs_cbs_official} />}
      {doc.description && (
        <section aria-label="Descrição do serviço">
          <h3 style={{ fontSize: '0.875rem', marginBottom: '0.25rem' }}>Descrição do serviço</h3>
          <p style={{ fontSize: '0.8125rem', whiteSpace: 'pre-wrap' }}>{doc.description}</p>
        </section>
      )}
      {extras}
    </div>
  );
};
