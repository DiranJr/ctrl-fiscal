import React, { useCallback, useEffect, useState } from 'react';
import { Badge, Button } from '@ctrl-fiscal/ui';
import type { FiscalProfileOut, ProviderDataApplyResult, ProviderDataFromDocuments } from '@ctrl-fiscal/api-client';
import { useFiscal, logSafe } from '../../context/FiscalContext.js';
import { Notice } from '../screen/Notice.js';

const ROTULO: Record<string, string> = {
  municipal_registration: 'Inscrição municipal', phone: 'Telefone', email: 'E-mail', postal_code: 'CEP',
  municipality_code: 'Município (IBGE)', 'address.street': 'Logradouro', 'address.number': 'Número',
  'address.complement': 'Complemento', 'address.district': 'Bairro', 'address.city': 'Cidade', 'address.state': 'UF',
};

function atual(p: FiscalProfileOut | null, campo: string): string {
  if (!p) return '';
  if (campo.startsWith('address.')) return String((p.address as Record<string, unknown> | null | undefined)?.[campo.slice(8)] ?? '');
  return String((p as unknown as Record<string, unknown>)[campo] ?? '');
}

/** Cadastro do prestador a partir do grupo emit das notas EMITIDAS: preenche só campos vazios; divergências ficam
 *  visíveis para decisão (nunca são sobrescritas). */
export const ProviderFromDocuments: React.FC<{ profile: FiscalProfileOut | null; onApplied: () => void }> = ({ profile, onApplied }) => {
  const { currentCompany, clients, showToast } = useFiscal();
  const canEdit = !!currentCompany?.permissions?.includes('admin');
  const [data, setData] = useState<ProviderDataFromDocuments | null>(null);
  const [result, setResult] = useState<ProviderDataApplyResult | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!currentCompany) return;
    try {
      setData(await clients.fiscal.getProviderDataFromDocuments(currentCompany.id));
    } catch (err) {
      logSafe('dados do prestador', err);
    }
  }, [currentCompany, clients.fiscal]);

  useEffect(() => { load(); }, [load]);

  const aplicar = async () => {
    if (!currentCompany) return;
    const id = currentCompany.id;
    setBusy(true);
    try {
      const r = await clients.fiscal.applyProviderDataFromDocuments(id);
      if (currentCompany?.id !== id) return;
      setResult(r);
      showToast(`${Object.keys(r.applied).length} campo(s) vazio(s) preenchido(s); ${r.divergent.length} divergência(s) mantida(s) sem alteração.`, 'success');
      onApplied();
    } catch (err) {
      logSafe('aplicar dados do prestador', err);
      showToast('Não foi possível completar o cadastro a partir das notas.', 'error');
    } finally {
      setBusy(false);
    }
  };

  if (!data || data.total_notas === 0) return null;
  const linhas = Object.entries(data.campos).filter(([, v]) => v.valores.length > 0);
  const vazios = linhas.filter(([k, v]) => v.consistente && !atual(profile, k)).length;
  return (
    <section className="pf-subpanel" aria-labelledby="prest-docs-title">
      <div className="pf-actions-row">
        <div>
          <h3 id="prest-docs-title" className="pf-subtitle">Dados do prestador nas notas emitidas</h3>
          <p className="pf-help">Grupo emit dos XMLs oficiais ({data.total_notas} nota(s)). Só valores iguais em todas as notas
            podem preencher campos vazios; um valor diferente do cadastro nunca é sobrescrito.</p>
        </div>
        {canEdit && (
          <Button variant="outline" size="sm" onClick={aplicar} isLoading={busy} disabled={vazios === 0}>
            Preencher campos vazios ({vazios})
          </Button>
        )}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="ui-table pf-prest-tabela" style={{ width: '100%', fontSize: '0.8125rem' }}>
          <thead><tr><th scope="col">Campo</th><th scope="col">Cadastro</th><th scope="col">Notas emitidas</th><th scope="col">Situação</th></tr></thead>
          <tbody>
            {linhas.map(([k, v]) => {
              const cad = atual(profile, k);
              const nota = v.valores[0]?.valor ?? '';
              const igual = cad === nota || (cad.replace(/\D/g, '') !== '' && cad.replace(/\D/g, '') === nota.replace(/\D/g, ''));
              const situacao = !v.consistente ? { s: 'DIVERGENT', l: `Varia entre as notas (${v.valores.length} valores)` }
                : !cad ? { s: 'PENDING', l: 'Cadastro vazio: pode ser preenchido' }
                : igual ? { s: 'VALID', l: 'Igual' }
                : { s: 'DIVERGENT', l: 'Diferente do cadastro: não será alterado' };
              return (
                <tr key={k}>
                  <td>{ROTULO[k] || k} <span className="pf-help">({v.tag})</span></td>
                  <td>{cad || <em>vazio</em>}</td>
                  <td>{v.valores.map((x) => `${x.valor} (${x.notas})`).join(' · ')}</td>
                  <td><Badge status={situacao.s} customLabel={situacao.l} size="sm" /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {result && result.divergent.length > 0 && (
        <Notice variant="warning" title="Divergências mantidas">
          {result.divergent.map((d) => `${ROTULO[d.field] || d.field}: cadastro “${d.cadastro}”, notas “${d.notas}”`).join(' · ')}.
          Altere manualmente no formulário acima, se for o caso.
        </Notice>
      )}
    </section>
  );
};
