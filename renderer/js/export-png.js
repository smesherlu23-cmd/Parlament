/**
 * Экспорт схемы в PNG.
 *
 * Рисуем на canvas, а не переводим SVG в картинку: во-первых, размер задаётся
 * точно (1920×1080 и выше — требование ТЗ), во-вторых, шрифт, уже загруженный
 * документом, доступен canvas напрямую, тогда как SVG внутри <img> его
 * подтягивать не станет и текст поехал бы на системный.
 */

import { drawSeatChartOnCanvas, CHART_VIEWBOX } from './seat-chart.js';

const BACKGROUND = '#f3f2f2';
const INK = '#201e1d';
const MUTED = '#605d5d';
const SERIF = '"Source Serif 4", Georgia, serif';

/**
 * Собирает картинку.
 *
 * @param {object} options
 * @param {number} options.width пиксели итогового файла
 * @param {number} options.height
 * @param {number} options.totalSeats
 * @param {number} options.rows
 * @param {Array<{name: string, abbr: string, color: string, seats: number}>} options.distribution
 * @param {string} options.title название созыва
 * @param {boolean} options.withLegend
 * @param {boolean} options.withTitle
 * @returns {Promise<string>} data-URL готового PNG
 */
export async function renderChartToPng(options) {
  const { width, height } = options;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;

  const ctx = canvas.getContext('2d');
  ctx.fillStyle = BACKGROUND;
  ctx.fillRect(0, 0, width, height);

  // Шрифт мог ещё не догрузиться — тогда подписи ушли бы системным.
  if (document.fonts && document.fonts.ready) await document.fonts.ready;

  // Всё построено от высоты: так композиция одинакова на 1080p и на 4K.
  const unit = height / 1080;
  const margin = Math.round(72 * unit);
  let cursorY = margin;

  if (options.withTitle && options.title) {
    ctx.fillStyle = INK;
    ctx.font = `600 ${Math.round(52 * unit)}px ${SERIF}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText(options.title, width / 2, cursorY);
    cursorY += Math.round(78 * unit);
  }

  const legendHeight = options.withLegend
    ? measureLegendHeight(ctx, options, width - margin * 2, unit)
    : 0;

  // Схема занимает всё, что осталось между заголовком и легендой, но не шире
  // полосы полей — иначе на 21:9 полукруг растянулся бы на весь экран.
  const availableHeight = height - cursorY - margin - legendHeight;
  const availableWidth = width - margin * 2;
  const chartWidth = Math.min(availableWidth,
    (availableHeight * CHART_VIEWBOX.width) / CHART_VIEWBOX.height);
  const chartHeight = (chartWidth * CHART_VIEWBOX.height) / CHART_VIEWBOX.width;

  drawSeatChartOnCanvas(ctx, {
    x: (width - chartWidth) / 2,
    y: cursorY + (availableHeight - chartHeight) / 2,
    width: chartWidth,
    total: options.totalSeats,
    rows: options.rows,
    dist: options.distribution,
  });

  if (options.withLegend) {
    drawLegend(ctx, options, {
      x: margin,
      y: height - margin - legendHeight,
      width: availableWidth,
      unit,
    });
  }

  return canvas.toDataURL('image/png');
}

/** Раскладывает легенду по строкам и отдаёт позиции — общая для замера и отрисовки. */
function layoutLegend(ctx, options, maxWidth, unit) {
  const fontSize = Math.round(26 * unit);
  const swatchSize = Math.round(20 * unit);
  const gap = Math.round(12 * unit);
  const columnGap = Math.round(48 * unit);
  const lineHeight = Math.round(44 * unit);

  ctx.font = `400 ${fontSize}px ${SERIF}`;

  const items = options.distribution.map((party) => {
    const label = `${party.name} ${party.seats}`;
    const percent = ` (${((party.seats / options.totalSeats) * 100).toFixed(1).replace('.', ',')} %)`;
    const width = swatchSize + gap + ctx.measureText(label + percent).width;
    return { party, label, percent, width };
  });

  const lines = [[]];
  let lineWidth = 0;
  for (const item of items) {
    const needed = (lines.at(-1).length ? columnGap : 0) + item.width;
    if (lineWidth + needed > maxWidth && lines.at(-1).length) {
      lines.push([item]);
      lineWidth = item.width;
    } else {
      lines.at(-1).push(item);
      lineWidth += needed;
    }
  }

  return { lines, fontSize, swatchSize, gap, columnGap, lineHeight };
}

function measureLegendHeight(ctx, options, maxWidth, unit) {
  if (!options.distribution.length) return 0;
  const { lines, lineHeight } = layoutLegend(ctx, options, maxWidth, unit);
  return lines.length * lineHeight;
}

function drawLegend(ctx, options, { x, y, width, unit }) {
  if (!options.distribution.length) return;

  const { lines, fontSize, swatchSize, gap, columnGap, lineHeight } =
    layoutLegend(ctx, options, width, unit);

  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';

  lines.forEach((line, lineIndex) => {
    const lineWidth = line.reduce((sum, item, index) =>
      sum + item.width + (index ? columnGap : 0), 0);
    let cursorX = x + (width - lineWidth) / 2;   // строки легенды по центру
    const centerY = y + lineIndex * lineHeight + lineHeight / 2;

    for (const item of line) {
      ctx.fillStyle = item.party.color;
      ctx.fillRect(cursorX, centerY - swatchSize / 2, swatchSize, swatchSize);
      cursorX += swatchSize + gap;

      ctx.font = `400 ${fontSize}px ${SERIF}`;
      ctx.fillStyle = INK;
      ctx.fillText(item.label, cursorX, centerY);
      cursorX += ctx.measureText(item.label).width;

      ctx.fillStyle = MUTED;
      ctx.fillText(item.percent, cursorX, centerY);
      cursorX += ctx.measureText(item.percent).width + columnGap;
    }
  });
}
