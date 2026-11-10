// Nome de exibição do workspace (seletor e títulos) separado da razão social (dado fiscal).
import test from 'node:test';
import assert from 'node:assert/strict';
import { nomeExibicao } from '../src/types.ts';

test('usa o nome do workspace quando houver', () => {
  assert.equal(nomeExibicao({ name: 'EMPRESA FICTÍCIA LTDA', display_name: 'Filial Fictícia - Norte' }),
    'Filial Fictícia - Norte');
});

test('sem nome de workspace (resposta antiga ou modo demonstração): razão social', () => {
  assert.equal(nomeExibicao({ name: 'EMPRESA FICTÍCIA LTDA' }), 'EMPRESA FICTÍCIA LTDA');
  assert.equal(nomeExibicao({ name: 'EMPRESA FICTÍCIA LTDA', display_name: '' }), 'EMPRESA FICTÍCIA LTDA');
  assert.equal(nomeExibicao(null), '');
});
