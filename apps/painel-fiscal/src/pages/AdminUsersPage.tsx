import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Column,
  ErrorState,
  Icon,
  Input,
  PageHeader,
  Select,
  Table,
  useDebouncedValue,
} from '@ctrl-fiscal/ui';
import type { AdminCompany, AdminMeta, AdminUser, AdminUserFilter, CreateAdminUserResponse, CredentialLink } from '@ctrl-fiscal/api-client';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import {
  EMPTY_ADMIN_FILTERS,
  MembershipSummary,
  STATUS_OPTIONS,
  SuperAdminBadge,
  UserStatusBadge,
  friendlyAdminError,
  useAdminUrlState,
} from '../components/admin/adminShared.js';
import { NewUserModal } from '../components/admin/NewUserModal.js';
import { UserDetailDrawer } from '../components/admin/UserDetailDrawer.js';
import { CredentialLinkModal } from '../components/admin/CredentialLinkModal.js';

const PAGE_SIZE = 25;

/** Administração → Usuários. Não depende da empresa ativa; o escopo vem de /admin/meta (o backend sempre valida). */
export const AdminUsersPage: React.FC = () => {
  const { clients, showToast } = useFiscal();
  const { filters, setFilters } = useAdminUrlState();
  const debouncedQ = useDebouncedValue(filters.q, 350);

  const [meta, setMeta] = useState<AdminMeta | null>(null);
  const [companies, setCompanies] = useState<AdminCompany[]>([]);
  const [metaError, setMetaError] = useState<{ forbidden: boolean; message: string } | null>(null);
  const [metaLoading, setMetaLoading] = useState(true);

  const [items, setItems] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [newOpen, setNewOpen] = useState(false);
  const [openUserId, setOpenUserId] = useState<string | null>(null);
  /** Link recém-gerado: só em memória, descartado ao fechar o diálogo. */
  const [created, setCreated] = useState<{ link: CredentialLink; name: string } | null>(null);

  const loadMeta = useCallback(async () => {
    setMetaLoading(true);
    setMetaError(null);
    try {
      const [m, cs] = await Promise.all([clients.core.getAdminMeta(), clients.core.getAdminCompanies()]);
      setMeta(m);
      setCompanies(cs);
    } catch (err) {
      logSafe('admin: escopo', err);
      const f = friendlyAdminError(err, 'Não foi possível carregar a administração de usuários.');
      setMetaError({ forbidden: f.code === 'ADMIN_FORBIDDEN' || f.code === 'FORBIDDEN', message: f.message });
    } finally {
      setMetaLoading(false);
    }
  }, [clients.core]);

  useEffect(() => {
    loadMeta();
  }, [loadMeta]);

  const superAdminFilter = meta?.is_super_admin && filters.sa ? filters.sa === 'true' : undefined;

  useEffect(() => {
    if (!meta) return;
    let cancelled = false;
    setListLoading(true);
    setListError(null);
    clients.core
      .listAdminUsers({
        query: debouncedQ.trim() || undefined,
        status: (filters.status || undefined) as AdminUserFilter['status'],
        company_id: filters.company || undefined,
        permission: filters.perm || undefined,
        super_admin: superAdminFilter,
        page: filters.page,
        page_size: PAGE_SIZE,
      })
      .then((res) => {
        if (cancelled) return;
        setItems(res.items);
        setTotal(res.total);
      })
      .catch((err) => {
        if (cancelled) return;
        logSafe('admin: lista de usuários', err);
        setItems([]);
        setTotal(0);
        setListError(friendlyAdminError(err, 'Não foi possível carregar os usuários.').message);
      })
      .finally(() => {
        if (!cancelled) setListLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [meta, clients.core, debouncedQ, filters.status, filters.company, filters.perm, superAdminFilter, filters.page, reloadKey]);

  const onUserChanged = useCallback((u: AdminUser) => {
    setItems((list) => list.map((x) => (x.id === u.id ? u : x)));
  }, []);

  const onCreated = (res: CreateAdminUserResponse) => {
    setNewOpen(false);
    setCreated({ link: res.credential_link, name: res.user.name });
    showToast('Usuário criado.', 'success');
    setReloadKey((k) => k + 1);
  };

  const activeFilters = [filters.q, filters.status, filters.company, filters.perm, meta?.is_super_admin ? filters.sa : '']
    .filter(Boolean).length;

  if (metaLoading) {
    return (
      <div className="pf-stack">
        <PageHeader title="Usuários" description="Administração de acessos" />
        <Table<AdminUser> caption="Usuários" columns={[{ key: 'name', header: 'Usuário' }, { key: 'status', header: 'Situação' }]}
          data={[]} keyExtractor={(u) => u.id} isLoading />
      </div>
    );
  }

  if (metaError || !meta) {
    return (
      <div className="pf-stack">
        <PageHeader title="Usuários" description="Administração de acessos" />
        <ErrorState title={metaError?.forbidden ? 'Acesso restrito' : 'Não foi possível carregar'}
          message={metaError?.message || 'Tente novamente.'} onRetry={metaError?.forbidden ? undefined : loadMeta} />
      </div>
    );
  }

  const permOptions = [{ value: '', label: 'Todas as permissões' }, ...meta.permissions.map((p) => ({ value: p.id, label: p.label }))];
  const companyOptions = [{ value: '', label: 'Todas as empresas' }, ...companies.map((c) => ({ value: c.id, label: `${c.name} (${c.cnpj})` }))];

  const columns: Column<AdminUser>[] = [
    {
      key: 'user',
      header: 'Usuário',
      render: (u) => (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.125rem', minWidth: 0 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: '0.375rem', flexWrap: 'wrap' }}>
            <button type="button" className="pf-linkbtn" style={{ textAlign: 'left', overflowWrap: 'anywhere' }}
              aria-label={`Abrir ${u.name}`}
              onClick={(e) => {
                e.stopPropagation();
                setOpenUserId(u.id);
              }}>{u.name}</button>
            {u.is_super_admin && <SuperAdminBadge />}
          </span>
          <span style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', overflowWrap: 'anywhere' }}>{u.email}</span>
        </div>
      ),
    },
    { key: 'status', header: 'Situação', render: (u) => <UserStatusBadge status={u.status} /> },
    { key: 'memberships', header: 'Empresas e permissões', hideOnMobile: true, render: (u) => <MembershipSummary user={u} meta={meta} /> },
    { key: 'sessions', header: 'Sessões ativas', numeric: true, hideOnMobile: true, render: (u) => u.active_sessions },
  ];

  return (
    <div className="pf-stack">
      <PageHeader
        title="Usuários"
        description={meta.is_super_admin
          ? 'Você é SUPER_ADMIN: administra usuários de todas as empresas.'
          : `Você administra usuários de ${companies.length === 1 ? '1 empresa' : `${companies.length} empresas`}.`}
        primaryAction={
          <Button variant="primary" onClick={() => setNewOpen(true)}>
            <Icon name="plus" /> Novo usuário
          </Button>
        }
      />

      <form role="search" aria-label="Filtrar usuários" className="pf-filterbar" onSubmit={(e) => e.preventDefault()}>
        <Input label="Buscar por nome ou e-mail" type="search" value={filters.q} autoComplete="off"
          onChange={(e) => setFilters({ q: e.target.value })} />
        <Select label="Situação" value={filters.status} options={STATUS_OPTIONS}
          onChange={(e) => setFilters({ status: e.target.value })} />
        <Select label="Empresa" value={filters.company} options={companyOptions}
          onChange={(e) => setFilters({ company: e.target.value })} />
        <Select label="Permissão" value={filters.perm} options={permOptions}
          onChange={(e) => setFilters({ perm: e.target.value })} />
        {meta.is_super_admin && (
          <Select label="SUPER_ADMIN" value={filters.sa}
            options={[{ value: '', label: 'Todos' }, { value: 'true', label: 'Somente SUPER_ADMIN' }, { value: 'false', label: 'Sem SUPER_ADMIN' }]}
            onChange={(e) => setFilters({ sa: e.target.value as '' | 'true' | 'false' })} />
        )}
        {activeFilters > 0 && (
          <div>
            <Button variant="ghost" onClick={() => setFilters({ ...EMPTY_ADMIN_FILTERS })}>Limpar filtros ({activeFilters})</Button>
          </div>
        )}
      </form>

      {listError ? (
        <Alert variant="error" title="Não foi possível carregar os usuários"
          action={<Button size="sm" variant="outline" onClick={() => setReloadKey((k) => k + 1)}>Tentar novamente</Button>}>
          {listError}
        </Alert>
      ) : (
        <Table<AdminUser>
          caption="Usuários no seu escopo de administração"
          columns={columns}
          data={items}
          keyExtractor={(u) => u.id}
          isLoading={listLoading}
          pageSize={PAGE_SIZE}
          page={filters.page}
          total={total}
          onPageChange={(p) => setFilters({ page: p })}
          onRowClick={(u) => setOpenUserId(u.id)}
          emptyState={
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.5rem' }}>
              <strong style={{ color: 'var(--color-text-main)' }}>
                {activeFilters > 0 ? 'Nenhum usuário encontrado com estes filtros.' : 'Nenhum usuário no seu escopo ainda.'}
              </strong>
              {activeFilters > 0 ? (
                <Button size="sm" variant="outline" onClick={() => setFilters({ ...EMPTY_ADMIN_FILTERS })}>Limpar filtros</Button>
              ) : (
                <Button size="sm" variant="primary" onClick={() => setNewOpen(true)}>Novo usuário</Button>
              )}
            </div>
          }
        />
      )}

      <NewUserModal isOpen={newOpen} meta={meta} companies={companies} onClose={() => setNewOpen(false)} onCreated={onCreated} />

      <UserDetailDrawer userId={openUserId} meta={meta} companies={companies} onClose={() => setOpenUserId(null)}
        onUserChanged={onUserChanged} />

      <CredentialLinkModal link={created?.link || null} personName={created?.name || ''} onClose={() => setCreated(null)} />
    </div>
  );
};

export default AdminUsersPage;
