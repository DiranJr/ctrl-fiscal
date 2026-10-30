import type { Draft } from '@ctrl-fiscal/api-client';

/**
 * Excluir rascunho na tela: a ação aparece sempre (exceto em rascunho já excluído) e, quando houver impedimento, fica
 * desabilitada com o motivo. Quem decide é o servidor (`deletion`, pelo histórico real dos itens de emissão — um
 * vínculo antigo com item retirado ou barrado pela validação local não impede); a permissão vem do workspace. O
 * servidor confere tudo de novo, com a linha travada, ao excluir.
 */

export const SEM_PERMISSAO_EXCLUIR = 'Sem a permissão “Preparar rascunhos” neste workspace: peça a um administrador.';

/** Motivo que impede excluir; null = pode excluir. */
export function motivoNaoExcluir(d: Draft, podePreparar: boolean): string | null {
  if (!podePreparar) return SEM_PERMISSAO_EXCLUIR;
  if (d.deletion) return d.deletion.allowed ? null : d.deletion.reason || 'Este rascunho não pode ser excluído.';
  // Resposta sem avaliação (ex.: modo demonstração): só a situação; o servidor decide na exclusão.
  return ['DRAFT', 'WITHDRAWN', 'REJECTED'].includes(d.status) ? null : `Rascunho em ${d.status} não pode ser excluído.`;
}
