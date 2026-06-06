import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StockHistoryTrendDrawer } from '../StockHistoryTrendDrawer';
import type { AnalysisReport, HistoryItem } from '../../../types/analysis';

const report: AnalysisReport = {
  meta: {
    id: 1,
    queryId: 'q-1',
    stockCode: '600519',
    stockName: '贵州茅台',
    reportType: 'detailed',
    createdAt: '2026-03-20T08:00:00Z',
  },
  summary: {
    analysisSummary: '等待确认',
    operationAdvice: '买入',
    action: 'avoid',
    actionLabel: '回避',
    trendPrediction: '震荡',
    sentimentScore: 35,
  },
};

const items: HistoryItem[] = [
  {
    id: 1,
    queryId: 'q-1',
    stockCode: '600519',
    stockName: '贵州茅台',
    sentimentScore: 35,
    operationAdvice: '买入',
    action: 'avoid',
    actionLabel: '回避',
    trendPrediction: '震荡',
    createdAt: '2026-03-20T08:00:00Z',
  },
];

describe('StockHistoryTrendDrawer', () => {
  it('prefers structured action label in summary and rows', () => {
    render(
      <StockHistoryTrendDrawer
        report={report}
        items={items}
        total={1}
        hasMore={false}
        isLoading={false}
        isLoadingMore={false}
        filters={{ range: 'all', model: 'all', sort: 'desc' }}
        onClose={vi.fn()}
        onRangeChange={vi.fn()}
        onLoadMore={vi.fn()}
        onSelectRecord={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getAllByText('回避').length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText('买入')).not.toBeInTheDocument();
  });

  it('keeps full legacy operation advice when structured action is absent', () => {
    render(
      <StockHistoryTrendDrawer
        report={{
          ...report,
          summary: {
            ...report.summary,
            operationAdvice: '继续持有，等待突破',
            action: null,
            actionLabel: null,
          },
        }}
        items={[
          {
            ...items[0],
            operationAdvice: '继续持有，等待突破',
            action: null,
            actionLabel: null,
          },
        ]}
        total={1}
        hasMore={false}
        isLoading={false}
        isLoadingMore={false}
        filters={{ range: 'all', model: 'all', sort: 'desc' }}
        onClose={vi.fn()}
        onRangeChange={vi.fn()}
        onLoadMore={vi.fn()}
        onSelectRecord={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getAllByText('继续持有，等待突破').length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText('持有')).not.toBeInTheDocument();
  });
});
