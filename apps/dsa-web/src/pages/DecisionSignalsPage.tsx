import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, RefreshCw, Search } from 'lucide-react';
import { decisionSignalsApi } from '../api/decisionSignals';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import {
  ApiErrorAlert,
  AppPage,
  Card,
  ConfirmDialog,
  Drawer,
  EmptyState,
  InlineAlert,
  PageHeader,
  Pagination,
} from '../components/common';
import {
  DecisionSignalCard,
  DecisionSignalDetails,
} from '../components/decision-signals/DecisionSignalDisplay';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import type { UiTextKey } from '../i18n/uiText';
import type { DecisionAction, MarketPhaseValue } from '../types/analysis';
import type {
  DecisionSignalItem,
  DecisionSignalListParams,
  DecisionSignalMarket,
  DecisionSignalSourceType,
  DecisionSignalStatus,
} from '../types/decisionSignals';
import { cn } from '../utils/cn';
import { buildDecisionActionLabelMap } from '../utils/decisionAction';

const PAGE_SIZE = 20;

type ListFilters = {
  market: '' | DecisionSignalMarket;
  stockCode: string;
  action: '' | DecisionAction;
  marketPhase: '' | MarketPhaseValue;
  sourceType: '' | DecisionSignalSourceType;
  status: '' | DecisionSignalStatus;
};

type PendingStatusChange = {
  item: DecisionSignalItem;
  status: Extract<DecisionSignalStatus, 'closed' | 'invalidated' | 'archived'>;
  message: string;
};

const MARKET_OPTIONS: DecisionSignalMarket[] = ['cn', 'hk', 'us'];
const ACTION_OPTIONS: DecisionAction[] = ['buy', 'add', 'hold', 'reduce', 'sell', 'watch', 'avoid', 'alert'];
const PHASE_OPTIONS: MarketPhaseValue[] = ['premarket', 'intraday', 'lunch_break', 'closing_auction', 'postmarket', 'non_trading', 'unknown'];
const SOURCE_OPTIONS: DecisionSignalSourceType[] = ['analysis', 'agent', 'alert', 'market_review', 'manual'];
const STATUS_OPTIONS: DecisionSignalStatus[] = ['active', 'expired', 'invalidated', 'closed', 'archived'];

const STATUS_ACTIONS: Array<PendingStatusChange['status']> = ['closed', 'invalidated', 'archived'];

const STATUS_LABEL_KEYS: Record<DecisionSignalStatus, UiTextKey> = {
  active: 'decisionSignals.active',
  expired: 'decisionSignals.expired',
  invalidated: 'decisionSignals.invalidated',
  closed: 'decisionSignals.closed',
  archived: 'decisionSignals.archived',
};

const STATUS_ACTION_LABEL_KEYS: Record<PendingStatusChange['status'], UiTextKey> = {
  closed: 'decisionSignals.close',
  invalidated: 'decisionSignals.invalidate',
  archived: 'decisionSignals.archive',
};

const STATUS_ACTION_CONFIRM_KEYS: Record<PendingStatusChange['status'], UiTextKey> = {
  closed: 'decisionSignals.closeConfirm',
  invalidated: 'decisionSignals.invalidateConfirm',
  archived: 'decisionSignals.archiveConfirm',
};

function toListParams(filters: ListFilters, page: number): DecisionSignalListParams {
  return {
    market: filters.market || undefined,
    stockCode: filters.stockCode.trim() || undefined,
    action: filters.action || undefined,
    marketPhase: filters.marketPhase || undefined,
    sourceType: filters.sourceType || undefined,
    status: filters.status || undefined,
    page,
    pageSize: PAGE_SIZE,
  };
}

const DecisionSignalsPage: React.FC = () => {
  const { t } = useUiLanguage();
  const actionLabels = useMemo(() => buildDecisionActionLabelMap(t), [t]);
  const [filters, setFilters] = useState<ListFilters>({
    market: '',
    stockCode: '',
    action: '',
    marketPhase: '',
    sourceType: '',
    status: 'active',
  });
  const [appliedFilters, setAppliedFilters] = useState<ListFilters>(filters);
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<DecisionSignalItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [selected, setSelected] = useState<DecisionSignalItem | null>(null);
  const [pendingStatus, setPendingStatus] = useState<PendingStatusChange | null>(null);
  const [statusUpdating, setStatusUpdating] = useState(false);
  const [latestStockCode, setLatestStockCode] = useState('');
  const [latestItems, setLatestItems] = useState<DecisionSignalItem[]>([]);
  const [latestSearched, setLatestSearched] = useState(false);
  const [latestLoading, setLatestLoading] = useState(false);
  const [latestError, setLatestError] = useState<ParsedApiError | null>(null);
  const requestIdRef = useRef(0);
  const latestRequestIdRef = useRef(0);

  useEffect(() => {
    document.title = t('decisionSignals.pageTitle');
  }, [t]);

  const loadSignals = useCallback(async () => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    try {
      const response = await decisionSignalsApi.list(toListParams(appliedFilters, page));
      if (requestIdRef.current !== requestId) return;
      setItems(response.items);
      setTotal(response.total);
      setError(null);
      setSelected((current) => {
        if (!current) return current;
        return response.items.find((item) => item.id === current.id) ?? current;
      });
    } catch (err) {
      if (requestIdRef.current !== requestId) return;
      setError(getParsedApiError(err));
    } finally {
      if (requestIdRef.current === requestId) {
        setLoading(false);
      }
    }
  }, [appliedFilters, page]);

  useEffect(() => {
    void loadSignals();
    return () => {
      requestIdRef.current += 1;
    };
  }, [loadSignals]);

  useEffect(() => () => {
    latestRequestIdRef.current += 1;
  }, []);

  const handleApplyFilters = (event: React.FormEvent) => {
    event.preventDefault();
    setAppliedFilters(filters);
    setPage(1);
  };

  const handleLatestSearch = async (event: React.FormEvent) => {
    event.preventDefault();
    const stockCode = latestStockCode.trim();
    if (!stockCode) return;
    const requestId = latestRequestIdRef.current + 1;
    latestRequestIdRef.current = requestId;
    setLatestLoading(true);
    setLatestError(null);
    setLatestSearched(true);
    try {
      const response = await decisionSignalsApi.getLatest(stockCode, {
        market: filters.market || undefined,
        limit: 5,
      });
      if (latestRequestIdRef.current !== requestId) return;
      setLatestItems(response.items);
    } catch (err) {
      if (latestRequestIdRef.current !== requestId) return;
      setLatestItems([]);
      setLatestError(getParsedApiError(err));
    } finally {
      if (latestRequestIdRef.current === requestId) {
        setLatestLoading(false);
      }
    }
  };

  const handleStatusUpdate = async () => {
    if (!pendingStatus) return;
    setStatusUpdating(true);
    try {
      const updated = await decisionSignalsApi.updateStatus(pendingStatus.item.id, {
        status: pendingStatus.status,
      });
      const nextItems = items.flatMap((item) => {
        if (item.id !== updated.id) return [item];
        if (appliedFilters.status && updated.status !== appliedFilters.status) return [];
        return [updated];
      });
      const removedCount = items.length - nextItems.length;
      setItems(nextItems);
      if (removedCount > 0) {
        setTotal((current) => Math.max(0, current - removedCount));
      }
      setLatestItems(latestItems.flatMap((item) => (
        item.id === updated.id
          ? updated.status === 'active' ? [updated] : []
          : [item]
      )));
      setSelected(updated);
      setPendingStatus(null);
      setError(null);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setStatusUpdating(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <AppPage>
      <div className="space-y-5">
        <PageHeader
          eyebrow={t('decisionSignals.activeOnly')}
          title={t('decisionSignals.title')}
          description={t('decisionSignals.description')}
          actions={(
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-2"
              onClick={() => void loadSignals()}
              disabled={loading}
            >
              <RefreshCw className={cn('h-4 w-4', loading ? 'animate-spin' : '')} />
              {t('decisionSignals.refresh')}
            </button>
          )}
        />

        <Card padding="md">
          <form className="grid gap-3 md:grid-cols-3 xl:grid-cols-6" onSubmit={handleApplyFilters}>
            <select
              className="input-surface input-focus-glow h-11 rounded-xl border bg-transparent px-3 text-sm"
              value={filters.market}
              onChange={(event) => setFilters((current) => ({ ...current, market: event.target.value as ListFilters['market'] }))}
              aria-label={t('decisionSignals.market')}
            >
              <option value="">{t('decisionSignals.allMarkets')}</option>
              {MARKET_OPTIONS.map((market) => (
                <option key={market} value={market}>{t(`decisionSignals.market.${market}` as UiTextKey)}</option>
              ))}
            </select>
            <input
              className="input-surface input-focus-glow h-11 rounded-xl border bg-transparent px-3 text-sm"
              value={filters.stockCode}
              onChange={(event) => setFilters((current) => ({ ...current, stockCode: event.target.value }))}
              placeholder={t('decisionSignals.stockCode')}
              aria-label={t('decisionSignals.stockCode')}
            />
            <select
              className="input-surface input-focus-glow h-11 rounded-xl border bg-transparent px-3 text-sm"
              value={filters.action}
              onChange={(event) => setFilters((current) => ({ ...current, action: event.target.value as ListFilters['action'] }))}
              aria-label={t('decisionSignals.action')}
            >
              <option value="">{t('decisionSignals.allActions')}</option>
              {ACTION_OPTIONS.map((action) => (
                <option key={action} value={action}>{actionLabels[action]}</option>
              ))}
            </select>
            <select
              className="input-surface input-focus-glow h-11 rounded-xl border bg-transparent px-3 text-sm"
              value={filters.marketPhase}
              onChange={(event) => setFilters((current) => ({ ...current, marketPhase: event.target.value as ListFilters['marketPhase'] }))}
              aria-label={t('decisionSignals.marketPhase')}
            >
              <option value="">{t('decisionSignals.allPhases')}</option>
              {PHASE_OPTIONS.map((phase) => <option key={phase} value={phase}>{phase}</option>)}
            </select>
            <select
              className="input-surface input-focus-glow h-11 rounded-xl border bg-transparent px-3 text-sm"
              value={filters.sourceType}
              onChange={(event) => setFilters((current) => ({ ...current, sourceType: event.target.value as ListFilters['sourceType'] }))}
              aria-label={t('decisionSignals.source')}
            >
              <option value="">{t('decisionSignals.allSources')}</option>
              {SOURCE_OPTIONS.map((source) => <option key={source} value={source}>{source}</option>)}
            </select>
            <select
              className="input-surface input-focus-glow h-11 rounded-xl border bg-transparent px-3 text-sm"
              value={filters.status}
              onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value as ListFilters['status'] }))}
              aria-label={t('decisionSignals.status')}
            >
              <option value="">{t('decisionSignals.allStatuses')}</option>
              {STATUS_OPTIONS.map((status) => <option key={status} value={status}>{t(STATUS_LABEL_KEYS[status])}</option>)}
            </select>
            <button type="submit" className="btn-primary inline-flex h-11 items-center justify-center gap-2 xl:col-start-6">
              <Search className="h-4 w-4" />
              {t('decisionSignals.filter')}
            </button>
          </form>
        </Card>

        <Card title={t('decisionSignals.latestTitle')} subtitle={t('decisionSignals.latestDescription')} padding="md">
          <form className="flex flex-col gap-3 md:flex-row" onSubmit={handleLatestSearch}>
            <input
              className="input-surface input-focus-glow h-11 flex-1 rounded-xl border bg-transparent px-3 text-sm"
              value={latestStockCode}
              onChange={(event) => setLatestStockCode(event.target.value)}
              placeholder={t('decisionSignals.latestPlaceholder')}
              aria-label={t('decisionSignals.latestInput')}
            />
            <button type="submit" className="btn-secondary inline-flex h-11 items-center justify-center gap-2" disabled={latestLoading || !latestStockCode.trim()}>
              <Search className="h-4 w-4" />
              {t('decisionSignals.latestButton')}
            </button>
          </form>
          {latestError ? <ApiErrorAlert className="mt-3" error={latestError} /> : null}
          {latestSearched && !latestLoading && !latestError && latestItems.length === 0 ? (
            <EmptyState
              className="mt-4 border-none bg-transparent py-6 shadow-none"
              title={t('decisionSignals.noLatestTitle')}
              description={t('decisionSignals.noLatestDescription')}
              icon={<Activity className="h-6 w-6" />}
            />
          ) : null}
          {latestItems.length > 0 ? (
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              {latestItems.map((item) => (
                <DecisionSignalCard key={item.id} item={item} onSelect={setSelected} selected={selected?.id === item.id} />
              ))}
            </div>
          ) : null}
        </Card>

        {error ? (
          <ApiErrorAlert
            error={{ ...error, title: t('decisionSignals.errorTitle') }}
            actionLabel={t('common.retry')}
            onAction={() => void loadSignals()}
          />
        ) : null}

        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-secondary-text">{t('decisionSignals.total', { total })}</p>
          {loading ? <span className="text-xs text-secondary-text">{t('common.loading')}...</span> : null}
        </div>

        {!loading && items.length === 0 ? (
          <EmptyState
            title={t('decisionSignals.emptyTitle')}
            description={t('decisionSignals.emptyDescription')}
            icon={<Activity className="h-7 w-7" />}
          />
        ) : (
          <div className="grid gap-3 xl:grid-cols-2">
            {items.map((item) => (
              <DecisionSignalCard key={item.id} item={item} onSelect={setSelected} selected={selected?.id === item.id} />
            ))}
          </div>
        )}

        <Pagination currentPage={page} totalPages={totalPages} onPageChange={setPage} />
      </div>

      <Drawer
        isOpen={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={t('decisionSignals.detailTitle')}
        width="max-w-3xl"
      >
        {selected ? (
          <DecisionSignalDetails
            item={selected}
            actions={STATUS_ACTIONS.map((status) => (
              <button
                key={status}
                type="button"
                className="btn-secondary !px-3 !py-1.5 !text-xs"
                onClick={() => setPendingStatus({
                  item: selected,
                  status,
                  message: t(STATUS_ACTION_CONFIRM_KEYS[status]),
                })}
                disabled={statusUpdating || selected.status === status}
              >
                {t(STATUS_ACTION_LABEL_KEYS[status])}
              </button>
            ))}
          />
        ) : null}
      </Drawer>

      {statusUpdating ? (
        <InlineAlert
          className="fixed bottom-5 right-5 z-[60] max-w-sm"
          variant="info"
          title={t('common.processing')}
          message={t('decisionSignals.confirmStatusTitle')}
        />
      ) : null}

      <ConfirmDialog
        isOpen={Boolean(pendingStatus)}
        title={t('decisionSignals.confirmStatusTitle')}
        message={pendingStatus?.message ?? ''}
        confirmText={t('common.confirm')}
        onConfirm={() => void handleStatusUpdate()}
        onCancel={() => setPendingStatus(null)}
      />
    </AppPage>
  );
};

export default DecisionSignalsPage;
