import { describe, expect, it } from 'vitest';
import {
  getDecisionActionLabel,
  getDecisionActionTone,
  getLegacyDecisionActionLabel,
} from '../decisionAction';

describe('decisionAction helpers', () => {
  it('prefers structured action label over legacy advice text', () => {
    expect(getDecisionActionLabel('avoid', '回避', '买入', '建议')).toBe('回避');
  });

  it('falls back to the action taxonomy label when actionLabel is absent', () => {
    expect(getDecisionActionLabel('add', null, '持有', '建议')).toBe('加仓');
  });

  it('keeps legacy fallback compatible with negated buy advice', () => {
    expect(getLegacyDecisionActionLabel('不建议买入，等待确认')).toBe('回避');
    expect(getDecisionActionLabel(null, null, '避免买入', '建议')).toBe('回避');
    expect(getLegacyDecisionActionLabel('暂不买入，等待确认')).toBe('回避');
    expect(getLegacyDecisionActionLabel('先不建仓，等待放量')).toBe('回避');
  });

  it('keeps legacy fallback compatible with negated sell and add advice', () => {
    expect(getLegacyDecisionActionLabel('不建议卖出，继续观察')).toBe('持有');
    expect(getLegacyDecisionActionLabel('无需减仓，维持仓位')).toBe('持有');
    expect(getLegacyDecisionActionLabel('不建议加仓，等待回踩')).toBe('持有');
    expect(getDecisionActionTone(null, null, '不建议卖出，继续观察')).toBe('success');
  });

  it('maps action tone without reading legacy text when action is present', () => {
    expect(getDecisionActionTone('buy', null, '卖出')).toBe('success');
    expect(getDecisionActionTone('reduce', null, '买入')).toBe('danger');
    expect(getDecisionActionTone('alert', null, '买入')).toBe('warning');
  });
});
