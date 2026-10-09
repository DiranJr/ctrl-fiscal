import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, ErrorState, Input, Modal, Select, Skeleton } from '@ctrl-fiscal/ui';
import type { AdminCompany, AdminMeta, AdminUser, CredentialLink } from '@ctrl-fiscal/api-client';
import { useFiscal, logSafe } from '../../context/FiscalContext.js';
import {
  PermissionCheckboxes,
  SuperAdminBadge,
  UserStatusBadge,
  formatDateTimePtBr,
  friendlyAdminError,
  normalizePermissions,
  permissionLabel,
} from './adminShared.js';
import { CredentialLinkModal } from './CredentialLinkModal.js';

type Confirm =
  | { kind: 'deactivate' }
  | { kind: 'activate' }
  | { kind: 'remove-membership'; companyId: string; companyName: string }
  | { kind: 'grant-sa' }
  | { kind: 'revoke-sa' }
  | { kind: 'revoke-sessions' }
  | { kind: 'credential-link' };

interface Banner {
  variant: 'success' | 'error' | 'warning';
  message: string;
  conflict?: boolean;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));

const Section: React.FC<{ id: string; title: string; children: React.ReactNode }> = ({ id, title, children }) => (
  <section aria-labelledby={id} style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', paddingTop: '1rem',
    borderTop: '1px solid var(--color-border)' }}>
    <h3 id={id} style={{ margin: 0, fontSize: '0.9375rem', fontWeight: 600 }}>{title}</h3>
    {children}
  </section>
);

const Note: React.FC<{ id?: string; children: React.ReactNode }> = ({ id, children }) => (
  <p id={id} style={{ margin: 0, fontSize: '0.8125rem', color: 'var(--color-text-muted)', lineHeight: 1.45 }}>{children}</p>
);

export const UserDetailDrawer: React.FC<{
  userId: string | null;
  meta: AdminMeta;
  companies: AdminCompany[];
  onClose: () => void;
  /** Avisa a lista que o registro mudou (atualiza a linha sem recarregar tudo). */
  onUserChanged: (user: AdminUser) => void;
}> = ({ userId, meta, companies, onClose, onUserChanged }) => {
  const { clients, user: me } = useFiscal();
  const [user, setUser] = useState<AdminUser | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [identityError, setIdentityError] = useState<{ name?: string; email?: string }>({});
  const [permDraft, setPermDraft] = useState<Record<string, string[]>>({});
  const [addCompany, setAddCompany] = useState('');
  const [addPerms, setAddPerms] = useState<string[]>(['view']);
  const [banner, setBanner] = useState<Banner | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [link, setLink] = useState<CredentialLink | null>(null);

  const onUserChangedRef = useRef(onUserChanged);
  onUserChangedRef.current = onUserChanged;

  const applyUser = useCallback((u: AdminUser, notify = true) => {
    setUser(u);
    setName(u.name);
    setEmail(u.email);
    setIdentityError({});
    setPermDraft(Object.fromEntries(u.memberships.map((m) => [m.company_id, m.permissions])));
    if (notify) onUserChangedRef.current(u);
  }, []);

  const load = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    setLoadError(null);
    try {
      const u = await clients.core.getAdminUser(userId);
      applyUser(u, false);
    } catch (err) {
      logSafe('admin: detalhe do usuário', err);
      setLoadError(friendlyAdminError(err, 'Não foi possível carregar o usuário.').message);
    } finally {
      setLoading(false);
    }
  }, [userId, clients.core, applyUser]);

  useEffect(() => {
    setUser(null);
    setBanner(null);
    setConfirm(null);
    setLink(null);
    setAddCompany('');
    setAddPerms(['view']);
    if (userId) load();
  }, [userId, load]);

  const reload = async () => {
    setBanner(null);
    if (!userId) return;
    try {
      const u = await clients.core.getAdminUser(userId);
      applyUser(u);
      setBanner({ variant: 'success', message: 'Dados recarregados. Revise e refaça a alteração, se ainda for necessária.' });
    } catch (err) {
      logSafe('admin: recarregar usuário', err);
      setBanner({ variant: 'error', message: friendlyAdminError(err).message });
    }
  };

  /** Executa uma ação; o backend devolve o usuário atualizado (com a nova versão). */
  const run = async (key: string, fn: () => Promise<AdminUser>, success: string) => {
    setBusy(key);
    setBanner(null);
    try {
      const u = await fn();
      applyUser(u);
      setBanner({ variant: 'success', message: success });
      return true;
    } catch (err) {
      logSafe(`admin: ${key}`, err);
      const f = friendlyAdminError(err);
      if (key === 'identity' && f.code === 'EMAIL_IN_USE') setIdentityError({ email: f.message });
      setBanner({ variant: f.code === 'VERSION_CONFLICT' ? 'warning' : 'error', message: f.message, conflict: f.code === 'VERSION_CONFLICT' });
      return false;
    } finally {
      setBusy(null);
    }
  };

  const identityDirty = !!user && (name.trim() !== user.name || email.trim().toLowerCase() !== user.email.toLowerCase());
  const permsDirty = !!user && user.memberships.some((m) => !sameSet(permDraft[m.company_id] || [], m.permissions));
  const isDirty = identityDirty || permsDirty;

  const requestClose = () => {
    if (busy) return;
    if (isDirty && !window.confirm('Há alterações não salvas neste usuário. Fechar mesmo assim?')) return;
    onClose();
  };

  const saveIdentity = (e: React.FormEvent) => {
    e.preventDefault();
    if (!user) return;
    const errs: { name?: string; email?: string } = {};
    if (!name.trim()) errs.name = 'Informe o nome.';
    if (!email.trim()) errs.email = 'Informe o e-mail.';
    else if (!EMAIL_RE.test(email.trim())) errs.email = 'E-mail inválido.';
    setIdentityError(errs);
    if (Object.keys(errs).length) return;
    const patch: { expected_version: number; name?: string; email?: string } = { expected_version: user.version };
    if (name.trim() !== user.name) patch.name = name.trim();
    if (email.trim().toLowerCase() !== user.email.toLowerCase()) patch.email = email.trim();
    run('identity', () => clients.core.updateAdminUser(user.id, patch), 'Nome e e-mail atualizados.');
  };

  const doConfirm = async () => {
    if (!user || !confirm) return;
    const c = confirm;
    if (c.kind === 'credential-link') {
      setBusy('credential-link');
      setBanner(null);
      try {
        const l = await clients.core.issueCredentialLink(user.id);
        setConfirm(null);
        setLink(l); // exibido uma única vez, só em memória
      } catch (err) {
        logSafe('admin: link de acesso', err);
        setConfirm(null);
        setBanner({ variant: 'error', message: friendlyAdminError(err, 'Não foi possível gerar o link.').message });
      } finally {
        setBusy(null);
      }
      return;
    }
    switch (c.kind) {
      case 'deactivate':
        await run('status', () => clients.core.updateAdminUser(user.id, { expected_version: user.version, is_active: false }),
          'Usuário desativado. As sessões dele foram encerradas.');
        break;
      case 'activate':
        await run('status', () => clients.core.updateAdminUser(user.id, { expected_version: user.version, is_active: true }),
          'Usuário reativado.');
        break;
      case 'remove-membership':
        await run(`remove-${c.companyId}`, () => clients.core.removeAdminMembership(user.id, c.companyId),
          `Vínculo com ${c.companyName} removido.`);
        break;
      case 'grant-sa':
        await run('super-admin', () => clients.core.setSuperAdmin(user.id, true), 'SUPER_ADMIN concedido.');
        break;
      case 'revoke-sa':
        await run('super-admin', () => clients.core.setSuperAdmin(user.id, false), 'SUPER_ADMIN removido.');
        break;
      case 'revoke-sessions':
        await run('sessions', () => clients.core.revokeUserSessions(user.id), 'Sessões encerradas. O usuário precisará entrar novamente.');
        break;
    }
    setConfirm(null);
  };

  const isSelf = !!user && !!me && user.id === me.id;
  const canIdentity = !!user?.can_manage_identity;
  const selfMembershipLocked = isSelf && !meta.is_super_admin;
  const addable = user ? companies.filter((c) => !user.memberships.some((m) => m.company_id === c.id)) : [];
  const addTarget = addCompany || addable[0]?.id || '';
  const addTargetName = companies.find((c) => c.id === addTarget)?.name || '';

  const confirmText = (() => {
    if (!user || !confirm) return null;
    switch (confirm.kind) {
      case 'deactivate':
        return { title: 'Desativar usuário?', body: `${user.name} não conseguirá mais entrar e todas as sessões abertas serão encerradas. Você pode reativá-lo depois.`, action: 'Desativar', danger: true };
      case 'activate':
        return { title: 'Reativar usuário?', body: `${user.name} voltará a poder entrar com a senha atual (ou pelo link de ativação, se ainda não tiver senha).`, action: 'Reativar', danger: false };
      case 'remove-membership':
        return { title: 'Remover vínculo?', body: `${user.name} perderá todo o acesso aos dados de ${confirm.companyName}.`, action: 'Remover vínculo', danger: true };
      case 'grant-sa':
        return { title: 'Conceder SUPER_ADMIN?', body: 'SUPER_ADMIN administra usuários de todas as empresas (cria, desativa, altera vínculos e concede SUPER_ADMIN). Ele NÃO dá acesso aos documentos fiscais das empresas: esse acesso continua dependendo dos vínculos.', action: 'Conceder SUPER_ADMIN', danger: false };
      case 'revoke-sa':
        return { title: 'Remover SUPER_ADMIN?', body: `${user.name} deixará de administrar usuários globalmente. Os vínculos com empresas não mudam.`, action: 'Remover SUPER_ADMIN', danger: true };
      case 'revoke-sessions':
        return { title: 'Encerrar sessões?', body: `Todas as sessões abertas de ${user.name} serão encerradas e ele precisará entrar novamente em todos os dispositivos.`, action: 'Encerrar sessões', danger: true };
      case 'credential-link':
        return user.password_set_at
          ? { title: 'Gerar link de redefinição de senha?', body: 'Será gerado um link de uso único para a pessoa definir uma nova senha. Ao usá-lo, todas as sessões dela serão encerradas. O link será exibido somente uma vez.', action: 'Gerar link', danger: false }
          : { title: 'Gerar link de ativação?', body: 'Será gerado um link de uso único para a pessoa definir a primeira senha. O link será exibido somente uma vez.', action: 'Gerar link', danger: false };
    }
  })();

  return (
    <>
      <Drawer isOpen={!!userId} onClose={requestClose} width="560px"
        title={user ? user.name : 'Usuário'} subtitle={user ? user.email : undefined}
        footer={<Button variant="outline" onClick={requestClose} disabled={!!busy}>Fechar</Button>}>
        {loading && !user ? (
          <div aria-busy="true" aria-label="Carregando usuário" className="pf-stack">
            <Skeleton height="1.25rem" width="50%" />
            <Skeleton height="6rem" />
            <Skeleton height="8rem" />
          </div>
        ) : loadError ? (
          <ErrorState message={loadError} onRetry={load} />
        ) : user ? (
          <div className="pf-stack">
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
              <UserStatusBadge status={user.status} />
              {user.is_super_admin && <SuperAdminBadge />}
              {isSelf && <span style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)' }}>(você)</span>}
            </div>

            {banner && (
              <Alert variant={banner.variant}
                action={banner.conflict ? <Button size="sm" variant="outline" onClick={reload}>Recarregar</Button> : undefined}>
                {banner.message}
              </Alert>
            )}

            {!canIdentity && (
              <Alert variant="info" title="Edição limitada">
                Este usuário também tem vínculo com {user.other_companies === 1 ? 'uma empresa' : `${user.other_companies || 'outras'} empresas`} fora
                do seu escopo. Nome, e-mail, situação, sessões e links de acesso só podem ser alterados por um SUPER_ADMIN.
                Você ainda pode ajustar os vínculos com as suas empresas.
              </Alert>
            )}

            <dl className="pf-dl">
              <dt>Senha definida em</dt>
              <dd>{user.password_set_at ? formatDateTimePtBr(user.password_set_at) : 'Ainda não definida'}</dd>
              <dt>Criado em</dt>
              <dd>{formatDateTimePtBr(user.created_at)}</dd>
              <dt>Atualizado em</dt>
              <dd>{formatDateTimePtBr(user.updated_at)}</dd>
            </dl>

            <Section id="pf-admin-sec-identity" title="Identificação">
              <form onSubmit={saveIdentity} noValidate className="pf-stack" style={{ gap: '0.75rem' }}>
                <Input label="Nome" value={name} onChange={(e) => setName(e.target.value)} disabled={!canIdentity || busy === 'identity'}
                  required error={identityError.name} maxLength={200} autoComplete="off"
                  helperText={!canIdentity ? 'Somente SUPER_ADMIN pode alterar (usuário com empresas fora do seu escopo).' : undefined} />
                <Input label="E-mail" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                  disabled={!canIdentity || busy === 'identity'} required error={identityError.email} maxLength={254} autoComplete="off"
                  helperText={!canIdentity ? 'Somente SUPER_ADMIN pode alterar (usuário com empresas fora do seu escopo).' : undefined} />
                {canIdentity && (
                  <div>
                    <Button type="submit" variant="primary" size="sm" disabled={!identityDirty} isLoading={busy === 'identity'}>
                      Salvar nome e e-mail
                    </Button>
                  </div>
                )}
              </form>
            </Section>

            <Section id="pf-admin-sec-status" title="Situação da conta">
              <Note>
                {user.is_active
                  ? 'Conta ativa. Ao desativar, a pessoa não consegue mais entrar e as sessões abertas são encerradas.'
                  : 'Conta desativada: a pessoa não consegue entrar.'}
              </Note>
              {isSelf && user.is_active && <Note>Você não pode desativar a sua própria conta.</Note>}
              <div>
                {user.is_active ? (
                  <Button variant="danger" size="sm" disabled={!canIdentity || isSelf || !!busy} isLoading={busy === 'status'}
                    onClick={() => setConfirm({ kind: 'deactivate' })}>Desativar usuário</Button>
                ) : (
                  <Button variant="secondary" size="sm" disabled={!canIdentity || !!busy} isLoading={busy === 'status'}
                    onClick={() => setConfirm({ kind: 'activate' })}>Reativar usuário</Button>
                )}
              </div>
            </Section>

            <Section id="pf-admin-sec-memberships" title="Acesso às empresas">
              {selfMembershipLocked && <Note>Você não pode alterar os seus próprios vínculos. Peça a outro administrador.</Note>}
              {user.memberships.length === 0 && (
                <Note>Nenhum vínculo com empresas do seu escopo.</Note>
              )}
              {user.memberships.map((m) => {
                const draft = permDraft[m.company_id] || m.permissions;
                const dirty = !sameSet(draft, m.permissions);
                return (
                  <div key={m.company_id} style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '0.75rem',
                    display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                    <div style={{ minWidth: 0 }}>
                      <strong style={{ fontSize: '0.875rem', overflowWrap: 'anywhere' }}>{m.company_name}</strong>
                      <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>
                        {m.company_cnpj}{!m.company_active && ' · empresa inativa'}
                      </div>
                    </div>
                    <PermissionCheckboxes meta={meta} idPrefix={`pf-perm-${m.company_id}`} legend={`Permissões em ${m.company_name}`}
                      value={draft} disabled={selfMembershipLocked || !!busy}
                      onChange={(next) => setPermDraft((d) => ({ ...d, [m.company_id]: next }))} />
                    <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                      <Button size="sm" variant="primary" disabled={!dirty || selfMembershipLocked || (!!busy && busy !== `perm-${m.company_id}`)}
                        isLoading={busy === `perm-${m.company_id}`}
                        aria-label={`Salvar permissões em ${m.company_name}`}
                        onClick={() => run(`perm-${m.company_id}`, () => clients.core.setAdminMembership(user.id, m.company_id, normalizePermissions(meta, draft)),
                          `Permissões em ${m.company_name} atualizadas.`)}>
                        Salvar permissões
                      </Button>
                      {dirty && (
                        <Button size="sm" variant="ghost" disabled={!!busy}
                          onClick={() => setPermDraft((d) => ({ ...d, [m.company_id]: m.permissions }))}>Desfazer</Button>
                      )}
                      <Button size="sm" variant="outline" disabled={selfMembershipLocked || !!busy} isLoading={busy === `remove-${m.company_id}`}
                        aria-label={`Remover vínculo com ${m.company_name}`}
                        onClick={() => setConfirm({ kind: 'remove-membership', companyId: m.company_id, companyName: m.company_name })}>
                        Remover vínculo
                      </Button>
                    </div>
                  </div>
                );
              })}
              {user.other_companies > 0 && (
                <Note>
                  E {user.other_companies} {user.other_companies === 1 ? 'outra empresa' : 'outras empresas'} fora do seu escopo
                  (não exibidas).
                </Note>
              )}

              {addable.length > 0 && !selfMembershipLocked && (
                <div style={{ border: '1px dashed var(--color-border-hover)', borderRadius: 'var(--radius-md)', padding: '0.75rem',
                  display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  <Select label="Vincular a outra empresa" value={addTarget} onChange={(e) => setAddCompany(e.target.value)}
                    options={addable.map((c) => ({ value: c.id, label: `${c.name} (${c.cnpj})` }))} disabled={!!busy} />
                  <PermissionCheckboxes meta={meta} idPrefix="pf-perm-new" legend={`Permissões na nova empresa ${addTargetName}`}
                    value={addPerms} onChange={setAddPerms} disabled={!!busy} />
                  <div>
                    <Button size="sm" variant="secondary" disabled={!addTarget || (!!busy && busy !== 'add')} isLoading={busy === 'add'}
                      onClick={async () => {
                        const ok = await run('add', () => clients.core.setAdminMembership(user.id, addTarget, normalizePermissions(meta, addPerms)),
                          `Vínculo com ${addTargetName} criado.`);
                        if (ok) {
                          setAddCompany('');
                          setAddPerms(['view']);
                        }
                      }}>
                      Adicionar vínculo
                    </Button>
                  </div>
                </div>
              )}
            </Section>

            {meta.is_super_admin && (
              <Section id="pf-admin-sec-sa" title="SUPER_ADMIN">
                <Note>
                  SUPER_ADMIN administra usuários de todas as empresas. Ele <strong>não</strong> dá acesso aos documentos fiscais
                  das empresas — esse acesso depende dos vínculos acima.
                </Note>
                {!user.is_super_admin && user.status !== 'ACTIVE' && (
                  <Note>Só é possível conceder a uma conta ativa que já definiu a senha.</Note>
                )}
                <div>
                  {user.is_super_admin ? (
                    <Button size="sm" variant="outline" disabled={!!busy} isLoading={busy === 'super-admin'}
                      onClick={() => setConfirm({ kind: 'revoke-sa' })}>Remover SUPER_ADMIN</Button>
                  ) : (
                    <Button size="sm" variant="secondary" disabled={!!busy || user.status !== 'ACTIVE'} isLoading={busy === 'super-admin'}
                      onClick={() => setConfirm({ kind: 'grant-sa' })}>Conceder SUPER_ADMIN</Button>
                  )}
                </div>
              </Section>
            )}

            <Section id="pf-admin-sec-access" title="Sessões e acesso">
              <dl className="pf-dl">
                <dt>Sessões ativas</dt>
                <dd>{user.active_sessions}</dd>
              </dl>
              {!user.is_active && <Note>Reative a conta para gerar um link de acesso.</Note>}
              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                <Button size="sm" variant="outline" disabled={!canIdentity || !!busy || user.active_sessions === 0}
                  isLoading={busy === 'sessions'} onClick={() => setConfirm({ kind: 'revoke-sessions' })}>
                  Encerrar sessões
                </Button>
                <Button size="sm" variant="secondary" disabled={!canIdentity || !user.is_active || !!busy}
                  isLoading={busy === 'credential-link'} onClick={() => setConfirm({ kind: 'credential-link' })}>
                  {user.password_set_at ? 'Gerar link de redefinição de senha' : 'Gerar link de ativação'}
                </Button>
              </div>
            </Section>
          </div>
        ) : null}
      </Drawer>

      <Modal isOpen={!!confirm && !!confirmText} onClose={() => !busy && setConfirm(null)} title={confirmText?.title || ''}
        footer={
          <>
            <Button variant="outline" onClick={() => setConfirm(null)} disabled={!!busy}>Cancelar</Button>
            <Button variant={confirmText?.danger ? 'danger' : 'primary'} onClick={doConfirm} isLoading={!!busy}>
              {confirmText?.action}
            </Button>
          </>
        }>
        <p style={{ margin: 0, fontSize: '0.875rem', lineHeight: 1.5 }}>{confirmText?.body}</p>
      </Modal>

      <CredentialLinkModal link={link} personName={user?.name || ''} onClose={() => setLink(null)} />
    </>
  );
};
