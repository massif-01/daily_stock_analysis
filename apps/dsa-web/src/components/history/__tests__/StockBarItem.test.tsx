import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StockBarItemComponent } from '../StockBarItem';
import type { StockBarItem } from '../../../types/analysis';

const issue1600Item: StockBarItem = {
  id: 1,
  stockCode: '600519',
  stockName: '贵州茅台股票股份有限公司',
  sentimentScore: 62,
  operationAdvice: '观望',
  analysisCount: 2,
  lastAnalysisTime: '2026-05-31T04:52:00Z',
  marketPhaseSummary: {
    market: 'CN',
    phase: 'non_trading',
    warnings: [],
  },
};

describe('StockBarItemComponent', () => {
  it('keeps market phase in the meta row instead of the action row', () => {
    render(
      <StockBarItemComponent
        item={issue1600Item}
        isViewing={false}
        onClick={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    const meta = screen.getByTestId('history-card-meta');

    expect(within(actions).getByText('观望 62')).toBeInTheDocument();
    expect(within(actions).getByRole('button', { name: /删除 贵州茅台股票股份有限公司 历史记录/ })).toBeInTheDocument();
    expect(within(actions).queryByText('CN · 非交易日')).not.toBeInTheDocument();
    expect(within(meta).getByText('CN · 非交易日')).toBeVisible();

    expect(screen.getByText('贵州茅台股票股份.')).toBeVisible();
    expect(
      screen.getByRole('button', {
        name: /^贵州茅台股票股份有限公司 600519 历史记录$/,
      }),
    ).toBeInTheDocument();
  });

  it('uses structured action label before legacy operation advice', () => {
    render(
      <StockBarItemComponent
        item={{
          ...issue1600Item,
          action: 'avoid',
          actionLabel: '回避',
          operationAdvice: '买入',
          sentimentScore: 35,
        }}
        isViewing={false}
        onClick={vi.fn()}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    expect(within(actions).getByText('回避 35')).toBeInTheDocument();
    expect(within(actions).queryByText('买入 35')).not.toBeInTheDocument();
  });

  it('uses the unified legacy fallback for negated buy advice without structured action', () => {
    render(
      <StockBarItemComponent
        item={{
          ...issue1600Item,
          action: null,
          actionLabel: null,
          operationAdvice: '不建议买入，等待确认',
          sentimentScore: 28,
        }}
        isViewing={false}
        onClick={vi.fn()}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    expect(within(actions).getByText('回避 28')).toBeInTheDocument();
    expect(within(actions).queryByText('买入 28')).not.toBeInTheDocument();
  });

  it('does not render ambiguous English legacy advice as a buy action', () => {
    render(
      <StockBarItemComponent
        item={{
          ...issue1600Item,
          action: null,
          actionLabel: null,
          operationAdvice: 'buy or sell',
          sentimentScore: 28,
        }}
        isViewing={false}
        onClick={vi.fn()}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    expect(within(actions).queryByText('buy 28')).not.toBeInTheDocument();
    expect(within(actions).queryByText(/28/)).not.toBeInTheDocument();
  });

  it('does not render multi-guard legacy advice as an action badge', () => {
    render(
      <StockBarItemComponent
        item={{
          ...issue1600Item,
          action: null,
          actionLabel: null,
          operationAdvice: 'risk alert, avoid buying',
          sentimentScore: 28,
        }}
        isViewing={false}
        onClick={vi.fn()}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    expect(within(actions).queryByText('回避 28')).not.toBeInTheDocument();
    expect(within(actions).queryByText('预警 28')).not.toBeInTheDocument();
    expect(within(actions).queryByText(/28/)).not.toBeInTheDocument();
  });
});
