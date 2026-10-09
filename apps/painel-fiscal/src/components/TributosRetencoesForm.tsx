import React from 'react';
import { Input, Select, Card } from '@ctrl-fiscal/ui';
import {
  ISS_RETENTION_OPTIONS,
  TP_RET_PIS_COFINS_OPTIONS,
  PIS_COFINS_CST_OPTIONS,
} from '@ctrl-fiscal/api-client';

export interface TributosRetencoesState {
  // ISS
  issqnTaxation: 'TAXABLE' | 'IMMUNE' | 'EXPORT' | 'NOT_INCIDENT';
  issRetention: 'NOT_RETAINED' | 'RETAINED_BY_TOMADOR' | 'RETAINED_BY_INTERMEDIARY';
  issRate: string;
  immunityType: '1' | '2' | '3' | '4' | '5';
  // Retenções Federais
  irrfWithheld: string;
  inssWithheld: string;
  csllWithheld: string;
  pisCofinsRetention: string;
  // PIS / COFINS
  pisCofinsCst: string;
  pisCofinsBase: string;
  pisRate: string;
  cofinsRate: string;
  pisAmount: string;
  cofinsAmount: string;
}

interface TributosRetencoesFormProps {
  values: TributosRetencoesState;
  onChange: <K extends keyof TributosRetencoesState>(field: K, value: TributosRetencoesState[K]) => void;
  errors?: Record<string, string[]>;
  isCardWrapper?: boolean;
}

export const TributosRetencoesForm: React.FC<TributosRetencoesFormProps> = ({
  values,
  onChange,
  errors = {},
  isCardWrapper = true,
}) => {
  const content = (
    <div className="pf-form pf-form-stack">
      {/* 1. ISSQN & Responsabilidade de Retenção */}
      <section className="pf-subpanel">
        <h3 className="pf-subpanel-title">ISSQN — Regime e Responsabilidade de Retenção</h3>

        <div className="pf-form-grid">
          <Select
            label="Tributação do ISSQN (tribISSQN) *"
            value={values.issqnTaxation}
            onChange={(e) => onChange('issqnTaxation', e.target.value as any)}
            options={[
              { value: 'TAXABLE', label: '1 - Operação Tributável' },
              { value: 'IMMUNE', label: '2 - Imunidade' },
              { value: 'EXPORT', label: '3 - Exportação de Serviços' },
              { value: 'NOT_INCIDENT', label: '4 - Não Incidência' },
            ]}
          />

          <Select
            label="Tipo de Retenção e Responsável (tpRetISSQN) *"
            value={values.issRetention}
            onChange={(e) => onChange('issRetention', e.target.value as any)}
            options={ISS_RETENTION_OPTIONS}
            helperText={
              values.issRetention === 'RETAINED_BY_TOMADOR'
                ? 'Tomador recolhe o ISS na fonte.'
                : values.issRetention === 'RETAINED_BY_INTERMEDIARY'
                ? 'Intermediário recolhe o ISS na fonte.'
                : 'Prestador recolhe o ISS (sem retenção na fonte).'
            }
          />

          <Input
            label="Alíquota do ISS (%)"
            value={values.issRate}
            onChange={(e) => onChange('issRate', e.target.value)}
            className="pf-money"
            placeholder="ex: 2.00 ou 5.00"
            helperText="Opcional. Se não informada, a Sefin aplicará a alíquota parametrizada do município."
            error={errors['tax_settings.iss_rate']?.join(', ')}
          />

          {values.issqnTaxation === 'IMMUNE' && (
            <Select
              label="Tipo de Imunidade (tpImunidade) *"
              value={values.immunityType}
              onChange={(e) => onChange('immunityType', e.target.value as any)}
              options={[
                { value: '1', label: '1 - Imunidade Constitucional (Art. 150, VI, a)' },
                { value: '2', label: '2 - Templos de qualquer culto' },
                { value: '3', label: '3 - Partidos políticos / sindicatos' },
                { value: '4', label: '4 - Livros, jornais e periódicos' },
                { value: '5', label: '5 - Fonogramas musicais brasileiros' },
              ]}
              helperText="Exigido exclusivamente quando a tributação for Imunidade."
            />
          )}
        </div>
      </section>

      {/* 2. Retenções Federais na Fonte (IRRF, INSS/CP, CSLL) */}
      <section className="pf-subpanel">
        <div className="pf-subpanel-head">
          <h3 className="pf-subpanel-title">Retenções Federais na Fonte (Valores em R$)</h3>
          <p className="pf-help">
            Informe os valores efetivamente retidos pelo tomador. Deixe em branco para remover ou indicar não informado.
          </p>
        </div>

        <div className="pf-form-grid">
          <Input
            label="IRRF Retido (vRetIRRF)"
              className="pf-money"
            value={values.irrfWithheld}
            onChange={(e) => onChange('irrfWithheld', e.target.value)}
            placeholder="0,00"
            helperText="Imposto de Renda Retido na Fonte (R$)."
            error={errors['tax_settings.irrf_withheld']?.join(', ')}
          />

          <Input
            label="INSS / Previdenciária (vRetCP)"
              className="pf-money"
            value={values.inssWithheld}
            onChange={(e) => onChange('inssWithheld', e.target.value)}
            placeholder="0,00"
            helperText="Contribuição Previdenciária Retida (R$)."
            error={errors['tax_settings.inss_withheld']?.join(', ')}
          />

          <Input
            label="Contribuições Federais Retidas (vRetCSLL / PCC)"
              className="pf-money"
            value={values.csllWithheld}
            onChange={(e) => onChange('csllWithheld', e.target.value)}
            placeholder="0,00"
            helperText="Total retido das contribuições (CSLL/PIS/COFINS). Não duplica."
            error={errors['tax_settings.csll_withheld']?.join(', ')}
          />

          <Select
            label="Modalidade de Retenção Aplicável (tpRetPisCofins)"
            value={values.pisCofinsRetention}
            onChange={(e) => onChange('pisCofinsRetention', e.target.value)}
            options={TP_RET_PIS_COFINS_OPTIONS}
            helperText="Identifica no leiaute DPS quais contribuições foram retidas."
            error={errors['tax_settings.pis_cofins_retention']?.join(', ')}
          />
        </div>
      </section>

      {/* 3. PIS e COFINS (Apuração Própria / Regime Tributário) */}
      <section className="pf-subpanel">
        <div className="pf-subpanel-head">
          <h3 className="pf-subpanel-title">PIS e COFINS — Apuração do Prestador</h3>
          <p className="pf-help">Débitos próprios do prestador na DPS (não são retidos na fonte).</p>
        </div>

        <div className="pf-form-stack">
          <div className="pf-form-grid">
            <Select
              label="Situação Tributária PIS/COFINS (CST)"
              value={values.pisCofinsCst}
              onChange={(e) => onChange('pisCofinsCst', e.target.value)}
              options={PIS_COFINS_CST_OPTIONS}
              helperText="Exigido para enviar o grupo PIS/COFINS na DPS."
              error={errors['tax_settings.pis_cofins_cst']?.join(', ')}
            />
          </div>

          {values.pisCofinsCst && (
            <div className="pf-form-grid">
              <Input
                label="Base de Cálculo (R$)"
                className="pf-money"
                error={errors['tax_settings.pis_cofins_base']?.join(', ')}
                value={values.pisCofinsBase}
                onChange={(e) => onChange('pisCofinsBase', e.target.value)}
                placeholder="0,00"
                helperText="vBCPisCofins na DPS."
              />

              <Input
                label="Alíquota PIS (%)"
                className="pf-money"
                error={errors['tax_settings.pis_rate']?.join(', ')}
                value={values.pisRate}
                onChange={(e) => onChange('pisRate', e.target.value)}
                placeholder="ex: 0.65"
                helperText="Alíquota informada (não fixada)."
              />

              <Input
                label="Alíquota COFINS (%)"
                className="pf-money"
                error={errors['tax_settings.cofins_rate']?.join(', ')}
                value={values.cofinsRate}
                onChange={(e) => onChange('cofinsRate', e.target.value)}
                placeholder="ex: 3.00"
                helperText="Alíquota informada (não fixada)."
              />

              <Input
                label="Débito de PIS (vPis)"
                className="pf-money"
                error={errors['tax_settings.pis_amount']?.join(', ')}
                value={values.pisAmount}
                onChange={(e) => onChange('pisAmount', e.target.value)}
                placeholder="0,00"
                helperText="Informativo: não deduz do líquido."
              />

              <Input
                label="Débito de COFINS (vCofins)"
                className="pf-money"
                error={errors['tax_settings.cofins_amount']?.join(', ')}
                value={values.cofinsAmount}
                onChange={(e) => onChange('cofinsAmount', e.target.value)}
                placeholder="0,00"
                helperText="Informativo: não deduz do líquido."
              />
            </div>
          )}
        </div>
      </section>
    </div>
  );

  if (!isCardWrapper) {
    return content;
  }

  return (
    <Card
      title="Tributos e Retenções"
      subtitle="Configuração tributária do ISSQN, retenções federais na fonte (IRRF, INSS, CSLL/PCC) e apuração de PIS/COFINS."
    >
      {content}
    </Card>
  );
};
