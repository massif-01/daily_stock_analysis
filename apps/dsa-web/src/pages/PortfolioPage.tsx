import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Pie, PieChart, ResponsiveContainer, Tooltip, Legend, Cell } from 'recharts';
import { portfolioApi } from '../api/portfolio';
import type { ParsedApiError } from '../api/error';
import { getParsedApiError } from '../api/error';
import { ApiErrorAlert, Card, Badge } from '../components/common';
import type {
  PortfolioAccountItem,
  PortfolioCostMethod,
  PortfolioPositionItem,
  PortfolioRiskResponse,
  PortfolioSnapshotResponse,
} from '../types/portfolio';

const PIE_COLORS = ['#00d4ff', '#00ff88', '#ffaa00', '#ff7a45', '#7f8cff', '#ff4466'];

type AccountOption = 'all' | number;

type FlatPosition = PortfolioPositionItem & {
  accountId: number;
  accountName: string;
};

function formatMoney(value: number | undefined | null, currency = 'CNY'): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${currency} ${Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatPct(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${value.toFixed(2)}%`;
}

const PortfolioPage: React.FC = () => {
  const [accounts, setAccounts] = useState<PortfolioAccountItem[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<AccountOption>('all');
  const [costMethod, setCostMethod] = useState<PortfolioCostMethod>('fifo');
  const [snapshot, setSnapshot] = useState<PortfolioSnapshotResponse | null>(null);
  const [risk, setRisk] = useState<PortfolioRiskResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [riskWarning, setRiskWarning] = useState<string | null>(null);

  const queryAccountId = selectedAccount === 'all' ? undefined : selectedAccount;

  const loadAccounts = useCallback(async () => {
    try {
      const response = await portfolioApi.getAccounts(false);
      setAccounts(response.accounts || []);
    } catch (err) {
      setError(getParsedApiError(err));
    }
  }, []);

  const loadSnapshotAndRisk = useCallback(async () => {
    setIsLoading(true);
    setRiskWarning(null);
    try {
      const snapshotData = await portfolioApi.getSnapshot({
        accountId: queryAccountId,
        costMethod,
      });
      setSnapshot(snapshotData);
      setError(null);

      try {
        const riskData = await portfolioApi.getRisk({
          accountId: queryAccountId,
          costMethod,
        });
        setRisk(riskData);
      } catch (riskErr) {
        setRisk(null);
        const parsed = getParsedApiError(riskErr);
        setRiskWarning(parsed.message || '风险数据获取失败，已降级为仅展示快照数据。');
      }
    } catch (err) {
      setSnapshot(null);
      setRisk(null);
      setError(getParsedApiError(err));
    } finally {
      setIsLoading(false);
    }
  }, [queryAccountId, costMethod]);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    void loadSnapshotAndRisk();
  }, [loadSnapshotAndRisk]);

  const positionRows: FlatPosition[] = useMemo(() => {
    if (!snapshot) return [];
    const rows: FlatPosition[] = [];
    for (const account of snapshot.accounts || []) {
      for (const position of account.positions || []) {
        rows.push({
          ...position,
          accountId: account.accountId,
          accountName: account.accountName,
        });
      }
    }
    rows.sort((a, b) => Number(b.marketValueBase || 0) - Number(a.marketValueBase || 0));
    return rows;
  }, [snapshot]);

  const concentrationPieData = useMemo(() => {
    if (!risk?.concentration?.topPositions?.length) {
      return [];
    }
    return risk.concentration.topPositions
      .slice(0, 6)
      .map((item) => ({
        name: item.symbol,
        value: Number(item.weightPct || 0),
      }))
      .filter((item) => item.value > 0);
  }, [risk]);

  return (
    <div className="min-h-screen p-4 md:p-6 space-y-4">
      <section className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-xl md:text-2xl font-semibold text-white">持仓管理</h1>
          <p className="text-xs md:text-sm text-secondary mt-1">
            组合快照与风险视图（支持全组合 / 单账户切换）
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={String(selectedAccount)}
            onChange={(e) => setSelectedAccount(e.target.value === 'all' ? 'all' : Number(e.target.value))}
            className="input-terminal text-sm min-w-44"
          >
            <option value="all">全部账户</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name} (#{account.id})
              </option>
            ))}
          </select>
          <select
            value={costMethod}
            onChange={(e) => setCostMethod(e.target.value as PortfolioCostMethod)}
            className="input-terminal text-sm min-w-28"
          >
            <option value="fifo">FIFO</option>
            <option value="avg">AVG</option>
          </select>
          <button
            type="button"
            onClick={() => void loadSnapshotAndRisk()}
            disabled={isLoading}
            className="btn-secondary whitespace-nowrap"
          >
            {isLoading ? '刷新中...' : '刷新'}
          </button>
        </div>
      </section>

      {error ? <ApiErrorAlert error={error} /> : null}
      {riskWarning ? (
        <div className="rounded-xl border border-amber-500/35 bg-amber-500/10 px-4 py-3 text-amber-100 text-sm">
          风险模块降级：{riskWarning}
        </div>
      ) : null}

      <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <Card variant="gradient" padding="md">
          <p className="text-xs text-secondary">总权益</p>
          <p className="mt-1 text-xl font-semibold text-white">
            {formatMoney(snapshot?.totalEquity, snapshot?.currency || 'CNY')}
          </p>
        </Card>
        <Card variant="gradient" padding="md">
          <p className="text-xs text-secondary">总市值</p>
          <p className="mt-1 text-xl font-semibold text-white">
            {formatMoney(snapshot?.totalMarketValue, snapshot?.currency || 'CNY')}
          </p>
        </Card>
        <Card variant="gradient" padding="md">
          <p className="text-xs text-secondary">总现金</p>
          <p className="mt-1 text-xl font-semibold text-white">
            {formatMoney(snapshot?.totalCash, snapshot?.currency || 'CNY')}
          </p>
        </Card>
        <Card variant="gradient" padding="md">
          <p className="text-xs text-secondary">汇率状态</p>
          <div className="mt-2">
            {snapshot?.fxStale ? (
              <Badge variant="warning">stale</Badge>
            ) : (
              <Badge variant="success">fresh</Badge>
            )}
          </div>
        </Card>
      </section>

      <section className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <Card className="xl:col-span-2" padding="md">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-white">持仓明细</h2>
            <span className="text-xs text-secondary">共 {positionRows.length} 项</span>
          </div>
          {positionRows.length === 0 ? (
            <p className="text-sm text-muted py-6 text-center">当前无持仓数据</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs text-secondary border-b border-white/10">
                  <tr>
                    <th className="text-left py-2 pr-2">账户</th>
                    <th className="text-left py-2 pr-2">代码</th>
                    <th className="text-right py-2 pr-2">数量</th>
                    <th className="text-right py-2 pr-2">均价</th>
                    <th className="text-right py-2 pr-2">现价</th>
                    <th className="text-right py-2 pr-2">市值</th>
                    <th className="text-right py-2">未实现盈亏</th>
                  </tr>
                </thead>
                <tbody>
                  {positionRows.map((row) => (
                    <tr key={`${row.accountId}-${row.symbol}-${row.market}`} className="border-b border-white/5">
                      <td className="py-2 pr-2 text-secondary">{row.accountName}</td>
                      <td className="py-2 pr-2 font-mono text-white">{row.symbol}</td>
                      <td className="py-2 pr-2 text-right">{row.quantity.toFixed(2)}</td>
                      <td className="py-2 pr-2 text-right">{row.avgCost.toFixed(4)}</td>
                      <td className="py-2 pr-2 text-right">{row.lastPrice.toFixed(4)}</td>
                      <td className="py-2 pr-2 text-right">{formatMoney(row.marketValueBase, row.valuationCurrency)}</td>
                      <td
                        className={`py-2 text-right ${
                          row.unrealizedPnlBase >= 0 ? 'text-emerald-400' : 'text-red-400'
                        }`}
                      >
                        {formatMoney(row.unrealizedPnlBase, row.valuationCurrency)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card padding="md">
          <h2 className="text-sm font-semibold text-white mb-3">集中度饼图（Top Positions）</h2>
          {concentrationPieData.length > 0 ? (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={concentrationPieData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    outerRadius={90}
                  >
                    {concentrationPieData.map((entry, index) => (
                      <Cell key={`cell-${entry.name}`} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value) => `${Number(value).toFixed(2)}%`} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="text-sm text-muted py-8 text-center">暂无集中度数据</p>
          )}
          <div className="mt-3 text-xs text-secondary space-y-1">
            <div>头寸集中度告警: {risk?.concentration?.alert ? '是' : '否'}</div>
            <div>Top1 权重: {formatPct(risk?.concentration?.topWeightPct)}</div>
          </div>
        </Card>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Card padding="md">
          <h3 className="text-sm font-semibold text-white mb-2">回撤监控</h3>
          <div className="text-xs text-secondary space-y-1">
            <div>最大回撤: {formatPct(risk?.drawdown?.maxDrawdownPct)}</div>
            <div>当前回撤: {formatPct(risk?.drawdown?.currentDrawdownPct)}</div>
            <div>告警: {risk?.drawdown?.alert ? '是' : '否'}</div>
          </div>
        </Card>
        <Card padding="md">
          <h3 className="text-sm font-semibold text-white mb-2">止损接近预警</h3>
          <div className="text-xs text-secondary space-y-1">
            <div>触发数: {risk?.stopLoss?.triggeredCount ?? 0}</div>
            <div>接近数: {risk?.stopLoss?.nearCount ?? 0}</div>
            <div>告警: {risk?.stopLoss?.nearAlert ? '是' : '否'}</div>
          </div>
        </Card>
        <Card padding="md">
          <h3 className="text-sm font-semibold text-white mb-2">口径</h3>
          <div className="text-xs text-secondary space-y-1">
            <div>账户数: {snapshot?.accountCount ?? 0}</div>
            <div>计价币种: {snapshot?.currency || 'CNY'}</div>
            <div>成本法: {(snapshot?.costMethod || costMethod).toUpperCase()}</div>
          </div>
        </Card>
      </section>
    </div>
  );
};

export default PortfolioPage;
