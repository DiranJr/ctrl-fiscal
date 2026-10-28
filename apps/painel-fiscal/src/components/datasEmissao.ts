/**
 * Prestação (dCompet) x data de emissão — regra E0015 do Anexo I: dCompet não pode ser posterior à data do dhEmi.
 * O dhEmi é o instante real da transmissão, no fuso da empresa: "Emitir agora" usa hoje; o agendamento, o dia programado.
 * O servidor confere de novo ao emitir ou agendar e o worker antes de transmitir; aqui só explicamos antes.
 */

/** Dia (AAAA-MM-DD) de um instante no fuso IANA da empresa. */
export function diaNoFuso(instante: Date, fuso: string): string {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: fuso, year: 'numeric', month: '2-digit', day: '2-digit' })
    .formatToParts(instante).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}`;
}

/** AAAA-MM-DD → DD/MM/AAAA. */
export const dataBr = (iso: string) => iso.slice(0, 10).split('-').reverse().join('/');

/** Maior data de prestação POSTERIOR ao dia da emissão (o que exige ajuste), ou null se não há conflito. */
export function prestacaoPosterior(prestacoes: (string | null | undefined)[], diaEmissao: string): string | null {
  const futuras = prestacoes.filter((d): d is string => !!d && /^\d{4}-\d{2}-\d{2}$/.test(d) && d > diaEmissao).sort();
  return futuras.length ? futuras[futuras.length - 1] : null;
}

/** Horário sugerido para agendar no dia da prestação (o worker só transmite a partir das 00:02 desse dia). */
export const sugestaoAgendamento = (dia: string) => `${dia}T00:05`;

/** Texto do conflito, ou null. `modo`: NOW = emitir agora (dia de hoje); SCHEDULED = data programada (datetime-local). */
export function conflitoPrestacao(prestacoes: (string | null | undefined)[], modo: 'NOW' | 'SCHEDULED', hoje: string,
  agendamentoLocal?: string): { prestacao: string; mensagem: string } | null {
  const dia = modo === 'NOW' ? hoje : (agendamentoLocal || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dia)) return null;
  const prestacao = prestacaoPosterior(prestacoes, dia);
  if (!prestacao) return null;
  const mensagem = modo === 'NOW'
    ? `A prestação (${dataBr(prestacao)}) é posterior a hoje (${dataBr(hoje)}): não é possível emitir agora, porque a data `
      + `da prestação não pode ser posterior à emissão (regra E0015). Agende a emissão para ${dataBr(prestacao)} ou depois.`
    : `A emissão programada (${dataBr(dia)} ${(agendamentoLocal || '').slice(11, 16)}) é anterior à prestação `
      + `(${dataBr(prestacao)}): a data da prestação não pode ser posterior à emissão (regra E0015). Ajuste o `
      + `agendamento para ${dataBr(prestacao)} ou depois.`;
  return { prestacao, mensagem };
}
