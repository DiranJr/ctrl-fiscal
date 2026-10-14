// Seleção acumulada de notas (download em lote): regras puras do armazenamento, sem navegador.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { SelecaoNotas, chaveContexto, conferirDisponibilidade, type NotaSelecionada } from '../src/components/documents/selecaoNotas.ts';

const nota = (id: string, extra: Partial<NotaSelecionada> = {}): NotaSelecionada => ({
  id, number: id.toUpperCase(), access_key: `chave-${id}`, issued_at: null, counterpart: 'Fictícia', counterpart_doc: null,
  amount: '10.00', environment: 'mock', xml_available: true, danfse_available: true, ...extra,
});
const A = chaveContexto({ company: 'wsA', direction: 'RECEIVED', env: '' });

test('acumula entre pesquisas e páginas e conta também as não visíveis', () => {
  const s = new SelecaoNotas();
  s.entrar(A);
  s.alternar(A, nota('n1')); // pesquisa 1
  s.marcarPagina(A, [nota('n2'), nota('n3')], true); // pesquisa 2, página inteira
  assert.deepEqual([...s.itens(A).keys()], ['n1', 'n2', 'n3']);
});

test('o cabeçalho desmarca só a página atual', () => {
  const s = new SelecaoNotas();
  s.entrar(A);
  s.alternar(A, nota('outra'));
  s.marcarPagina(A, [nota('p1'), nota('p2')], true);
  s.marcarPagina(A, [nota('p1'), nota('p2')], false);
  assert.deepEqual([...s.itens(A).keys()], ['outra']);
});

test('remoção individual, limpar seleção e alternar a mesma nota', () => {
  const s = new SelecaoNotas();
  s.entrar(A);
  s.marcarPagina(A, [nota('a'), nota('b'), nota('c')], true);
  s.remover(A, ['b']);
  s.alternar(A, nota('c'));
  assert.deepEqual([...s.itens(A).keys()], ['a']);
  s.limpar(A);
  assert.equal(s.itens(A).size, 0);
});

test('trocar workspace, direção ou ambiente começa vazio; ação atrasada do contexto anterior é ignorada', () => {
  const s = new SelecaoNotas<{ q: string }>();
  s.entrar(A);
  s.alternar(A, nota('x'));
  s.guardarFiltros(A, { q: 'pesquisa' });
  for (const outro of [
    chaveContexto({ company: 'wsB', direction: 'RECEIVED', env: '' }),
    chaveContexto({ company: 'wsA', direction: 'ISSUED', env: '' }),
    chaveContexto({ company: 'wsA', direction: 'RECEIVED', env: 'production' }),
  ]) {
    assert.equal(s.itens(outro).size, 0, outro); // nada aparece no outro contexto, nem antes de entrar
    s.entrar(outro);
    s.alternar(A, nota('atrasada')); // clique/resposta do contexto anterior
    assert.equal(s.itens(outro).size, 0);
    assert.equal(s.itens(A).size, 0);
  }
  s.entrar(A);
  assert.equal(s.itens(A).size, 0, 'voltar ao contexto anterior depois de trocar não ressuscita a seleção');
});

test('navegação interna: mesmo contexto mantém a seleção e retoma a consulta', () => {
  const s = new SelecaoNotas<{ q: string; page: number }>();
  s.entrar(A);
  s.alternar(A, nota('m'));
  s.guardarFiltros(A, { q: 'abc', page: 3 });
  s.entrar(A); // tela desmontada e montada de novo
  assert.deepEqual([...s.itens(A).keys()], ['m']);
  assert.deepEqual(s.ultimaConsulta('wsA', 'RECEIVED'), { q: 'abc', page: 3 });
  assert.equal(s.ultimaConsulta('wsA', 'ISSUED'), null);
  assert.equal(s.ultimaConsulta('wsB', 'RECEIVED'), null);
});

test('conferência aponta quais notas estão indisponíveis e o motivo', async () => {
  const falha = (status?: number) => Object.assign(new Error('x'), status ? { problem: { status } } : {});
  const buscar = async (id: string) => {
    if (id === 'sumiu') throw falha(404);
    if (id === 'proibida') throw falha(403);
    if (id === 'rede') throw falha();
    return { environment: id === 'outroamb' ? 'production' : 'mock' };
  };
  const r = await conferirDisponibilidade(['ok', 'sumiu', 'proibida', 'outroamb', 'rede'], buscar, () => 'mock', 2);
  assert.deepEqual(r.map((i) => [i.id, i.definitivo]), [['sumiu', true], ['proibida', true], ['outroamb', true], ['rede', false]]);
  assert.match(r[0].motivo, /Não encontrada/);
  assert.match(r[1].motivo, /permissão/);
  assert.match(r[2].motivo, /outro ambiente/);
});
