export function squarify(items, x0, y0, x1, y1, weightKey = 'weight') {
  const totalArea   = (x1 - x0) * (y1 - y0);
  const totalWeight = items.reduce((s, d) => s + d[weightKey], 0);

  const nodes = items.slice()
    .sort((a, b) => b[weightKey] - a[weightKey])
    .map(d => ({ ...d, _a: (d[weightKey] / totalWeight) * totalArea }));

  const rects = [];

  function worst(row, stripLen) {
    if (!row.length) return Infinity;
    const s    = row.reduce((acc, d) => acc + d._a, 0);
    const rMax = Math.max(...row.map(d => d._a));
    const rMin = Math.min(...row.map(d => d._a));
    return Math.max(stripLen * stripLen * rMax / (s * s), s * s / (stripLen * stripLen * rMin));
  }

  function layout(nodes, x0, y0, x1, y1) {
    if (!nodes.length) return;
    const w = x1 - x0, h = y1 - y0;
    if (w <= 0 || h <= 0) return;
    if (nodes.length === 1) {
      rects.push({ data: nodes[0], x: x0, y: y0, w: Math.max(w, 0), h: Math.max(h, 0) });
      return;
    }

    const isWide   = w >= h;
    const stripLen = isWide ? w : h;

    let row = [], i = 0;
    while (i < nodes.length) {
      const cand = [...row, nodes[i]];
      if (!row.length || worst(cand, stripLen) <= worst(row, stripLen)) { row.push(nodes[i]); i++; }
      else break;
    }

    const rowArea = row.reduce((s, d) => s + d._a, 0);
    const thick   = rowArea / stripLen;
    let   offset  = 0;

    row.forEach(d => {
      const len = (d._a / rowArea) * stripLen;
      if (isWide) rects.push({ data: d, x: x0 + offset, y: y0,          w: len,   h: thick });
      else        rects.push({ data: d, x: x0,           y: y0 + offset, w: thick, h: len  });
      offset += len;
    });

    const rem = nodes.slice(row.length);
    if (rem.length) {
      // Clamp to prevent float accumulation from creating negative remaining space
      if (isWide) layout(rem, x0, Math.min(y0 + thick, y1), x1, y1);
      else        layout(rem, Math.min(x0 + thick, x1), y0, x1, y1);
    }
  }

  layout(nodes, x0, y0, x1, y1);
  return rects;
}
