/* Exact nano-USD presentation. Input stays a canonical decimal string. */
(function attachCostFormatter(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MiniCodeCost = api;
}(typeof globalThis === 'object' ? globalThis : this, function buildCostFormatter() {
  const CANONICAL_NANO_USD = /^(0|[1-9][0-9]{0,47})$/;
  const NANO_PER_USD = 1000000000n;
  const NANO_PER_CENT = 10000000n;
  const NANO_PER_MICRO_USD = 1000n;

  function groupInteger(value) {
    return value.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function formatNanoUsd(value) {
    if (typeof value !== 'string' || !CANONICAL_NANO_USD.test(value)) return '—';
    const nanoUsd = BigInt(value);
    const whole = nanoUsd / NANO_PER_USD;
    let fraction = String(nanoUsd % NANO_PER_USD).padStart(9, '0');
    const minimumDigits = nanoUsd === 0n ? 6 : (nanoUsd >= NANO_PER_CENT ? 2 : (nanoUsd < NANO_PER_MICRO_USD ? 9 : 6));
    while (fraction.length > minimumDigits && fraction.endsWith('0')) {
      fraction = fraction.slice(0, -1);
    }
    return `$${groupInteger(String(whole))}.${fraction}`;
  }

  return Object.freeze({ formatNanoUsd });
}));
