import React, { useEffect, useState } from 'react';
import type { ServiceProfile } from '@ctrl-fiscal/api-client';
import { Badge, Select } from '@ctrl-fiscal/ui';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { Notice } from './screen/Notice.js';

const SITUACAO: Record<string, string> = { APPROVED: 'aprovado', PENDING_REVIEW: 'pendente de revisão' };

/**
 * "Aplicar perfil de serviço": preenche os campos que o perfil define e vincula o perfil ao rascunho (o servidor
 * registra a versão). Perfis pendentes de revisão ajudam a preparar o rascunho, mas o agendamento fica bloqueado até
 * a aprovação. Os valores aplicados continuam editáveis; campos que o perfil não define não são alterados.
 */
export const ServiceProfilePicker: React.FC<{
  onApply: (p: ServiceProfile) => void;
  onClear?: () => void;
  selectedId?: string | null;
}> = ({ onApply, onClear, selectedId = null }) => {
  const { clients, currentCompany } = useFiscal();
  const [profiles, setProfiles] = useState<ServiceProfile[] | null>(null);
  const [applied, setApplied] = useState<string>(selectedId || '');

  useEffect(() => { setApplied(selectedId || ''); }, [selectedId]);

  useEffect(() => {
    if (!currentCompany) return;
    let alive = true;
    clients.core.listServiceProfiles(currentCompany.id)
      .then((list) => { if (alive) setProfiles(list.filter((p) => p.status !== 'INACTIVE')); })
      .catch((err) => { logSafe('perfis-servico', err); if (alive) setProfiles([]); });
    return () => { alive = false; };
  }, [clients.core, currentCompany]);

  if (profiles === null) return null;
  if (!profiles.length) {
    return <p className="pf-muted">Nenhum perfil de serviço cadastrado (Configurações → Perfis de serviço).</p>;
  }
  const current = profiles.find((p) => p.id === applied);
  return (
    <div className="pf-form-stack">
      <div className="pf-form-grid">
        <Select label="Aplicar perfil de serviço" value={applied}
          onChange={(e) => {
            const p = profiles.find((x) => x.id === e.target.value);
            setApplied(e.target.value);
            if (p) onApply(p);
            else onClear?.();
          }}
          helperText="Opcional. Preenche os códigos e a tributação definidos no perfil."
          options={[{ value: '', label: applied ? 'Desvincular o perfil' : 'Selecione um perfil…' },
            ...profiles.map((p) => ({ value: p.id, label: `${p.name} (${p.national_code}) — ${SITUACAO[p.status] || p.status}` }))]} />
      </div>
      {current && (
        <Notice variant={current.status === 'APPROVED' ? 'info' : 'warning'} live>
          <span style={{ display: 'inline-flex', gap: '0.375rem', alignItems: 'center', flexWrap: 'wrap' }}>
            Perfil “{current.name}” aplicado (versão {current.version})
            <Badge status={current.status === 'APPROVED' ? 'VALID' : 'PROCESSING'}
              customLabel={current.status === 'APPROVED' ? 'Aprovado' : 'Pendente de revisão'} size="sm" />
          </span>
          {current.status !== 'APPROVED' && (
            <div>Pode preparar o rascunho, mas o agendamento só é liberado depois da aprovação do perfil em
              Configurações → Perfis de serviço.</div>
          )}
          <div>Os valores aplicados continuam editáveis.</div>
        </Notice>
      )}
    </div>
  );
};
