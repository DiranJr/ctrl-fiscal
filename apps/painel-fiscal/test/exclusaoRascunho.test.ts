// Excluir rascunho no painel: a decisão vem do servidor (deletion), não do simples vínculo current_item_id.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Draft } from '@ctrl-fiscal/api-client';
import { motivoNaoExcluir, SEM_PERMISSAO_EXCLUIR } from '../src/components/exclusaoRascunho.ts';

const base = { id: 'd1', customer_id: 'c1', version: 3, competence: '2026-10', amount: '10.00', created_at: '', updated_at: '',
  service: { national_code: '010101', description: 'Serviço' } } as unknown as Draft;

test('rascunho corrigido após rejeição local: vínculo antigo não esconde nem bloqueia o Excluir', () => {
  const d = { ...base, status: 'DRAFT', current_item_id: 'item-rejeitado', deletion: { allowed: true } } as Draft;
  assert.equal(motivoNaoExcluir(d, true), null);
});

test('impedimento do servidor vira motivo exibido (agendado, histórico de transmissão)', () => {
  const ag = { ...base, status: 'SCHEDULED', deletion: { allowed: false, reason_code: 'DRAFT_SCHEDULED',
    reason: 'Rascunho agendado. Retire-o da fila (cancelar o agendamento) antes de excluir.' } } as Draft;
  assert.match(motivoNaoExcluir(ag, true)!, /Retire-o da fila/);
  const tx = { ...base, status: 'DRAFT', current_item_id: 'i', deletion: { allowed: false, reason_code: 'DRAFT_TRANSMITTED',
    reason: 'Rascunho com histórico de transmissão: ...' } } as Draft;
  assert.match(motivoNaoExcluir(tx, true)!, /histórico de transmissão/);
});

test('sem a permissão "prepare" o motivo é a permissão, mesmo quando o servidor permitiria', () => {
  const d = { ...base, status: 'DRAFT', deletion: { allowed: true } } as Draft;
  assert.equal(motivoNaoExcluir(d, false), SEM_PERMISSAO_EXCLUIR);
});

test('resposta sem avaliação (demonstração): só a situação decide; o servidor confere ao excluir', () => {
  assert.equal(motivoNaoExcluir({ ...base, status: 'REJECTED' } as Draft, true), null);
  assert.match(motivoNaoExcluir({ ...base, status: 'AUTHORIZED' } as Draft, true)!, /AUTHORIZED/);
});
