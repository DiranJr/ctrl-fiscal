import React, { useCallback, useEffect, useState } from 'react';
import { Badge } from '@ctrl-fiscal/ui';
import { ProblemDetailsException } from '@ctrl-fiscal/api-client';
import type { AdminMeta, AdminUser } from '@ctrl-fiscal/api-client';

/* ------------------------------------------------------------------------------------------------
 * Erros do backend → mensagens em português (nunca exibe tokens, e-mails ou detalhes técnicos).
 * ---------------------------------------------------------------------------------------------- */

const ERROR_MESSAGES: Record<string, string> = {
  ADMIN_FORBIDDEN: 'Você não tem permissão para administrar usuários.',
  SUPER_ADMIN_REQUIRED: 'Somente um SUPER_ADMIN pode conceder ou remover SUPER_ADMIN.',
  SELF_DEACTIVATION: 'Você não pode desativar a sua própria conta.',
  LAST_SUPER_ADMIN: 'Não é possível: este é o último SUPER_ADMIN ativo. Conceda SUPER_ADMIN a outra pessoa antes.',
  VERSION_CONFLICT: 'Este usuário foi alterado por outra pessoa enquanto você editava. Recarregue para ver a versão atual.',
  EMAIL_IN_USE: 'Já existe um usuário com este e-mail.',
  USER_OUTSIDE_SCOPE: 'Este usuário tem vínculos com empresas fora do seu escopo: somente um SUPER_ADMIN pode fazer esta alteração.',
  SELF_MEMBERSHIP: 'Você não pode alterar o seu próprio vínculo com a empresa. Peça a outro administrador.',
  USER_NOT_ACTIVE: 'SUPER_ADMIN só pode ser concedido a uma conta ativa que já definiu a senha.',
  USER_INACTIVE: 'Reative a conta antes de gerar um link de acesso.',
  USER_NOT_FOUND: 'Usuário não encontrado (ou fora do seu escopo).',
  COMPANY_FORBIDDEN: 'Empresa fora do seu escopo de administração.',
  COMPANY_NOT_FOUND: 'Empresa não encontrada.',
  MEMBERSHIP_REQUIRED: 'Vincule o usuário a pelo menos uma empresa que você administra.',
  MEMBERSHIP_NOT_FOUND: 'Este vínculo já não existe. Recarregue os dados do usuário.',
  PERMISSION_VIEW_REQUIRED: "Todo vínculo precisa da permissão de visualização. Para tirar o acesso, remova o vínculo.",
  PERMISSION_UNKNOWN: 'Uma das permissões escolhidas não existe.',
};

export interface FriendlyError {
  code: string;
  message: string;
}

export function friendlyAdminError(err: unknown, fallback = 'Não foi possível concluir a operação.'): FriendlyError {
  if (err instanceof ProblemDetailsException) {
    const code = err.problem.code || '';
    if (code && ERROR_MESSAGES[code]) return { code, message: ERROR_MESSAGES[code] };
    if (err.problem.status === 401) return { code: 'UNAUTHORIZED', message: 'Sua sessão expirou. Entre novamente.' };
    if (err.problem.status === 403) return { code: code || 'FORBIDDEN', message: ERROR_MESSAGES.ADMIN_FORBIDDEN };
    if (err.problem.status === 422) {
      const summary = err.formatErrorsSummary();
      return { code: code || 'VALIDATION', message: summary.length ? `Verifique os dados: ${summary.join('; ')}` : err.problem.detail || fallback };
    }
    return { code: code || `HTTP_${err.problem.status}`, message: err.problem.detail || fallback };
  }
  return { code: 'NETWORK', message: 'Falha de comunicação com o servidor. Tente novamente.' };
}

/* ------------------------------------------------------------------------------------------------
 * Badges (texto + ícone; nunca só cor)
 * ---------------------------------------------------------------------------------------------- */

export const UserStatusBadge: React.FC<{ status: string }> = ({ status }) => {
  if (status === 'ACTIVE') return <Badge status="AUTHORIZED" customLabel="Ativo" icon="✓" />;
  if (status === 'INACTIVE') return <Badge status="WITHDRAWN" customLabel="Inativo" icon="✕" />;
  if (status === 'PENDING') return <Badge status="PENDING" customLabel="Ativação pendente" icon="◔" />;
  return <Badge status="UNKNOWN" customLabel={status} icon="?" />;
};

export const SuperAdminBadge: React.FC = () => <Badge status="SCHEDULED" customLabel="SUPER_ADMIN" icon="★" />;

export const STATUS_OPTIONS = [
  { value: '', label: 'Todas as situações' },
  { value: 'ACTIVE', label: 'Ativo' },
  { value: 'PENDING', label: 'Ativação pendente' },
  { value: 'INACTIVE', label: 'Inativo' },
];

/* ------------------------------------------------------------------------------------------------
 * Permissões
 * ---------------------------------------------------------------------------------------------- */

export function permissionLabel(meta: AdminMeta | null, id: string): string {
  return meta?.permissions.find((p) => p.id === id)?.label || id;
}

/** Ordena conforme o catálogo do backend e garante 'view'. */
export function normalizePermissions(meta: AdminMeta | null, perms: Iterable<string>): string[] {
  const set = new Set(perms);
  set.add('view');
  const order = meta?.permissions.map((p) => p.id) || [];
  return [...order.filter((p) => set.has(p)), ...Array.from(set).filter((p) => !order.includes(p))];
}

/** Grupo de caixas de permissão de um vínculo; 'view' sempre marcada e obrigatória. */
export const PermissionCheckboxes: React.FC<{
  meta: AdminMeta;
  legend: string;
  value: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
  idPrefix: string;
}> = ({ meta, legend, value, onChange, disabled, idPrefix }) => (
  <fieldset style={{ border: 'none', margin: 0, padding: 0, minWidth: 0 }}>
    <legend className="ui-sr-only">{legend}</legend>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem 1rem' }}>
      {meta.permissions.map((p) => {
        const isView = p.id === 'view';
        const checked = isView || value.includes(p.id);
        const id = `${idPrefix}-${p.id}`;
        return (
          <label key={p.id} htmlFor={id}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem', fontSize: '0.8125rem', minHeight: '1.75rem',
              color: 'var(--color-text-main)', cursor: disabled || isView ? 'default' : 'pointer' }}>
            <input id={id} type="checkbox" checked={checked} disabled={disabled || isView}
              aria-describedby={isView ? `${idPrefix}-view-note` : undefined}
              onChange={(e) => {
                const next = new Set(value);
                if (e.target.checked) next.add(p.id);
                else next.delete(p.id);
                onChange(normalizePermissions(meta, next));
              }} />
            {p.label}
          </label>
        );
      })}
    </div>
    <span id={`${idPrefix}-view-note`} style={{ display: 'block', fontSize: '0.75rem', color: 'var(--color-text-subtle)', marginTop: '0.25rem' }}>
      “{permissionLabel(meta, 'view')}” é obrigatória em todo vínculo.
    </span>
  </fieldset>
);

/** Resumo textual dos vínculos visíveis + contagem dos que estão fora do escopo. */
export const MembershipSummary: React.FC<{ user: AdminUser; meta: AdminMeta | null }> = ({ user, meta }) => {
  if (user.memberships.length === 0 && user.other_companies === 0) {
    return <span style={{ color: 'var(--color-text-subtle)' }}>Sem vínculo com empresas</span>;
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.8125rem' }}>
      {user.memberships.map((m) => (
        <div key={m.company_id} style={{ overflowWrap: 'anywhere' }}>
          <strong style={{ fontWeight: 600 }}>{m.company_name}</strong>
          {!m.company_active && <span style={{ color: 'var(--color-text-subtle)' }}> (empresa inativa)</span>}
          <span style={{ color: 'var(--color-text-muted)' }}> — {m.permissions.map((p) => permissionLabel(meta, p)).join(', ')}</span>
        </div>
      ))}
      {user.other_companies > 0 && (
        <div style={{ color: 'var(--color-text-subtle)' }}>
          {user.memberships.length > 0 ? 'e ' : ''}
          {user.other_companies} {user.other_companies === 1 ? 'outra empresa' : 'outras empresas'} fora do seu escopo
        </div>
      )}
    </div>
  );
};

export function formatDateTimePtBr(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
}

/* ------------------------------------------------------------------------------------------------
 * Estado na URL (?q=&status=&company=&perm=&sa=&page=), como em components/documents/urlState.ts
 * ---------------------------------------------------------------------------------------------- */

export interface AdminFilters {
  q: string;
  status: string;
  company: string;
  perm: string;
  sa: '' | 'true' | 'false';
  page: number;
}

export const EMPTY_ADMIN_FILTERS: AdminFilters = { q: '', status: '', company: '', perm: '', sa: '', page: 1 };

function readAdminFilters(): AdminFilters {
  const p = new URLSearchParams(window.location.search);
  const status = p.get('status') || '';
  const sa = p.get('sa');
  return {
    q: p.get('q') || '',
    status: ['ACTIVE', 'INACTIVE', 'PENDING'].includes(status) ? status : '',
    company: p.get('company') || '',
    perm: p.get('perm') || '',
    sa: sa === 'true' || sa === 'false' ? sa : '',
    page: Math.max(1, Number(p.get('page')) || 1),
  };
}

function writeAdminFilters(f: AdminFilters) {
  const url = new URL(window.location.href);
  const keep = ['view', 'company_id'];
  for (const k of Array.from(url.searchParams.keys())) if (!keep.includes(k)) url.searchParams.delete(k);
  const set = (k: string, v: string) => v && url.searchParams.set(k, v);
  set('q', f.q); set('status', f.status); set('company', f.company); set('perm', f.perm); set('sa', f.sa);
  if (f.page > 1) url.searchParams.set('page', String(f.page));
  window.history.replaceState({}, '', url.toString());
}

export function useAdminUrlState() {
  const [filters, setState] = useState<AdminFilters>(readAdminFilters);

  useEffect(() => {
    const onPop = () => setState(readAdminFilters());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  /** Altera filtros (volta à página 1, salvo quando só a página muda). */
  const setFilters = useCallback((patch: Partial<AdminFilters>) => {
    setState((prev) => {
      const onlyPage = Object.keys(patch).length === 1 && 'page' in patch;
      const next = { ...prev, ...patch, page: onlyPage ? patch.page! : 1 };
      writeAdminFilters(next);
      return next;
    });
  }, []);

  return { filters, setFilters };
}
