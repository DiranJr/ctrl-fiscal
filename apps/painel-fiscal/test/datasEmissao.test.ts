// Prestação futura x emissão (E0015) no painel: dia no fuso da empresa e conflitos de "Emitir agora" e agendamento.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { conflitoPrestacao, diaNoFuso, prestacaoPosterior, sugestaoAgendamento } from '../src/components/datasEmissao.ts';

test('dia no fuso da empresa (não o do navegador nem o UTC)', () => {
  const i = new Date('2026-10-25T02:30:00Z'); // 24/10 23:30 em São Paulo
  assert.equal(diaNoFuso(i, 'America/Sao_Paulo'), '2026-10-24');
  assert.equal(diaNoFuso(i, 'UTC'), '2026-10-25');
});

test('emitir agora: bloqueia prestação futura e libera a de hoje ou passada', () => {
  const c = conflitoPrestacao(['2026-10-20', '2026-10-25', '2026-10-23'], 'NOW', '2026-10-20');
  assert.equal(c?.prestacao, '2026-10-25');
  assert.match(c!.mensagem, /não é possível emitir agora.*25\/10\/2026 ou depois/);
  assert.equal(conflitoPrestacao(['2026-10-20', '2026-09-30', null], 'NOW', '2026-10-20'), null);
});

test('agendamento: mesmo dia e dia posterior aceitos; anterior explica e pede ajuste', () => {
  assert.equal(conflitoPrestacao(['2026-10-25'], 'SCHEDULED', '2026-10-20', '2026-10-25T00:00'), null);
  assert.equal(conflitoPrestacao(['2026-10-25'], 'SCHEDULED', '2026-10-20', '2026-10-27T09:00'), null);
  const c = conflitoPrestacao(['2026-10-25'], 'SCHEDULED', '2026-10-20', '2026-10-24T23:59');
  assert.match(c!.mensagem, /24\/10\/2026 23:59.*anterior à prestação.*25\/10\/2026 ou depois/);
  assert.equal(conflitoPrestacao(['2026-10-25'], 'SCHEDULED', '2026-10-20', ''), null); // sem data ainda: nada a comparar
});

test('maior prestação futura e sugestão no próprio dia', () => {
  assert.equal(prestacaoPosterior(['2026-11-03', 'x', '2026-10-30'], '2026-10-28'), '2026-11-03');
  assert.equal(sugestaoAgendamento('2026-11-03'), '2026-11-03T00:05');
});
