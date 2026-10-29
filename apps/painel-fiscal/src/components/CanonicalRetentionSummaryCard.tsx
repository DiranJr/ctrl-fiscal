import React from 'react';
import {
  RetentionSummary,
  formatCurrencyPtBr,
  formatOptionalCurrency,
  formatOptionalPercent,
} from '@ctrl-fiscal/api-client';
import { origemAliquota } from './aliquotaIss.js';

interface CanonicalRetentionSummaryCardProps {
  summary: RetentionSummary;
  title?: string;
  subtitle?: string;
  /** backend = conferido por POST /retention-preview (canônico); local = prévia aguardando conferência. */
  source?: 'backend' | 'local' | 'invalid';
  diverged?: boolean;
}

export const CanonicalRetentionSummaryCard: React.FC<CanonicalRetentionSummaryCardProps> = ({
  summary,
  title = 'Resumo de valores e retenções',
  subtitle = 'Cálculo do backend conforme o leiaute DPS v1.01 (Anexo I). PIS/COFINS de apuração própria informados não entram no total, exceto na modalidade 1 (ver aviso).',
  source = 'backend',
  diverged = false,
}) => {
  const isNetNegative = parseFloat(summary.net_amount) < 0;

  return (
    <div
      id="canonical-retention-summary"
      style={{
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-lg)',
        backgroundColor: 'var(--color-bg-card)',
        padding: '1.25rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '1rem',
      }}
    >
      <div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.5rem' }}>
          <h4 style={{ margin: 0, fontSize: '1rem', fontWeight: 700, color: 'var(--color-text-main)' }}>
            {title}
          </h4>
          <span
            title="Antes da autorização os valores são estimados; os oficiais vêm da NFS-e autorizada pela Sefin."
            style={{ fontSize: '0.75rem', padding: '0.2rem 0.5rem', borderRadius: '4px', fontWeight: 600,
              backgroundColor: 'var(--color-bg-subtle)', color: 'var(--color-text-subtle)' }}
          >
            Estimativa — valores oficiais na autorização
          </span>
          {!summary.complete && (
            <span
              style={{
                fontSize: '0.75rem',
                padding: '0.2rem 0.5rem',
                borderRadius: '4px',
                backgroundColor: '#FEF3C7',
                color: '#92400E',
                fontWeight: 600,
              }}
            >
              ! Total parcial: há valores a confirmar
            </span>
          )}
        </div>
        <p role="status" aria-live="polite" style={{ margin: '0.25rem 0 0 0', fontSize: '0.75rem', fontWeight: 600,
          color: source === 'backend' ? 'var(--color-primary)' : 'var(--status-processing-text)' }}>
          {source === 'backend' ? '✓ Conferido pelo backend (mesmo cálculo da revisão, do XML e do documento)'
            : source === 'invalid' ? '! Prévia local: complete os valores para a conferência do backend'
            : '◔ Prévia local — conferindo no backend…'}
          {diverged && ' · a prévia local divergia; prevalece o valor do backend'}
        </p>
        {subtitle && (
          <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.8125rem', color: 'var(--color-text-muted)' }}>
            {subtitle}
          </p>
        )}
      </div>

      {/* Grid: Composição de Valores e Base */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
          gap: '0.75rem',
          backgroundColor: 'var(--color-bg-subtle)',
          padding: '0.875rem',
          borderRadius: 'var(--radius-md)',
        }}
      >
        <div>
          <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>Valor Bruto do Serviço (vServ)</div>
          <div style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--color-text-main)' }}>
            {formatCurrencyPtBr(summary.service_amount)}
          </div>
        </div>

        <div>
          <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>Desconto Incondicionado</div>
          <div style={{ fontSize: '0.9375rem', fontWeight: 500, color: 'var(--color-text-main)' }}>
            {formatOptionalCurrency(summary.unconditional_discount)}
          </div>
        </div>

        <div>
          <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>Desconto Condicionado</div>
          <div style={{ fontSize: '0.9375rem', fontWeight: 500, color: 'var(--color-text-main)' }}>
            {formatOptionalCurrency(summary.conditional_discount)}
          </div>
        </div>

        <div>
          <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>Deduções da Base de Cálculo (vDR)</div>
          <div style={{ fontSize: '0.9375rem', fontWeight: 500, color: 'var(--color-text-main)' }}>
            {formatOptionalCurrency(summary.deductions)}
          </div>
        </div>

        <div>
          <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>Base de Cálculo do ISSQN (vBC)</div>
          <div style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--color-primary)' }}>
            {formatCurrencyPtBr(summary.iss.base)}
          </div>
        </div>
      </div>

      {/* Seção: Retenções Discriminadas */}
      <div>
        <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: 'var(--color-text-main)', marginBottom: '0.5rem' }}>
          Discriminativo das Retenções na Fonte:
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.75rem' }}>
          {/* ISS Retido */}
          <div style={{ padding: '0.75rem', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: '0.75rem', fontWeight: 600 }}>ISSQN Retido</span>
              <span style={{ fontSize: '0.6875rem', color: 'var(--color-text-subtle)' }}>
                {summary.iss.retained ? (summary.iss.retention === 'RETAINED_BY_TOMADOR' ? 'Tomador' : 'Intermediário') : 'Prestador (Não retido)'}
              </span>
            </div>
            <div style={{ fontSize: '1rem', fontWeight: 700, marginTop: '0.25rem', color: summary.iss.retained ? '#DC2626' : 'var(--color-text-main)' }}>
              {summary.iss.retained
                ? (summary.iss.amount ? formatCurrencyPtBr(summary.iss.amount) : 'A definir pela Sefin (não é zero)')
                : 'Não retido (R$ 0,00)'}
            </div>
            <div style={{ fontSize: '0.6875rem', color: 'var(--color-text-subtle)', marginTop: '0.125rem' }}>
              {summary.iss.rate
                ? <>Alíquota usada: {formatOptionalPercent(summary.iss.rate)} ({origemAliquota(summary.iss.rate_source)})</>
                : 'Sem pAliq e sem alíquota de referência: a Sefin aplica a do município'}
            </div>
            {summary.iss.estimated_amount && (
              <div style={{ fontSize: '0.6875rem', color: 'var(--color-text-subtle)' }}>
                ISSQN estimado: {formatCurrencyPtBr(summary.iss.estimated_amount)}
              </div>
            )}
          </div>

          {/* IRRF Retido */}
          <div style={{ padding: '0.75rem', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 600 }}>IRRF Retido (vRetIRRF)</div>
            <div style={{ fontSize: '1rem', fontWeight: 700, marginTop: '0.25rem', color: summary.federal.irrf ? '#DC2626' : 'var(--color-text-main)' }}>
              {formatOptionalCurrency(summary.federal.irrf)}
            </div>
            <div style={{ fontSize: '0.6875rem', color: 'var(--color-text-subtle)', marginTop: '0.125rem' }}>
              Imposto de Renda Retido na Fonte
            </div>
          </div>

          {/* INSS Retido */}
          <div style={{ padding: '0.75rem', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 600 }}>INSS / Previdenciária (vRetCP)</div>
            <div style={{ fontSize: '1rem', fontWeight: 700, marginTop: '0.25rem', color: summary.federal.inss ? '#DC2626' : 'var(--color-text-main)' }}>
              {formatOptionalCurrency(summary.federal.inss)}
            </div>
            <div style={{ fontSize: '0.6875rem', color: 'var(--color-text-subtle)', marginTop: '0.125rem' }}>
              Contribuição Previdenciária Retida
            </div>
          </div>

          {/* Contribuições Federais (CSLL / PIS / COFINS) */}
          <div style={{ padding: '0.75rem', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 600 }}>Contribuições Federais (vRetCSLL / PCC)</div>
            <div style={{ fontSize: '1rem', fontWeight: 700, marginTop: '0.25rem', color: summary.federal.contributions ? '#DC2626' : 'var(--color-text-main)' }}>
              {formatOptionalCurrency(summary.federal.contributions)}
            </div>
            <div style={{ fontSize: '0.6875rem', color: 'var(--color-text-subtle)', marginTop: '0.125rem' }}>
              {summary.federal.contributions_label || 'Total das contribuições retidas (sem duplicidade)'}
            </div>
          </div>
        </div>
      </div>

      {summary.federal.code1_pis_cofins && (
        <div role="note" style={{ fontSize: '0.75rem', padding: '0.5rem 0.75rem', backgroundColor: 'var(--status-processing-bg)',
          border: '1px solid var(--status-processing-border)', borderRadius: 'var(--radius-md)', color: 'var(--status-processing-text)' }}>
          <strong>! Modalidade 1 (PIS/COFINS retidos):</strong> PIS {formatOptionalCurrency(summary.federal.code1_pis_cofins.pis)} |
          COFINS {formatOptionalCurrency(summary.federal.code1_pis_cofins.cofins)}. A NT 008 v1.02 apresenta esses valores como
          contribuições retidas no DANFSe. Ainda não está confirmado se a Sefin os inclui no total retido; esta prévia não os soma e o
          valor oficial virá da NFS-e autorizada.
        </div>
      )}
      {/* Informativo: Débitos Próprios PIS/COFINS (Não Retidos) */}
      {(summary.informed_not_withheld.pis || summary.informed_not_withheld.cofins) && (
        <div
          style={{
            fontSize: '0.75rem',
            padding: '0.5rem 0.75rem',
            backgroundColor: 'var(--color-bg-subtle)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--color-text-subtle)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '0.5rem',
          }}
        >
          <div>
            <strong>PIS e COFINS de apuração própria:</strong> PIS {formatOptionalCurrency(summary.informed_not_withheld.pis)} | COFINS {formatOptionalCurrency(summary.informed_not_withheld.cofins)}
          </div>
          <div style={{ fontStyle: 'italic' }}>
            Débitos do prestador informados na DPS (não são retidos e não reduzem o líquido).
          </div>
        </div>
      )}

      {/* Totais Finais: Total Retido e Valor Líquido */}
      <div
        style={{
          borderTop: '1px solid var(--color-border)',
          paddingTop: '0.875rem',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: '1rem',
          alignItems: 'center',
        }}
      >
        <div
          style={{
            padding: '0.75rem 1rem',
            backgroundColor: '#FEF2F2',
            borderRadius: 'var(--radius-md)',
            border: '1px solid #FCA5A5',
          }}
        >
          <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#991B1B' }}>
            Total das Retenções (vTotalRet) — estimado
          </div>
          <div style={{ fontSize: '1.25rem', fontWeight: 700, color: '#DC2626' }}>
            {formatCurrencyPtBr(summary.total_withheld)}
          </div>
          <div style={{ fontSize: '0.6875rem', color: '#B91C1C' }}>
            INSS + IRRF + Contribuições (CSLL/PCC) + ISS retido
          </div>
        </div>

        <div
          style={{
            padding: '0.75rem 1rem',
            backgroundColor: isNetNegative ? '#FEF2F2' : '#F0FDF4',
            borderRadius: 'var(--radius-md)',
            border: `1px solid ${isNetNegative ? '#F87171' : '#86EFAC'}`,
          }}
        >
          <div style={{ fontSize: '0.75rem', fontWeight: 600, color: isNetNegative ? '#991B1B' : '#166534' }}>
            Valor Líquido da NFS-e (vLiq) — estimado
          </div>
          <div style={{ fontSize: '1.375rem', fontWeight: 800, color: isNetNegative ? '#DC2626' : '#15803D' }}>
            {formatCurrencyPtBr(summary.net_amount)}
          </div>
          <div style={{ fontSize: '0.6875rem', color: isNetNegative ? '#B91C1C' : '#166534' }}>
            {isNetNegative ? '⚠️ Atenção: Descontos e retenções superam o valor bruto!' : 'Valor a receber após retenções e descontos'}
          </div>
        </div>
      </div>
    </div>
  );
};
