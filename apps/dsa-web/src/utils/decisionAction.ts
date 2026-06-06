import type { DecisionAction } from '../types/analysis';

export type DecisionActionTone = 'success' | 'warning' | 'danger' | 'default';

const ACTION_LABELS: Record<DecisionAction, string> = {
  buy: '买入',
  add: '加仓',
  hold: '持有',
  reduce: '减仓',
  sell: '卖出',
  watch: '观望',
  avoid: '回避',
  alert: '预警',
};

const firstAdviceToken = (value?: string | null): string | null => {
  const normalized = value?.trim();
  if (!normalized) return null;
  return normalized.split(/[，。；、\s]/)[0] || null;
};

const includesAny = (value: string, phrases: readonly string[]): boolean =>
  phrases.some((phrase) => value.includes(phrase));

export const getLegacyDecisionActionLabel = (advice?: string | null): string | null => {
  const normalized = advice?.trim();
  if (!normalized) return null;
  const lower = normalized.toLowerCase();

  if (
    includesAny(normalized, [
      '暂不买入',
      '不要买入',
      '不宜买入',
      '先不买入',
      '暂不建仓',
      '不要建仓',
      '不宜建仓',
      '先不建仓',
      '暂不布局',
      '不要布局',
      '不宜布局',
      '先不布局',
    ]) ||
    lower.includes('do not buy') ||
    lower.includes('no buy')
  ) {
    return '回避';
  }
  if (
    includesAny(normalized, [
      '不建议加仓',
      '无需加仓',
      '不要加仓',
      '不宜加仓',
      '暂不加仓',
      '不建议增持',
      '无需增持',
      '不要增持',
      '不宜增持',
      '暂不增持',
      '不建议卖出',
      '无需卖出',
      '不要卖出',
      '不宜卖出',
      '暂不卖出',
      '不建议减仓',
      '无需减仓',
      '不要减仓',
      '不宜减仓',
      '暂不减仓',
      '不建议清仓',
      '无需清仓',
      '不要清仓',
      '不宜清仓',
      '暂不清仓',
    ]) ||
    lower.includes('do not sell') ||
    lower.includes('no sell')
  ) {
    return '持有';
  }
  if (
    normalized.includes('不建议买入') ||
    normalized.includes('避免买入') ||
    normalized.includes('回避') ||
    normalized.includes('规避') ||
    lower.includes('do not buy')
  ) {
    return '回避';
  }
  if (
    normalized.includes('风险预警') ||
    normalized.includes('触发告警') ||
    normalized.includes('警惕') ||
    lower.includes('risk alert')
  ) {
    return '预警';
  }
  if (normalized.includes('加仓') || normalized.includes('增持')) return '加仓';
  if (normalized.includes('减仓')) return '减仓';
  if (normalized.includes('卖') || normalized.includes('清仓')) return '卖出';
  if (normalized.includes('持有')) return '持有';
  if (normalized.includes('观望') || normalized.includes('等待')) return '观望';
  if (normalized.includes('买') || normalized.includes('布局') || normalized.includes('建仓')) return '买入';
  return firstAdviceToken(normalized);
};

export const getDecisionActionLabel = (
  action?: DecisionAction | null,
  actionLabel?: string | null,
  legacyAdvice?: string | null,
  emptyLabel: string | null = '建议',
): string | null => {
  const explicitLabel = actionLabel?.trim();
  if (explicitLabel) return explicitLabel;
  if (action) return ACTION_LABELS[action];
  return getLegacyDecisionActionLabel(legacyAdvice) || emptyLabel;
};

export const getDecisionActionTone = (
  action?: DecisionAction | null,
  actionLabel?: string | null,
  legacyAdvice?: string | null,
): DecisionActionTone => {
  if (action === 'buy' || action === 'add' || action === 'hold') return 'success';
  if (action === 'sell' || action === 'reduce') return 'danger';
  if (action === 'watch' || action === 'avoid' || action === 'alert') return 'warning';

  const label = getDecisionActionLabel(null, actionLabel, legacyAdvice, null) || '';
  if (label.includes('买') || label.includes('加仓') || label.includes('持有')) return 'success';
  if (label.includes('卖') || label.includes('减仓') || label.includes('清仓')) return 'danger';
  if (label.includes('观望') || label.includes('等待') || label.includes('回避') || label.includes('预警')) {
    return 'warning';
  }
  return 'default';
};
