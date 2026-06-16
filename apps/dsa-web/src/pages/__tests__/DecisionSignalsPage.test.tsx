import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { decisionSignalsApi } from '../../api/decisionSignals';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';
import type { DecisionSignalItem, DecisionSignalListResponse } from '../../types/decisionSignals';
import DecisionSignalsPage from '../DecisionSignalsPage';

vi.mock('../../api/decisionSignals', () => ({
  decisionSignalsApi: {
    list: vi.fn(),
    getLatest: vi.fn(),
    updateStatus: vi.fn(),
  },
}));

const signal: DecisionSignalItem = {
  id: 7,
  stockCode: '600519',
  stockName: '贵州茅台',
  market: 'cn',
  sourceType: 'analysis',
  sourceReportId: 3001,
  marketPhase: 'intraday',
  triggerSource: 'web',
  action: 'hold',
  actionLabel: null,
  confidence: 0.72,
  score: 82,
  horizon: '3d',
  entryLow: 1600,
  entryHigh: 1620,
  stopLoss: 1550,
  targetPrice: 1700,
  invalidation: '跌破 1550',
  watchConditions: '观察成交量',
  reason: '趋势保持',
  riskSummary: '放量下跌风险',
  catalystSummary: '业绩窗口',
  evidence: { technical: 'ma' },
  dataQualitySummary: { freshness: 'ok' },
  planQuality: 'complete',
  status: 'active',
  expiresAt: '2026-06-18T09:30:00Z',
  createdAt: '2026-06-17T09:30:00Z',
  updatedAt: '2026-06-17T09:30:00Z',
  metadata: { source: 'test' },
};

function listResponse(items: DecisionSignalItem[] = [signal], total = items.length): DecisionSignalListResponse {
  return {
    items,
    total,
    page: 1,
    pageSize: 20,
  };
}

function renderPage() {
  return render(
    <UiLanguageProvider>
      <DecisionSignalsPage />
    </UiLanguageProvider>,
  );
}

function deferredPromise<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem('dsa.uiLanguage', 'zh');
  vi.clearAllMocks();
  vi.mocked(decisionSignalsApi.list).mockResolvedValue(listResponse());
  vi.mocked(decisionSignalsApi.getLatest).mockResolvedValue(listResponse([signal]));
  vi.mocked(decisionSignalsApi.updateStatus).mockResolvedValue({ ...signal, status: 'invalidated' });
});

describe('DecisionSignalsPage', () => {
  it('loads active signals by default', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'AI 建议' })).toBeInTheDocument();
    await waitFor(() => {
      expect(decisionSignalsApi.list).toHaveBeenCalledWith(expect.objectContaining({
        status: 'active',
        page: 1,
        pageSize: 20,
      }));
    });
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('放量下跌风险')).toBeInTheDocument();
  });

  it('passes filter parameters when applying filters', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    fireEvent.change(screen.getByLabelText('市场'), { target: { value: 'cn' } });
    fireEvent.change(screen.getByLabelText('股票代码'), { target: { value: '600519' } });
    fireEvent.change(screen.getByLabelText('动作'), { target: { value: 'hold' } });
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));

    await waitFor(() => {
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
        market: 'cn',
        stockCode: '600519',
        action: 'hold',
        status: 'active',
        page: 1,
        pageSize: 20,
      }));
    });
  });

  it('queries latest active signals by stock code', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    fireEvent.change(screen.getByLabelText('最新股票代码'), {
      target: { value: '600519' },
    });
    fireEvent.click(screen.getByRole('button', { name: '查询最新' }));

    await waitFor(() => {
      expect(decisionSignalsApi.getLatest).toHaveBeenCalledWith('600519', {
        market: undefined,
        limit: 5,
      });
    });
  });

  it('ignores stale latest-search responses', async () => {
    const firstSearch = deferredPromise<DecisionSignalListResponse>();
    const secondSignal = {
      ...signal,
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us' as const,
      riskSummary: '第二次查询结果',
    };
    vi.mocked(decisionSignalsApi.getLatest)
      .mockReturnValueOnce(firstSearch.promise)
      .mockResolvedValueOnce(listResponse([secondSignal]));
    renderPage();
    await screen.findByText('贵州茅台');

    const latestInput = screen.getByLabelText('最新股票代码');
    fireEvent.change(latestInput, {
      target: { value: '600519' },
    });
    fireEvent.click(screen.getByRole('button', { name: '查询最新' }));

    fireEvent.change(latestInput, {
      target: { value: 'AAPL' },
    });
    fireEvent.submit(latestInput.closest('form') as HTMLFormElement);

    expect(await screen.findByText('第二次查询结果')).toBeInTheDocument();

    await act(async () => {
      firstSearch.resolve(listResponse([{ ...signal, riskSummary: '第一次晚返回结果' }]));
      await firstSearch.promise;
    });

    await waitFor(() => {
      expect(screen.queryByText('第一次晚返回结果')).not.toBeInTheDocument();
    });
    expect(screen.getByText('第二次查询结果')).toBeInTheDocument();
  });

  it('renders latest empty and error states', async () => {
    vi.mocked(decisionSignalsApi.getLatest).mockResolvedValueOnce(listResponse([], 0));
    renderPage();
    await screen.findByText('贵州茅台');

    fireEvent.change(screen.getByLabelText('最新股票代码'), {
      target: { value: '600519' },
    });
    fireEvent.click(screen.getByRole('button', { name: '查询最新' }));

    expect(await screen.findByText('暂无最新有效信号')).toBeInTheDocument();

    vi.mocked(decisionSignalsApi.getLatest).mockRejectedValueOnce(new Error('latest down'));
    fireEvent.click(screen.getByRole('button', { name: '查询最新' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('latest down');
  });

  it('renders empty and error states', async () => {
    vi.mocked(decisionSignalsApi.list).mockResolvedValueOnce(listResponse([], 0));

    renderPage();

    expect(await screen.findByText('暂无决策信号')).toBeInTheDocument();
    vi.mocked(decisionSignalsApi.list).mockRejectedValueOnce(new Error('boom'));
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('boom');
  });

  it('opens details and confirms terminal status updates', async () => {
    renderPage();

    fireEvent.click(await screen.findByText('贵州茅台'));
    const dialog = await screen.findByRole('dialog');
    expect(screen.getAllByText('贵州茅台')).toHaveLength(2);
    expect(within(dialog).getByText('趋势保持')).toBeInTheDocument();
    expect(within(dialog).getByText('#3001')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: '标记失效' }));
    expect(await screen.findByRole('heading', { name: '更新信号状态' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确定' }));

    await waitFor(() => {
      expect(decisionSignalsApi.updateStatus).toHaveBeenCalledWith(7, { status: 'invalidated' });
    });
    expect(await within(dialog).findByText('已失效')).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText('贵州茅台')).toHaveLength(1));
    expect(screen.getByText('共 0 条信号')).toBeInTheDocument();
    expect(screen.getByText('暂无决策信号')).toBeInTheDocument();
  });

  it.each([
    ['关闭信号', 'closed'],
    ['归档', 'archived'],
  ] as const)('confirms %s without exposing active recovery', async (buttonName, status) => {
    vi.mocked(decisionSignalsApi.updateStatus).mockResolvedValueOnce({ ...signal, status });
    renderPage();

    fireEvent.click(await screen.findByText('贵州茅台'));
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByRole('button', { name: '关闭信号' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '标记失效' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '归档' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: '有效' })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: '已过期' })).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: buttonName }));
    fireEvent.click(await screen.findByRole('button', { name: '确定' }));

    await waitFor(() => {
      expect(decisionSignalsApi.updateStatus).toHaveBeenCalledWith(7, { status });
    });
  });
});
