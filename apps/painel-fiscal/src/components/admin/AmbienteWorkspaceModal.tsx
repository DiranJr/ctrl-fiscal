import React, { useCallback, useEffect, useState } from 'react';
import { Button, Modal } from '@ctrl-fiscal/ui';
import { ProblemDetailsException } from '@ctrl-fiscal/api-client';
import type { EnvironmentImpact, Workspace, WorkspaceEnvironment, WorkspaceEnvironmentOption } from '@ctrl-fiscal/api-client';
import { useFiscal, logSafe } from '../../context/FiscalContext.js';
import { Notice } from '../screen/Notice.js';

/** Ambiente do workspace e leitura ADN (administrador do workspace). Opções = o que a instalação permite, com as
 * pendências globais separadas das do workspace; impacto por ambiente antes de confirmar; confirmação explícita
 * (empresa, CNPJ, origem e destino) conferida no servidor. Não habilita emissão nem cancelamento. */
const NOME: Record<string, string> = { production: 'Produção', restricted: 'Produção Restrita', mock: 'Simulação' };
const nome = (env?: string | null) => (env ? NOME[env] || env : '—');
const liga = (v?: boolean) => (v ? 'ligada' : 'desligada');

export const AmbienteWorkspaceModal: React.FC<{ workspace: Workspace | null; onClose: () => void; onChanged: () => void }> = ({
  workspace, onClose, onChanged,
}) => {
  const { clients, showToast } = useFiscal();
  const [dados, setDados] = useState<WorkspaceEnvironment | null>(null);
  const [impacto, setImpacto] = useState<EnvironmentImpact | null>(null);
  const [destino, setDestino] = useState<'production' | 'restricted' | ''>('');
  const [lerAdn, setLerAdn] = useState(true);
  const [confirmo, setConfirmo] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [erros, setErros] = useState<string[]>([]);
  const [carregando, setCarregando] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const id = workspace?.id;

  const carregar = useCallback(async () => {
    if (!id) return;
    setCarregando(true);
    setErro(null);
    try {
      const d = await clients.core.getWorkspaceEnvironment(id);
      setDados(d);
      const op = d.options.find((o) => o.environment !== d.environment && o.available)
        || d.options.find((o) => o.environment === d.environment);
      setDestino((op?.environment || d.environment) as 'production' | 'restricted');
      setLerAdn(!!op?.read_available); // leitura só começa marcada quando é possível neste ambiente
    } catch (err: any) {
      logSafe('ambiente do workspace', err);
      setErro(err?.message || 'Não foi possível carregar o ambiente do workspace.');
    } finally {
      setCarregando(false);
    }
  }, [id, clients.core]);

  useEffect(() => {
    setDados(null); setImpacto(null); setConfirmo(false); setErros([]);
    carregar();
  }, [carregar]);

  useEffect(() => {
    if (!id || !destino) return;
    setConfirmo(false);
    clients.fiscal.getEnvironmentImpact(id, destino).then(setImpacto).catch((err) => {
      logSafe('impacto da troca de ambiente', err);
      setImpacto(null);
    });
  }, [id, destino, clients.fiscal]);

  const opcao: WorkspaceEnvironmentOption | undefined = dados?.options.find((o) => o.environment === destino);
  const novoAdn = lerAdn && destino ? [destino] : [];
  const semMudanca = !!dados && destino === dados.environment
    && JSON.stringify(novoAdn) === JSON.stringify([...dados.adn_environments].sort());
  const podeConfirmar = !!dados && !!opcao && opcao.available && (!lerAdn || opcao.read_available) && !semMudanca
    && confirmo && !salvando;

  const confirmar = async () => {
    if (!dados || !destino || !podeConfirmar) return;
    setSalvando(true);
    setErro(null);
    setErros([]);
    try {
      const r = await clients.core.changeWorkspaceEnvironment(dados.company_id, {
        environment: destino, adn_read: lerAdn,
        confirmation: { company_id: dados.company_id, cnpj: dados.cnpj, from_environment: dados.environment, to_environment: destino },
      });
      showToast(`Ambiente de “${workspace?.name}”: ${nome(r.environment)}`
        + (r.adn_environments.length ? `, leitura ADN em ${r.adn_environments.map(nome).join(', ')}.` : ', sem leitura ADN.'), 'success');
      onChanged();
    } catch (err: any) {
      logSafe('trocar ambiente', err);
      setErro(err?.message || 'Não foi possível trocar o ambiente.');
      if (err instanceof ProblemDetailsException) {
        setErros(Object.values(err.formatFieldErrors()).flat());
      }
    } finally {
      setSalvando(false);
    }
  };

  return (
    <Modal isOpen={!!workspace} onClose={() => !salvando && onClose()} maxWidth="760px"
      title="Ambiente e leitura ADN"
      description={workspace ? `${workspace.name} · ${workspace.company.name}` : undefined}
      footer={(
        <div className="pf-actions-end">
          <Button variant="outline" onClick={onClose} disabled={salvando}>Cancelar</Button>
          <Button variant="primary" onClick={confirmar} isLoading={salvando} disabled={!podeConfirmar}>Confirmar troca</Button>
        </div>
      )}>
      {carregando && !dados && <p className="pf-help" role="status" aria-busy="true">Conferindo a instalação e o certificado…</p>}
      {erro && (
        <Notice variant="error" title="Nada foi alterado">
          {erro}
          {erros.length > 0 && <ul>{erros.map((m) => <li key={m}>{m}</li>)}</ul>}
        </Notice>
      )}
      {dados && (
        <div className="pf-stack" data-testid="ambiente-workspace">
          <p>
            Atual: <strong>{nome(dados.environment)}</strong> · Leitura ADN:{' '}
            <strong>{dados.adn_environments.length ? dados.adn_environments.map(nome).join(', ') : 'nenhuma'}</strong>
          </p>
          <fieldset className="pf-stack" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="pf-help">Ambiente do workspace (opções da instalação)</legend>
            {dados.options.map((o) => (
              <div key={o.environment} data-env={o.environment} style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '0.625rem' }}>
                <label className="pf-check">
                  <input type="radio" name="ambiente" value={o.environment} checked={destino === o.environment}
                    disabled={!o.available} onChange={() => { setDestino(o.environment); setLerAdn(o.read_available); }} />
                  <span><strong>{o.label}</strong> — {o.available ? 'disponível na instalação' : 'indisponível'}</span>
                </label>
                <div className="pf-help">
                  Na instalação: leitura ADN {liga(o.installation.distribution)}, emissão {liga(o.installation.emission)},
                  cancelamento {liga(o.installation.cancellation)}. Certificado:{' '}
                  {o.certificate.present ? (o.certificate.subject_cnpj_matches ? `válido até ${String(o.certificate.not_after || '').slice(0, 10)}` : 'CNPJ divergente') : 'ausente'}.
                </div>
                {o.global_issues.length > 0 && (
                  <div className="pf-help" data-kind="global"><strong>Pendência global (instalação):</strong>{' '}
                    {o.global_issues.map((i) => i.message).join(' ')}</div>
                )}
                {o.company_issues.length > 0 && (
                  <div className="pf-help" data-kind="empresa"><strong>Pendência deste workspace:</strong>{' '}
                    {o.company_issues.map((i) => i.message).join(' ')}</div>
                )}
              </div>
            ))}
          </fieldset>
          {opcao && (
            <label className="pf-check">
              <input type="checkbox" checked={lerAdn} disabled={!opcao.read_available} onChange={(e) => setLerAdn(e.target.checked)} />
              <span>Habilitar a leitura ADN (consulta das notas) em {opcao.label}
                {!opcao.read_available && ' — indisponível (veja as pendências acima)'}</span>
            </label>
          )}
          {impacto && (
            <section aria-label="Dados por ambiente">
              <table className="ui-table" style={{ width: '100%', fontSize: '0.8125rem' }}>
                <thead>
                  <tr><th>Ambiente</th><th>Documentos</th><th>Rascunhos</th><th>Agendados</th><th>Em andamento</th><th>Agenda ADN</th></tr>
                </thead>
                <tbody>
                  {impacto.environments.map((e) => (
                    <tr key={e.environment} data-env={e.environment}>
                      <td>{nome(e.environment)}{e.current ? ' (atual)' : ''}{e.target && !e.current ? ' (destino)' : ''}</td>
                      <td className="ui-num">{e.documents}</td>
                      <td className="ui-num">{e.drafts}</td>
                      <td className="ui-num">{e.scheduled_not_started}</td>
                      <td className="ui-num">{e.in_flight + e.active_cancellations + e.active_jobs}</td>
                      <td>{e.sync ? `${e.sync.enabled ? 'ativa' : 'suspensa'} · NSU ${e.sync.cursor_nsu}` : 'sem agenda'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <ul className="pf-help">{impacto.on_switch.map((t) => <li key={t}>{t}</li>)}</ul>
            </section>
          )}
          <Notice variant="info" title="Emissão e cancelamento não mudam">{dados.note}</Notice>
          {semMudanca ? (
            <p className="pf-help">Nada a alterar: o ambiente e a leitura ADN já são estes.</p>
          ) : opcao?.available && (
            <label className="pf-check">
              <input type="checkbox" checked={confirmo} onChange={(e) => setConfirmo(e.target.checked)} />
              <span>Confirmo trocar o ambiente de <strong>{nome(dados.environment)}</strong> para{' '}
                <strong>{opcao.label}</strong>{lerAdn ? ' com leitura ADN' : ' sem leitura ADN'} no CNPJ {dados.cnpj}.</span>
            </label>
          )}
        </div>
      )}
    </Modal>
  );
};
