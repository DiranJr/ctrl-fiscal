/** Tributos aproximados (totTrib, Lei 12.741/2012): VALUES = valores em R$ (vTotTrib); PERCENT = percentuais
 * (pTotTrib). Mesmas regras no editor de rascunhos e na Nova NFS-e. */

/** Percentual com vírgula ou ponto → ponto e 2 casas (TSDec3V2). Texto inválido segue como está (o servidor aponta).
 * Zero digitado continua zero ("0" → "0.00"); campo vazio não é tratado aqui (não vira zero). */
export function normalizarPercentual(v: string): string {
  const s = v.trim().replace(',', '.');
  return /^\d{1,3}(\.\d+)?$/.test(s) ? Number(s).toFixed(2) : s;
}

/** Troca entre moeda e percentual com números preenchidos: devolve o aviso (quem chama limpa os campos). */
export function trocaDeUnidade(de: string, para: string, valores: string[]): string | null {
  const unidade = (m: string) => (m === 'VALUES' ? 'R$' : m === 'PERCENT' ? '%' : null);
  if (de === para || !unidade(de) || !unidade(para) || !valores.some((v) => v.trim())) return null;
  return `Os números informados em ${unidade(de)} foram apagados porque o modo mudou para ${unidade(para)}. `
    + (para === 'PERCENT' ? 'Informe os percentuais federal, estadual e municipal.'
      : 'Informe os valores em reais federal, estadual e municipal.');
}
