import React, { useState, useEffect, useCallback } from 'react';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { Badge, Alert, Button, Input, Select, Icon, PageHeader, Skeleton } from '@ctrl-fiscal/ui';
import {
  IntegrationStatus,
  FiscalProfileOut,
  FiscalProfilePatch,
  CertificateInfo,
  getEnvironmentBadgeConfig,
  ProblemDetailsException,
} from '@ctrl-fiscal/api-client';
import { ScreenSection, InfoTile } from '../components/screen/ScreenSection.js';
import { Notice } from '../components/screen/Notice.js';
import { WorkspaceReadiness } from '../components/workspace/WorkspaceReadiness.js';
import { ServiceProfilesSection } from '../components/workspace/ServiceProfilesSection.js';
import { CertificateHistory } from '../components/workspace/CertificateHistory.js';
import { ProviderFromDocuments } from '../components/workspace/ProviderFromDocuments.js';

/** Rótulos das permissões separadas por operação (o servidor confere cada uma; a tela só exibe). */
const PERMISSION_LABEL: Record<string, string> = {
  view: 'Consultar', import: 'Importar XML', sync: 'Sincronizar com o ADN', review: 'Conferir',
  prepare: 'Preparar rascunhos', schedule: 'Agendar lotes', emit: 'Emitir (transmitir)', cancel: 'Cancelar NFS-e',
  certificates: 'Certificado digital', admin: 'Administrar usuários',
};

export const SettingsPage: React.FC = () => {
  const { currentCompany, user, clients, showToast } = useFiscal();
  const [integrationStatus, setIntegrationStatus] = useState<IntegrationStatus | null>(null);
  const [fiscalProfile, setFiscalProfile] = useState<FiscalProfileOut | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Form Perfil Fiscal
  const [municipalityCode, setMunicipalityCode] = useState('');
  const [municipalRegistration, setMunicipalRegistration] = useState('');
  const [simplesOption, setSimplesOption] = useState<string>('');
  const [simplesRegime, setSimplesRegime] = useState<string>('');
  const [specialRegime, setSpecialRegime] = useState<string>('');
  const [profilePhone, setProfilePhone] = useState('');
  const [profileEmail, setProfileEmail] = useState('');
  const [dpsSeries, setDpsSeries] = useState('');
  const [postalCode, setPostalCode] = useState('');
  const [addrStreet, setAddrStreet] = useState('');
  const [addrNumber, setAddrNumber] = useState('');
  const [addrComplement, setAddrComplement] = useState('');
  const [addrDistrict, setAddrDistrict] = useState('');
  const [profileErrors, setProfileErrors] = useState<Record<string, string>>({});
  const [certReload, setCertReload] = useState(0);
  const [isSavingProfile, setIsSavingProfile] = useState(false);

  // Form Certificado A1
  const [certFile, setCertFile] = useState<File | null>(null);
  const [certPassword, setCertPassword] = useState('');
  const [certEnvironments, setCertEnvironments] = useState<string[]>(['restricted', 'production']);
  const [deleteEnvironment, setDeleteEnvironment] = useState<'restricted' | 'production'>('restricted');
  const [isUploadingCert, setIsUploadingCert] = useState(false);
  const [isDeletingCert, setIsDeletingCert] = useState(false);
  const [lastUploadedCert, setLastUploadedCert] = useState<CertificateInfo | null>(null);
  const [certOperationSuccess, setCertOperationSuccess] = useState<string | null>(null);
  const [certOperationError, setCertOperationError] = useState<string | null>(null);

  const envBadge = getEnvironmentBadgeConfig(
    integrationStatus?.environment || currentCompany?.environment,
    integrationStatus?.simulated ?? true
  );

  const loadData = useCallback(async () => {
    if (!currentCompany) return;
    setIsLoading(true);
    setLoadError(null);
    try {
      const [statusRes, profileRes] = await Promise.all([
        clients.core.getIntegrationStatus(currentCompany.id),
        clients.core.getFiscalProfile(currentCompany.id).catch(() => null),
      ]);
      setIntegrationStatus(statusRes);
      if (profileRes) {
        setFiscalProfile(profileRes);
        setMunicipalityCode(profileRes.municipality_code || '');
        setMunicipalRegistration(profileRes.municipal_registration || '');
        setSimplesOption(profileRes.simples_option || '');
        setSimplesRegime(profileRes.simples_regime || '');
        setSpecialRegime(profileRes.special_regime || '');
        setProfilePhone(profileRes.phone || '');
        setProfileEmail(profileRes.email || '');
        setDpsSeries(profileRes.dps_series || '');
        setPostalCode(profileRes.postal_code || '');
        setAddrStreet(profileRes.address?.street || '');
        setAddrNumber(profileRes.address?.number || '');
        setAddrComplement(profileRes.address?.complement || '');
        setAddrDistrict(profileRes.address?.district || '');
      }
    } catch (err: any) {
      // Erro de leitura exibido na própria tela, com nova tentativa; valores já digitados nos formulários são mantidos.
      logSafe('configurações', err);
      setLoadError(err.message || 'Falha ao carregar configurações da empresa.');
    } finally {
      setIsLoading(false);
    }
  }, [currentCompany, clients.core]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Salvar Perfil Fiscal (PATCH /fiscal-profile)
  const handleSaveFiscalProfile = async () => {
    if (!currentCompany) return;

    if (simplesRegime && simplesOption !== '3') {
      showToast('Regime de Apuração do Simples Nacional exige Opção pelo Simples igual a 3 (MEI).', 'error');
      return;
    }

    const local: Record<string, string> = {};
    if (dpsSeries && (!/^\d{1,5}$/.test(dpsSeries) || Number(dpsSeries) < 1 || Number(dpsSeries) > 49999)) {
      local.dps_series = 'Série do aplicativo próprio: 1 a 49999.';
    }
    if (postalCode && !/^\d{8}$/.test(postalCode.replace(/\D/g, ''))) local.postal_code = 'CEP: 8 dígitos.';
    setProfileErrors(local);
    if (Object.keys(local).length) return;
    const companyAtStart = currentCompany.id;
    setIsSavingProfile(true);
    try {
      const anyAddr = addrStreet || addrNumber || addrComplement || addrDistrict;
      const patch: FiscalProfilePatch = {
        dps_series: dpsSeries ? String(Number(dpsSeries)) : undefined,
        postal_code: postalCode ? postalCode.replace(/\D/g, '') : null,
        address: anyAddr ? { street: addrStreet.trim() || null, number: addrNumber.trim() || null,
          complement: addrComplement.trim() || null, district: addrDistrict.trim() || null } : null,
        municipality_code: municipalityCode.trim() || null,
        municipal_registration: municipalRegistration.trim() || null,
        simples_option: (simplesOption as any) || null,
        simples_regime: simplesOption === '3' ? ((simplesRegime as any) || null) : null,
        special_regime: (specialRegime as any) || null,
        phone: profilePhone ? profilePhone.replace(/\D/g, '') : null,
        email: profileEmail.trim() || null,
      };

      const updated = await clients.core.updateFiscalProfile(companyAtStart, patch);
      if (currentCompany?.id !== companyAtStart) return; // workspace trocado durante o envio: resposta ignorada
      setFiscalProfile(updated);
      showToast('Perfil fiscal atualizado com sucesso!', 'success');
      await loadData();
    } catch (err: any) {
      logSafe('perfil fiscal', err);
      if (err instanceof ProblemDetailsException) {
        showToast(err.message || 'Erro de validação ao salvar perfil fiscal.', 'error');
      } else {
        showToast(err.message || 'Erro ao atualizar perfil fiscal.', 'error');
      }
    } finally {
      setIsSavingProfile(false);
    }
  };

  // Upload Certificado A1 (POST /certificate)
  const handleUploadCertificate = async () => {
    if (!currentCompany) return;
    if (!certFile) {
      showToast('Selecione o arquivo do certificado (.pfx ou .p12).', 'error');
      return;
    }
    if (!certPassword) {
      showToast('Informe a senha do certificado A1.', 'error');
      return;
    }

    setIsUploadingCert(true);
    const passwordToSend = certPassword;
    // Limpeza imediata da senha por segurança (não persistir em estado nem log)
    setCertPassword('');

    try {
      const certInfo = await clients.core.uploadCertificate(
        currentCompany.id,
        certFile,
        passwordToSend,
        certEnvironments
      );
      setLastUploadedCert(certInfo);
      setCertReload((n) => n + 1);
      setCertFile(null);
      showToast('Certificado A1 validado e instalado com sucesso no cofre seguro!', 'success');
      await loadData();
    } catch (err: any) {
      logSafe('certificado', err);
      showToast(err.message || 'Erro ao processar certificado A1.', 'error');
    } finally {
      setIsUploadingCert(false);
    }
  };

  // Desativar Certificado A1 (DELETE /certificate?environment=restricted|production)
  const handleDeleteCertificate = async (envParam?: 'restricted' | 'production') => {
    if (!currentCompany) return;
    const targetEnv = envParam || deleteEnvironment || (currentCompany.environment === 'production' ? 'production' : 'restricted');
    setIsDeletingCert(true);
    setCertOperationError(null);
    setCertOperationSuccess(null);
    try {
      await clients.core.deleteCertificate(currentCompany.id, targetEnv);
      const msg = `Certificado desativado com sucesso para o ambiente ${targetEnv === 'production' ? 'Produção' : 'Produção Restrita'}.`;
      setCertOperationSuccess(msg);
      setCertReload((n) => n + 1);
      showToast(msg, 'info');
      setLastUploadedCert(null);
      await loadData();
    } catch (err: any) {
      logSafe('desativar certificado', err);
      const errMsg = err?.message || 'Erro ao desativar certificado.';
      setCertOperationError(errMsg);
      showToast(errMsg, 'error');
    } finally {
      setIsDeletingCert(false);
    }
  };

  const firstLoad = isLoading && !integrationStatus;
  const cert = integrationStatus?.certificate;

  return (
    <div className="pf-stack">
      <PageHeader
        title="Configurações e Parâmetros Fiscais"
        description={
          <>
            Parâmetros tributários, perfil fiscal e gestão segura do certificado A1 da empresa <strong>{currentCompany?.name}</strong>
          </>
        }
      />

      {loadError && (
        <Alert
          variant="error"
          title="Não foi possível carregar as configurações"
          action={
            <Button variant="outline" size="sm" isLoading={isLoading} onClick={() => loadData()}>
              Tentar novamente
            </Button>
          }
        >
          {loadError} Os valores digitados nos formulários foram mantidos.
        </Alert>
      )}

      <WorkspaceReadiness />

      {/* Identificação da empresa */}
      <ScreenSection title="Identificação cadastral e fuso horário">
        <div className="pf-grid-tiles">
          <InfoTile label="Razão social">{currentCompany?.name}</InfoTile>
          <InfoTile label="CNPJ da empresa">
            <span className="ui-mono">{currentCompany?.cnpj}</span>
          </InfoTile>
          <InfoTile label="Fuso horário oficial (IANA)">{currentCompany?.timezone || 'America/Sao_Paulo'}</InfoTile>
          <InfoTile label="Ambiente de execução">
            {firstLoad ? <Skeleton width="8rem" height="1.25rem" /> : <Badge status={envBadge.status} customLabel={envBadge.label} />}
          </InfoTile>
        </div>
      </ScreenSection>

      {/* Perfil fiscal da empresa (Sprint 02) */}
      <ScreenSection
        title="Perfil Fiscal do Emitente"
        description="Dados fiscais obrigatórios exigidos na geração de DPS para autorização pelo Ambiente Nacional"
      >
        {firstLoad ? (
          <div className="pf-form-grid" aria-busy="true">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} height="3.5rem" borderRadius="var(--radius-md)" />
            ))}
          </div>
        ) : (
          <>
            {/* Status do perfil */}
            <div className="pf-tile pf-actions-row" data-variant="subtle">
              <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                <span style={{ fontSize: '0.875rem', fontWeight: 600 }}>Prontidão para emissão oficial:</span>
                <Badge
                  status={fiscalProfile?.complete_for_emission ? 'VALID' : 'DIVERGENT'}
                  customLabel={fiscalProfile?.complete_for_emission ? 'Perfil Completo' : 'Perfil Incompleto'}
                />
              </div>
              {fiscalProfile?.updated_at && (
                <span className="pf-help">Última atualização: {new Date(fiscalProfile.updated_at).toLocaleString('pt-BR')}</span>
              )}
            </div>

            {/* Avisos de campos faltantes */}
            {fiscalProfile && !fiscalProfile.complete_for_emission && fiscalProfile.missing.length > 0 && (
              <Notice variant="warning" title="Campos fiscais obrigatórios pendentes">
                <div>Para habilitar emissões no Ambiente Oficial, preencha os seguintes campos do emitente:</div>
                <ul className="pf-bullets" style={{ marginTop: '0.25rem' }}>
                  {fiscalProfile.missing.map((m) => (
                    <li key={m}>
                      <strong>{m}</strong>
                    </li>
                  ))}
                </ul>
              </Notice>
            )}

            <p className="pf-help">Campos marcados com * são obrigatórios para emissão.</p>

            {/* Formulário de edição do perfil */}
            <div className="pf-form-grid">
              <Input
                label="Município de emissão (IBGE, 7 dígitos – cLocEmi)"
                required
                inputMode="numeric"
                value={municipalityCode}
                onChange={(e) => setMunicipalityCode(e.target.value)}
                placeholder="Ex: 3550308"
                helperText="Código IBGE do município da empresa prestadora."
              />

              <Input
                label="Inscrição municipal (prest/IM)"
                value={municipalRegistration}
                onChange={(e) => setMunicipalRegistration(e.target.value)}
                placeholder="Ex: 12345678"
                helperText="Inscrição Municipal junto à Prefeitura."
              />

              <Select
                label="Opção pelo Simples Nacional (opSimpNac)"
                required
                value={simplesOption}
                onChange={(e) => {
                  setSimplesOption(e.target.value);
                  if (e.target.value !== '3') setSimplesRegime('');
                }}
                options={[
                  { value: '', label: 'Selecione...' },
                  { value: '1', label: '1 - Não optante pelo Simples Nacional' },
                  { value: '2', label: '2 - Optante - Microempresa ou EPP' },
                  { value: '3', label: '3 - Optante - Microempreendedor Individual (MEI)' },
                ]}
              />

              <Select
                label="Regime de apuração Simples (regApTribSN)"
                value={simplesRegime}
                disabled={simplesOption !== '3'}
                onChange={(e) => setSimplesRegime(e.target.value)}
                options={[
                  { value: '', label: simplesOption === '3' ? 'Selecione...' : 'Não aplicável (apenas MEI)' },
                  { value: '1', label: '1 - Regime MEI Municipal' },
                  { value: '2', label: '2 - Estimativa' },
                  { value: '3', label: '3 - Sociedade de Profissionais' },
                ]}
                helperText={simplesOption === '3' ? 'Exigido quando opSimpNac = 3 (MEI)' : 'Habilitado apenas para MEI'}
              />

              <Select
                label="Regime especial de tributação (regEspTrib)"
                required
                value={specialRegime}
                onChange={(e) => setSpecialRegime(e.target.value)}
                options={[
                  { value: '', label: 'Selecione...' },
                  { value: '0', label: '0 - Nenhum regime especial' },
                  { value: '1', label: '1 - Ato Cooperado' },
                  { value: '2', label: '2 - Estimativa' },
                  { value: '3', label: '3 - Sociedade de Profissionais' },
                  { value: '4', label: '4 - Cooperativa' },
                  { value: '5', label: '5 - Microempresário Individual' },
                  { value: '6', label: '6 - Microempresa Municipal' },
                  { value: '9', label: '9 - Outros regimes' },
                ]}
              />

              <Input
                label="Telefone oficial do prestador (prest/fone)"
                type="tel"
                autoComplete="tel"
                value={profilePhone}
                onChange={(e) => setProfilePhone(e.target.value)}
                placeholder="11987654321"
                helperText="6 a 20 dígitos (com DDD)."
              />

              <Input
                label="E-mail oficial do prestador (prest/email)"
                type="email"
                autoComplete="email"
                value={profileEmail}
                onChange={(e) => setProfileEmail(e.target.value)}
                placeholder="contato@empresa.com.br"
              />

              <Input
                label="Série da DPS deste workspace"
                inputMode="numeric"
                maxLength={5}
                value={dpsSeries}
                onChange={(e) => setDpsSeries(e.target.value.replace(/\D/g, ''))}
                error={profileErrors.dps_series}
                helperText="Aplicativo próprio: 1 a 49999. Alterar a série não reinicia a numeração já usada."
              />
            </div>

            <fieldset className="pf-fieldset pf-subpanel">
              <legend>Endereço do prestador (cadastro do workspace)</legend>
              <p className="pf-help">Opcional. Não é enviado na DPS quando o emitente é o próprio prestador (regra E0128).</p>
              <div className="pf-form-grid">
                <Input label="CEP" inputMode="numeric" maxLength={9} value={postalCode} autoComplete="postal-code"
                  onChange={(e) => setPostalCode(e.target.value)} error={profileErrors.postal_code} helperText="8 dígitos." />
                <Input label="Logradouro" value={addrStreet} maxLength={200} autoComplete="address-line1"
                  onChange={(e) => setAddrStreet(e.target.value)} />
                <Input label="Número" value={addrNumber} maxLength={20} onChange={(e) => setAddrNumber(e.target.value)} />
                <Input label="Complemento" value={addrComplement} maxLength={100} autoComplete="address-line2"
                  onChange={(e) => setAddrComplement(e.target.value)} />
                <Input label="Bairro" value={addrDistrict} maxLength={100} onChange={(e) => setAddrDistrict(e.target.value)} />
              </div>
            </fieldset>

            <ProviderFromDocuments profile={fiscalProfile} onApplied={() => loadData()} />

            <div className="pf-actions-end">
              <Button variant="primary" onClick={handleSaveFiscalProfile} isLoading={isSavingProfile}>
                Salvar Parâmetros Fiscais
              </Button>
            </div>
          </>
        )}
      </ScreenSection>

      {/* Certificado digital A1 e conectores */}
      <ScreenSection
        title="Certificado Digital A1 (ICP-Brasil)"
        description="Gerenciamento de certificado digital A1 para autenticação mTLS e assinatura de DPS"
      >
        {/* Status do certificado instalado */}
        <div className="pf-grid-tiles" aria-busy={firstLoad || undefined}>
          <InfoTile label="Certificado atual">
            {firstLoad ? (
              <Skeleton width="9rem" height="1.25rem" />
            ) : cert?.present ? (
              <span style={{ display: 'inline-flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.375rem' }}>
                <Badge status="VALID" customLabel="Instalado" />
                <span style={{ fontWeight: 500 }}>{cert.days_to_expiry ?? '—'} dias até expirar</span>
              </span>
            ) : (
              <Badge status="NOT_CONFIGURED" customLabel="Não configurado" />
            )}
          </InfoTile>

          <InfoTile label="CNPJ do titular">
            {firstLoad ? (
              <Skeleton width="9rem" height="1.25rem" />
            ) : cert?.present ? (
              cert.subject_cnpj_matches ? (
                <Badge status="VALID" customLabel="Confere com a empresa" />
              ) : (
                <Badge status="DIVERGENT" customLabel="CNPJ divergente" />
              )
            ) : (
              '—'
            )}
          </InfoTile>

          <InfoTile label="Validade final">
            {firstLoad ? (
              <Skeleton width="7rem" height="1.25rem" />
            ) : cert?.not_after ? (
              `Até ${new Date(cert.not_after).toLocaleDateString('pt-BR')}`
            ) : (
              '—'
            )}
          </InfoTile>
        </div>

        {/* Resultado do upload recente */}
        {lastUploadedCert && (
          <Notice
            variant={lastUploadedCert.accepted ? 'success' : 'warning'}
            title={lastUploadedCert.accepted ? 'Certificado A1 aceito com sucesso' : 'Problemas no certificado'}
          >
            <div>
              <strong>Impressão digital (SHA-256):</strong>{' '}
              <span className="ui-mono" style={{ fontSize: '0.75rem', overflowWrap: 'anywhere' }}>{lastUploadedCert.fingerprint_sha256}</span>
            </div>
            <div>Validade: {lastUploadedCert.days_to_expiry} dias restantes.</div>
            {lastUploadedCert.problems && lastUploadedCert.problems.length > 0 && (
              <div style={{ marginTop: '0.5rem' }}>
                <strong>Avisos:</strong>
                {lastUploadedCert.problems.map((p, i) => (
                  <div key={i} style={{ fontSize: '0.75rem' }}>
                    {Object.entries(p).map(([k, v]) => `${k}: ${v}`).join(', ')}
                  </div>
                ))}
              </div>
            )}
          </Notice>
        )}

        {/* Formulário de upload do certificado A1 */}
        <section className="pf-subpanel" aria-labelledby="cert-upload-title">
          <h3 id="cert-upload-title" className="pf-subtitle">Anexar / Atualizar Certificado A1</h3>
          <p className="pf-muted">
            Envie o arquivo <strong>.pfx</strong> ou <strong>.p12</strong> (tamanho máximo de 64 KB). A senha é enviada de forma
            efêmera via canal seguro (TLS) e <strong>nunca é persistida na interface nem em logs</strong>.
          </p>

          <div className="pf-form-grid">
            <div>
              <label htmlFor="certFileInput" className="pf-field-label">
                Arquivo do certificado (.pfx / .p12)
                <span aria-hidden="true" style={{ color: 'var(--color-danger)' }}> *</span>
              </label>
              <input
                id="certFileInput"
                type="file"
                required
                accept=".pfx,.p12,application/x-pkcs12"
                onChange={(e) => setCertFile(e.target.files?.[0] || null)}
                className="pf-file-input"
              />
            </div>

            <Input
              label="Senha do certificado A1"
              required
              type="password"
              autoComplete="off"
              value={certPassword}
              onChange={(e) => setCertPassword(e.target.value)}
              placeholder="Digite a senha do arquivo PFX"
              helperText="A senha é limpa imediatamente após a validação."
            />
          </div>

          <fieldset className="pf-fieldset">
            <legend>Ambientes de aplicação</legend>
            <div className="pf-checks">
              <label>
                <input
                  type="checkbox"
                  checked={certEnvironments.includes('restricted')}
                  onChange={(e) => {
                    if (e.target.checked) setCertEnvironments((prev) => [...prev, 'restricted']);
                    else setCertEnvironments((prev) => prev.filter((x) => x !== 'restricted'));
                  }}
                />
                Produção Restrita (Homologação)
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={certEnvironments.includes('production')}
                  onChange={(e) => {
                    if (e.target.checked) setCertEnvironments((prev) => [...prev, 'production']);
                    else setCertEnvironments((prev) => prev.filter((x) => x !== 'production'));
                  }}
                />
                Produção (Leitura e Emissão)
              </label>
            </div>
          </fieldset>

          <div className="pf-actions-end">
            <Button variant="secondary" onClick={handleUploadCertificate} isLoading={isUploadingCert}>
              {!isUploadingCert && <Icon name="upload" size={16} />} Instalar Certificado A1
            </Button>
          </div>

          {cert?.present && (
            <div className="pf-actions-row" style={{ alignItems: 'flex-end', borderTop: '1px solid var(--color-border)', paddingTop: '1rem' }}>
              <div style={{ flex: '1 1 14rem', maxWidth: '22rem' }}>
                <Select
                  label="Ambiente do certificado a desativar"
                  value={deleteEnvironment}
                  onChange={(e) => setDeleteEnvironment(e.target.value as any)}
                  options={[
                    { value: 'restricted', label: 'Ambiente: Produção Restrita' },
                    { value: 'production', label: 'Ambiente: Produção' },
                  ]}
                />
              </div>
              <Button variant="danger" size="sm" onClick={() => handleDeleteCertificate()} isLoading={isDeletingCert}>
                Desativar Certificado
              </Button>
            </div>
          )}

          {/* Resultado das operações do certificado */}
          {certOperationSuccess && (
            <Alert variant="success" title="Operação concluída">
              {certOperationSuccess}
            </Alert>
          )}
          {certOperationError && (
            <Alert variant="error" title="Erro na operação do certificado">
              {certOperationError}
            </Alert>
          )}
        </section>

        {/* Bloqueadores reais */}
        {integrationStatus?.blockers && integrationStatus.blockers.length > 0 ? (
          <Notice variant="warning" title="Bloqueadores ativos no ambiente">
            {integrationStatus.blockers.map((b, i) => (
              <div key={i} style={{ marginTop: '0.25rem' }}>
                <strong>{b.code || 'BLOQUEIO'}:</strong> {b.message || 'Bloqueio não detalhado'}
              </div>
            ))}
          </Notice>
        ) : integrationStatus ? (
          <Notice variant="success">
            Nenhum bloqueio ativo no ambiente. Conexão apta para operações conforme capacidades declaradas.
          </Notice>
        ) : null}
        <CertificateHistory reloadKey={certReload} />
      </ScreenSection>

      <ServiceProfilesSection />

      {/* Permissões do usuário */}
      <ScreenSection title="Permissões concedidas ao usuário">
        <p className="pf-muted">
          Permissões ativas para <strong>{user?.name}</strong> nesta empresa:
        </p>
        {currentCompany?.permissions && currentCompany.permissions.length > 0 ? (
          <ul className="pf-chips" aria-label="Permissões">
            {currentCompany.permissions.map((p) => (
              <li key={p}>
                <Badge status="VALID" customLabel={PERMISSION_LABEL[p] || p} size="md" />
              </li>
            ))}
          </ul>
        ) : (
          <p className="pf-help">Nenhuma permissão registrada para esta empresa.</p>
        )}
      </ScreenSection>
    </div>
  );
};
