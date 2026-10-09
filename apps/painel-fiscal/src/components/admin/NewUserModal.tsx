import React, { useEffect, useState } from 'react';
import { Alert, Button, Input, Modal, Select } from '@ctrl-fiscal/ui';
import type { AdminCompany, AdminMeta, CreateAdminUserResponse } from '@ctrl-fiscal/api-client';
import { useFiscal, logSafe } from '../../context/FiscalContext.js';
import { PermissionCheckboxes, friendlyAdminError } from './adminShared.js';

interface DraftMembership {
  company_id: string;
  permissions: string[];
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const NewUserModal: React.FC<{
  isOpen: boolean;
  meta: AdminMeta;
  companies: AdminCompany[];
  onClose: () => void;
  onCreated: (res: CreateAdminUserResponse) => void;
}> = ({ isOpen, meta, companies, onClose, onCreated }) => {
  const { clients } = useFiscal();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [memberships, setMemberships] = useState<DraftMembership[]>([]);
  const [addCompany, setAddCompany] = useState('');
  const [errors, setErrors] = useState<{ name?: string; email?: string; memberships?: string }>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setName('');
    setEmail('');
    // Admin de uma única empresa: já sugere o vínculo com ela.
    setMemberships(!meta.is_super_admin && companies.length === 1 ? [{ company_id: companies[0].id, permissions: ['view'] }] : []);
    setAddCompany('');
    setErrors({});
    setSubmitError(null);
  }, [isOpen, meta.is_super_admin, companies]);

  const available = companies.filter((c) => !memberships.some((m) => m.company_id === c.id));
  const companyName = (id: string) => companies.find((c) => c.id === id)?.name || id;

  const addMembership = () => {
    const id = addCompany || available[0]?.id;
    if (!id) return;
    setMemberships((ms) => [...ms, { company_id: id, permissions: ['view'] }]);
    setAddCompany('');
    setErrors((e) => ({ ...e, memberships: undefined }));
  };

  const validate = () => {
    const next: typeof errors = {};
    if (!name.trim()) next.name = 'Informe o nome.';
    if (!email.trim()) next.email = 'Informe o e-mail.';
    else if (!EMAIL_RE.test(email.trim())) next.email = 'E-mail inválido.';
    if (!meta.is_super_admin && memberships.length === 0) next.memberships = 'Vincule o usuário a pelo menos uma empresa que você administra.';
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);
    if (!validate()) return;
    setSaving(true);
    try {
      const res = await clients.core.createAdminUser({
        name: name.trim(),
        email: email.trim(),
        memberships: memberships.map((m) => ({ company_id: m.company_id, permissions: m.permissions })),
      });
      onCreated(res);
    } catch (err) {
      logSafe('admin: criar usuário', err);
      const f = friendlyAdminError(err, 'Não foi possível criar o usuário.');
      if (f.code === 'EMAIL_IN_USE') setErrors((x) => ({ ...x, email: f.message }));
      else setSubmitError(f.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={() => !saving && onClose()} title="Novo usuário" maxWidth="640px"
      description="A pessoa receberá um link de ativação para definir a própria senha."
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={saving}>Cancelar</Button>
          <Button variant="primary" type="submit" form="pf-new-user-form" isLoading={saving}>Criar usuário</Button>
        </>
      }>
      <form id="pf-new-user-form" onSubmit={submit} noValidate className="pf-stack">
        {submitError && <Alert variant="error" title="Não foi possível criar">{submitError}</Alert>}
        <Input label="Nome" value={name} onChange={(e) => setName(e.target.value)} required autoComplete="off"
          error={errors.name} maxLength={200} />
        <Input label="E-mail" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="off"
          error={errors.email} maxLength={254} />

        <section aria-labelledby="pf-new-user-memberships" className="pf-stack" style={{ gap: '0.75rem' }}>
          <div>
            <h3 id="pf-new-user-memberships" style={{ margin: 0, fontSize: '0.9375rem' }}>Acesso às empresas</h3>
            <p style={{ margin: '0.25rem 0 0', fontSize: '0.8125rem', color: 'var(--color-text-muted)' }}>
              {meta.is_super_admin
                ? 'Opcional para SUPER_ADMIN: o usuário pode ser criado sem empresa e vinculado depois.'
                : 'Obrigatório: escolha ao menos uma empresa que você administra.'}
            </p>
          </div>

          {memberships.length === 0 && (
            <p style={{ margin: 0, fontSize: '0.8125rem', color: 'var(--color-text-subtle)' }}>Nenhuma empresa vinculada.</p>
          )}

          {memberships.map((m) => (
            <div key={m.company_id} style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '0.75rem',
              display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '0.5rem', flexWrap: 'wrap' }}>
                <strong style={{ fontSize: '0.875rem', overflowWrap: 'anywhere', minWidth: 0 }}>{companyName(m.company_id)}</strong>
                <Button variant="ghost" size="sm" aria-label={`Remover vínculo com ${companyName(m.company_id)}`}
                  onClick={() => setMemberships((ms) => ms.filter((x) => x.company_id !== m.company_id))}>Remover</Button>
              </div>
              <PermissionCheckboxes meta={meta} idPrefix={`pf-new-perm-${m.company_id}`} legend={`Permissões em ${companyName(m.company_id)}`}
                value={m.permissions}
                onChange={(next) => setMemberships((ms) => ms.map((x) => (x.company_id === m.company_id ? { ...x, permissions: next } : x)))} />
            </div>
          ))}

          {available.length > 0 && (
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <div style={{ flex: '1 1 14rem', minWidth: 0 }}>
                <Select label="Adicionar empresa" value={addCompany || available[0].id} onChange={(e) => setAddCompany(e.target.value)}
                  options={available.map((c) => ({ value: c.id, label: `${c.name} (${c.cnpj})` }))} />
              </div>
              <Button variant="secondary" onClick={addMembership}>Adicionar vínculo</Button>
            </div>
          )}
          {companies.length === 0 && (
            <p style={{ margin: 0, fontSize: '0.8125rem', color: 'var(--color-text-subtle)' }}>Não há empresas disponíveis no seu escopo.</p>
          )}
          {errors.memberships && (
            <span role="alert" style={{ fontSize: '0.75rem', color: 'var(--color-danger)', fontWeight: 500 }}>{errors.memberships}</span>
          )}
        </section>
      </form>
    </Modal>
  );
};
