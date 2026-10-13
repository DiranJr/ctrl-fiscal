import React, { useState, useEffect, useCallback } from 'react';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import {
  Table,
  Button,
  Modal,
  Input,
  Select,
  Badge,
  Column,
  ErrorState,
  Icon,
  PageHeader,
} from '@ctrl-fiscal/ui';
import { Customer } from '@ctrl-fiscal/api-client';
import { TableEmpty } from '../components/screen/ScreenSection.js';

export const CustomersPage: React.FC = () => {
  const { currentCompany, clients, showToast } = useFiscal();

  const [customers, setCustomers] = useState<Customer[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Modal State
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingCustomer, setEditingCustomer] = useState<Customer | null>(null);

  // Form Fields
  const [name, setName] = useState('');
  const [documentType, setDocumentType] = useState<'CNPJ' | 'CPF'>('CNPJ');
  const [documentNumber, setDocumentNumber] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [municipalRegistration, setMunicipalRegistration] = useState('');
  const [municipalityCode, setMunicipalityCode] = useState('');
  const [postalCode, setPostalCode] = useState('');
  const [street, setStreet] = useState('');
  const [number, setNumber] = useState('');
  const [complement, setComplement] = useState('');
  const [district, setDistrict] = useState('');
  const [city, setCity] = useState('');
  const [state, setState] = useState('');
  const [isActive, setIsActive] = useState(true);

  const [isSaving, setIsSaving] = useState(false);

  const loadCustomers = useCallback(async () => {
    if (!currentCompany) return;
    setIsLoading(true);
    setLoadError(null);
    try {
      const res = await clients.core.getCustomers(currentCompany.id);
      setCustomers(res.items);
    } catch (err: any) {
      logSafe('clientes', err);
      setLoadError(err.message || 'Falha ao listar clientes.');
    } finally {
      setIsLoading(false);
    }
  }, [currentCompany, clients.core]);

  useEffect(() => {
    loadCustomers();
  }, [loadCustomers]);

  const resetForm = () => {
    setEditingCustomer(null);
    setName('');
    setDocumentType('CNPJ');
    setDocumentNumber('');
    setEmail('');
    setPhone('');
    setMunicipalRegistration('');
    setMunicipalityCode('');
    setPostalCode('');
    setStreet('');
    setNumber('');
    setComplement('');
    setDistrict('');
    setCity('');
    setState('');
    setIsActive(true);
  };

  const handleOpenCreate = () => {
    resetForm();
    setIsModalOpen(true);
  };

  // Link de uma pendência de rascunho (?customer_id=<id>, aberto em nova aba): abre direto a edição desse cliente.
  useEffect(() => {
    const url = new URL(window.location.href);
    const id = url.searchParams.get('customer_id');
    if (!id || customers.length === 0) return;
    const alvo = customers.find((c) => c.id === id);
    url.searchParams.delete('customer_id');
    window.history.replaceState({}, '', url.toString());
    if (alvo) handleOpenEdit(alvo);
  }, [customers]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleOpenEdit = (c: Customer) => {
    setEditingCustomer(c);
    setName(c.name);
    setDocumentType(c.document_type || 'CNPJ');
    setDocumentNumber(c.document);
    setEmail(c.email || '');
    setPhone(c.phone || '');
    setMunicipalRegistration(c.municipal_registration || '');
    setMunicipalityCode(c.municipality_code || '');
    setPostalCode(c.postal_code || '');
    setStreet(c.address?.street || '');
    setNumber(c.address?.number || '');
    setComplement(c.address?.complement || '');
    setDistrict(c.address?.district || '');
    setCity(c.address?.city || '');
    setState(c.address?.state || '');
    setIsActive(c.is_active ?? true);
    setIsModalOpen(true);
  };

  const handleSaveCustomer = async () => {
    if (!currentCompany) return;
    if (!name.trim()) {
      showToast('O nome/razão social é obrigatório.', 'error');
      return;
    }

    const cleanDoc = documentNumber.replace(/\D/g, '');
    if (!editingCustomer && !cleanDoc) {
      showToast('O documento (CNPJ/CPF) é obrigatório.', 'error');
      return;
    }

    const cleanPhone = phone ? phone.replace(/\D/g, '') : null;
    if (cleanPhone && (cleanPhone.length < 6 || cleanPhone.length > 20)) {
      showToast('Telefone inválido. Deve conter de 6 a 20 dígitos numéricos (com DDD).', 'error');
      return;
    }

    setIsSaving(true);
    try {
      const cleanAddress = (street || number || district || city || state)
        ? {
            street: street.trim() || null,
            number: number.trim() || null,
            complement: complement.trim() || null,
            district: district.trim() || null,
            city: city.trim() || null,
            state: state.trim().toUpperCase() || null,
          }
        : null;

      if (editingCustomer) {
        // Atualização via PATCH /customers/{id} com expected_version
        await clients.core.updateCustomer(currentCompany.id, editingCustomer.id, {
          name: name.trim(),
          email: email.trim() || null,
          phone: cleanPhone,
          municipal_registration: municipalRegistration.trim() || null,
          municipality_code: municipalityCode.trim() || null,
          postal_code: postalCode.replace(/\D/g, '') || null,
          address: cleanAddress,
          is_active: isActive,
          expected_version: editingCustomer.version,
        });
        showToast('Cliente atualizado com sucesso!', 'success');
      } else {
        // Criação estrita via POST /customers
        await clients.core.createCustomer(currentCompany.id, {
          name: name.trim(),
          document_type: documentType,
          document: cleanDoc,
          email: email.trim() || null,
          phone: cleanPhone,
          municipal_registration: municipalRegistration.trim() || null,
          municipality_code: municipalityCode.trim() || null,
          postal_code: postalCode.replace(/\D/g, '') || null,
          address: cleanAddress,
        });
        showToast('Cliente cadastrado com sucesso!', 'success');
      }

      setIsModalOpen(false);
      resetForm();
      await loadCustomers();
    } catch (err: any) {
      // Mantém o modal aberto e os valores digitados para nova tentativa.
      logSafe('salvar cliente', err);
      showToast(err.message || 'Erro ao salvar cliente.', 'error');
    } finally {
      setIsSaving(false);
    }
  };

  const columns: Column<Customer>[] = [
    {
      key: 'name',
      header: 'Razão social / nome',
      render: (c) => (
        <div style={{ minWidth: '10rem' }}>
          <div style={{ fontWeight: 600 }}>{c.name}</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0 0.75rem', fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>
            {c.email && <span style={{ overflowWrap: 'anywhere' }}>{c.email}</span>}
            {c.phone && <span>Tel: {c.phone}</span>}
          </div>
        </div>
      ),
    },
    {
      key: 'document',
      header: 'Documento',
      render: (c) => (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '0.25rem' }}>
          <Badge status="INFO" customLabel={c.document_type || 'CNPJ'} icon="" />
          <span className="ui-mono" style={{ whiteSpace: 'nowrap' }}>{c.document}</span>
        </div>
      ),
    },
    {
      key: 'municipal_registration',
      header: 'Inscrição municipal',
      hideOnMobile: true,
      render: (c) => <span>{c.municipal_registration || '—'}</span>,
    },
    {
      key: 'address',
      header: 'Município / bairro',
      hideOnMobile: true,
      render: (c) => (
        <span>
          {c.address?.city ? `${c.address.city}/${c.address.state || ''}` : '—'}
          {c.address?.district ? ` (${c.address.district})` : ''}
        </span>
      ),
    },
    {
      key: 'is_active',
      header: 'Situação',
      render: (c) => (
        <Badge
          status={c.is_active ? 'VALID' : 'REJECTED'}
          customLabel={c.is_active ? `Ativo (v${c.version})` : `Inativo (v${c.version})`}
        />
      ),
    },
    {
      key: 'actions',
      header: <span className="ui-sr-only">Ações</span>,
      align: 'right',
      render: (c) => (
        <Button variant="ghost" size="sm" aria-label={`Editar cliente ${c.name}`} onClick={() => handleOpenEdit(c)}>
          <Icon name="draft" size={16} /> Editar
        </Button>
      ),
    },
  ];

  return (
    <div className="pf-stack">
      <PageHeader
        title="Cadastro de Clientes (Tomadores)"
        description={<>Base de clientes autorizados para faturamento da empresa <strong>{currentCompany?.name}</strong></>}
        primaryAction={
          <Button variant="primary" onClick={handleOpenCreate} aria-haspopup="dialog">
            <Icon name="plus" size={16} /> Novo Cliente
          </Button>
        }
      />

      {loadError ? (
        <ErrorState message={loadError} onRetry={loadCustomers} />
      ) : (
        <Table
          caption={`Clientes cadastrados: ${customers.length}`}
          columns={columns}
          data={customers}
          keyExtractor={(c) => c.id}
          isLoading={isLoading}
          emptyState={
            <TableEmpty
              title="Nenhum cliente cadastrado nesta empresa."
              description="Cadastre o primeiro tomador para usá-lo na emissão de NFS-e."
              action={
                <Button variant="outline" size="sm" onClick={handleOpenCreate} aria-haspopup="dialog">
                  <Icon name="plus" size={16} /> Cadastrar cliente
                </Button>
              }
            />
          }
        />
      )}

      {/* Modal Cadastro/Edição de Cliente — em caso de erro de rede o modal permanece aberto com os valores digitados */}
      <Modal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        title={editingCustomer ? `Editar Cliente: ${editingCustomer.name}` : 'Novo Tomador de Serviço'}
        description="Dados cadastrais e endereço em conformidade com o padrão NFS-e canônico"
        maxWidth="720px"
        footer={
          <>
            <Button variant="outline" onClick={() => setIsModalOpen(false)}>
              Cancelar
            </Button>
            <Button variant="primary" isLoading={isSaving} onClick={handleSaveCustomer}>
              {editingCustomer ? 'Salvar Alterações' : 'Cadastrar Cliente'}
            </Button>
          </>
        }
      >
        <div className="pf-stack">
          <p className="pf-help">Campos marcados com * são obrigatórios.</p>
          <Input
            label="Razão social / nome completo"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Ex: Consultoria Exemplo LTDA"
            autoComplete="organization"
          />

          <div className="pf-form-grid">
            <Select
              label="Tipo de documento"
              required
              disabled={!!editingCustomer}
              options={[
                { value: 'CNPJ', label: 'CNPJ (Pessoa Jurídica)' },
                { value: 'CPF', label: 'CPF (Pessoa Física)' },
              ]}
              value={documentType}
              onChange={(e) => setDocumentType(e.target.value as 'CNPJ' | 'CPF')}
            />

            <Input
              label="Número do documento"
              required={!editingCustomer}
              disabled={!!editingCustomer}
              inputMode="numeric"
              value={documentNumber}
              onChange={(e) => setDocumentNumber(e.target.value)}
              placeholder={documentType === 'CNPJ' ? '12.345.678/0001-90' : '123.456.789-00'}
              helperText={editingCustomer ? 'O documento fiscal não pode ser alterado após criação.' : undefined}
            />

            <Input
              label="Inscrição municipal"
              value={municipalRegistration}
              onChange={(e) => setMunicipalRegistration(e.target.value)}
              placeholder="Opcional"
            />

            <Input
              label="Código do município (IBGE, 7 dígitos)"
              inputMode="numeric"
              value={municipalityCode}
              onChange={(e) => setMunicipalityCode(e.target.value)}
              placeholder="Ex: 3550308"
            />

            <Input
              label="E-mail de contato / faturamento"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="financeiro@empresa.com.br"
            />

            <Input
              label="Telefone / celular (com DDD)"
              type="tel"
              autoComplete="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="11987654321"
              helperText="6 a 20 dígitos (vai para toma/fone na DPS)"
            />
          </div>

          <section aria-labelledby="cliente-endereco" style={{ borderTop: '1px solid var(--color-border)', paddingTop: '0.75rem' }}>
            <h3 id="cliente-endereco" className="pf-subtitle" style={{ marginBottom: '0.75rem' }}>Endereço do Tomador</h3>
            <div className="pf-form-grid">
              <Input
                label="Logradouro / rua"
                value={street}
                onChange={(e) => setStreet(e.target.value)}
                placeholder="Av. Paulista"
              />

              <Input
                label="Número"
                value={number}
                onChange={(e) => setNumber(e.target.value)}
                placeholder="1000"
              />

              <Input
                label="Complemento"
                value={complement}
                onChange={(e) => setComplement(e.target.value)}
                placeholder="Sala 101"
              />

              <Input
                label="Bairro"
                value={district}
                onChange={(e) => setDistrict(e.target.value)}
                placeholder="Bela Vista"
              />

              <Input
                label="Cidade"
                value={city}
                onChange={(e) => setCity(e.target.value)}
                placeholder="São Paulo"
              />

              <Input
                label="UF"
                maxLength={2}
                value={state}
                onChange={(e) => setState(e.target.value.toUpperCase())}
                placeholder="SP"
              />

              <Input
                label="CEP"
                inputMode="numeric"
                autoComplete="postal-code"
                value={postalCode}
                onChange={(e) => setPostalCode(e.target.value)}
                placeholder="01310-100"
              />
            </div>
          </section>
        </div>
      </Modal>
    </div>
  );
};
