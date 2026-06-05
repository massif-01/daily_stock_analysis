import { validateStockCode } from './validation';
import { normalizeStockCode } from './stockCode';

const EXCHANGE_PREFIXES = new Set(['SH', 'SZ', 'BJ', 'HK', 'US', 'SS']);
const STOCK_CODE_PATTERNS = [
  /\b(30\d{4}\.SZ)\b/gi,
  /\b(68\d{4}\.SH)\b/gi,
  /\b(00\d{4}\.SZ)\b/gi,
  /\b(60\d{4}\.SH)\b/gi,
  /\b(SH\d{6})\b/gi,
  /\b(SZ\d{6})\b/gi,
  /\b(BJ\d{6})\b/gi,
  /\b(hk\d{4,5})\b/gi,
  /\b(\d{1,5}\.HK)\b/gi,
  /\b(\d{5,6})\b/g,
  /\b([A-Z]{2,5}(?:\.(?:US|[A-Z]))?)\b/g,
];
const EXPLICIT_STOCK_SWITCH_INTENT = /换成|切换到?|改成|改为/i;
const EXPLICIT_STOCK_SCOPE_INTENT = /换成|切换到?|改成|改为|分析|研究|看看|看一下|查一?下|查询|诊断|怎么看|怎么样|如何|走势|趋势/i;
const COMPARE_INTENT = /比较|对比|相比|差异|差别|区别|不同|优劣|哪个更|谁更|\bvs\b/i;
const CURRENT_STOCK_REFERENCE_PATTERN = /当前股票|当前标的|这只股票|这支股票|该股/i;
const COMPARE_CONNECTOR = /和|跟|与|\bvs\b/i;

// Mirrors backend _COMMON_WORDS for #1596 free-text extraction only.
// Explicit validation via validateStockCode() intentionally keeps its original contract.
const FREE_TEXT_TICKER_DENYLIST = new Set([
  'AM', 'AS', 'AT', 'BE', 'BY', 'DO', 'GO', 'HE', 'IF', 'IN',
  'IS', 'IT', 'ME', 'MY', 'NO', 'OF', 'ON', 'OR', 'SO', 'TO',
  'UP', 'US', 'WE',
  'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL',
  'CAN', 'HAD', 'HER', 'WAS', 'ONE', 'OUR', 'OUT', 'HAS',
  'HIS', 'HOW', 'ITS', 'LET', 'MAY', 'NEW', 'NOW', 'OLD',
  'SEE', 'WAY', 'WHO', 'DID', 'GET', 'HIM', 'USE', 'SAY',
  'SHE', 'TOO', 'ANY', 'WITH', 'FROM', 'THAT', 'THAN',
  'THIS', 'WHAT', 'WHEN', 'WILL', 'JUST', 'ALSO',
  'BEEN', 'EACH', 'HAVE', 'MUCH', 'ONLY', 'OVER',
  'SOME', 'SUCH', 'THEM', 'THEN', 'THEY', 'VERY',
  'WERE', 'YOUR', 'ABOUT', 'AFTER', 'COULD', 'EVERY',
  'OTHER', 'THEIR', 'THERE', 'THESE', 'THOSE', 'WHICH',
  'WOULD', 'BEING', 'STILL', 'WHERE',
  'BUY', 'SELL', 'HOLD', 'LONG', 'PUT', 'CALL',
  'ETF', 'IPO', 'RSI', 'EPS', 'PEG', 'ROE', 'ROA',
  'USA', 'USD', 'CNY', 'HKD', 'EUR', 'GBP',
  'STOCK', 'TRADE', 'PRICE', 'INDEX', 'FUND',
  'HIGH', 'LOW', 'OPEN', 'CLOSE', 'STOP', 'LOSS',
  'TREND', 'BULL', 'BEAR', 'RISK', 'CASH', 'BOND',
  'MACD', 'VWAP', 'BOLL',
  'TTM', 'LTM', 'NTM', 'FWD', 'YOY', 'QOQ', 'YTD',
  'EBIT', 'EBITDA', 'DCF', 'CAGR', 'FCF', 'NAV', 'AUM',
  'PE', 'PB',
  'HELLO', 'PLEASE', 'THANKS', 'CHECK', 'LOOK', 'THINK',
  'MAYBE', 'GUESS', 'TELL', 'SHOW', 'WHATS',
  'WHY', 'HOWDY', 'HEY', 'HI',
]);

export function isDeniedTickerCandidate(value: string): boolean {
  return FREE_TEXT_TICKER_DENYLIST.has(value.trim().toUpperCase());
}

type StockCodeCandidate = {
  raw: string;
  code: string;
  start: number;
  end: number;
};

function findStockCodeCandidates(message: string): StockCodeCandidate[] {
  const candidates: StockCodeCandidate[] = [];
  const seen = new Set<string>();

  // More specific patterns first to avoid greedy \d{6} capturing inside .SH/.SZ codes.
  for (const pattern of STOCK_CODE_PATTERNS) {
    pattern.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(message)) !== null) {
      const raw = match[1] ?? match[0];
      if (EXCHANGE_PREFIXES.has(raw.toUpperCase())) {
        continue;
      }
      if (isDeniedTickerCandidate(raw)) {
        continue;
      }
      const { valid, normalized } = validateStockCode(raw);
      if (!valid) {
        continue;
      }
      const code = normalizeStockCode(normalized);
      if (seen.has(code)) {
        continue;
      }
      seen.add(code);
      const start = match.index + match[0].indexOf(raw);
      candidates.push({ raw, code, start, end: start + raw.length });
    }
  }
  return candidates;
}

export function extractStockCodeFromMessage(message: string): string | null {
  return findStockCodeCandidates(message)[0]?.code ?? null;
}

export function extractStockCodeForScopeSwitch(message: string): string | null {
  const candidates = findStockCodeCandidates(message);
  if (!candidates.length) {
    return extractLowercaseSwitchTicker(message);
  }
  const stripped = message.trim().toUpperCase();
  const bareCandidate = candidates.find((candidate) =>
    stripped === candidate.raw.toUpperCase() || stripped === candidate.code.toUpperCase()
  );
  if (bareCandidate) {
    return bareCandidate.code;
  }

  return candidates.find((candidate) => isCandidateInExplicitScope(message, candidate))?.code
    ?? extractLowercaseSwitchTicker(message);
}

function isCandidateInExplicitScope(message: string, candidate: StockCodeCandidate): boolean {
  if (hasNegatedStockScope(message, candidate.start, candidate.end)) {
    return false;
  }
  const windowText = message.slice(Math.max(0, candidate.start - 10), Math.min(message.length, candidate.end + 10));
  if (EXPLICIT_STOCK_SWITCH_INTENT.test(windowText)) {
    return true;
  }
  if (hasComparisonIntent(message)) {
    return false;
  }
  return EXPLICIT_STOCK_SCOPE_INTENT.test(windowText);
}

function hasComparisonIntent(message: string): boolean {
  return COMPARE_INTENT.test(message)
    || (CURRENT_STOCK_REFERENCE_PATTERN.test(message) && COMPARE_CONNECTOR.test(message));
}

function extractLowercaseSwitchTicker(message: string): string | null {
  const switchMatch = message.match(/(?:换成|切换到?|改成|改为)\s*([a-z]{2,4}(?:\.(?:us|[a-z]))?)(?![a-z])/);
  if (!switchMatch || switchMatch.index === undefined) {
    return null;
  }
  const raw = switchMatch[1];
  if (!/[a-z]/.test(raw) || isDeniedTickerCandidate(raw)) {
    return null;
  }
  const { valid, normalized } = validateStockCode(raw);
  if (!valid) {
    return null;
  }
  const start = switchMatch.index + switchMatch[0].lastIndexOf(raw);
  if (hasNegatedStockScope(message, start, start + raw.length)) {
    return null;
  }
  return normalizeStockCode(normalized);
}

export function hasNegatedStockScope(message: string, start: number, end: number): boolean {
  const left = message.slice(Math.max(0, start - 12), start);
  const right = message.slice(end, Math.min(message.length, end + 8));
  if (/(不要|别|无需|不用|不必|别再|排除|避免|忽略|不参考|不要参考)\s*.{0,8}$/.test(left)) {
    return true;
  }
  if (/(not|without|exclude|ignore)\s*.{0,16}$/i.test(left)) {
    return true;
  }
  if (/^\s*(不用|不必|不要|别|排除|忽略)/.test(right)) {
    return true;
  }
  return false;
}
