/**
 * Полукруглая схема парламента.
 *
 * Геометрия повторяет компонент SeatChart из макета, чтобы приложение
 * выглядело один в один с дизайном:
 *
 *  - места лежат на `rows` концентрических дугах между внутренним и внешним
 *    радиусом, радиусы распределены линейно;
 *  - мест в ряду — пропорционально его радиусу (то есть длине дуги), остаток
 *    от округления достаётся рядам с наибольшей дробной частью;
 *  - внутри ряда места стоят через равный угол; затем все места сортируются
 *    по углу (слева направо) и последовательно красятся цветами партий.
 *
 * Расчёт отделён от отрисовки: экран рисует SVG, экспорт — canvas, но
 * геометрия у них одна и та же.
 */

/** Система координат макета; всё остальное — масштабирование этой сетки. */
export const CHART_VIEWBOX = { width: 630, height: 330 };

const CENTER_X = 315;
const CENTER_Y = 315;
const INNER_RADIUS = 128;
const OUTER_RADIUS = 300;

export const EMPTY_SEAT_COLOR = '#d7d3d3';
const SEAT_STROKE = '#f3f2f2';
const SEAT_STROKE_WIDTH = 1.2;

/**
 * Считает координаты всех мест.
 *
 * @param {object} options
 * @param {number} options.total общее число мест (120 по умолчанию)
 * @param {number} options.rows число рядов
 * @param {Array<{color: string, seats: number}>} options.dist доли партий по порядку
 * @param {string} [options.emptyColor] цвет нераспределённых мест
 * @returns {Array<{x: number, y: number, r: number, color: string}>}
 */
export function computeSeats({ total = 120, rows = 5, dist = [], emptyColor = EMPTY_SEAT_COLOR }) {
  const seatCount = Math.max(1, Math.round(total));
  const rowCount = Math.max(2, Math.round(rows));

  // Радиус каждого ряда — линейно от внутреннего к внешнему.
  const radii = [];
  for (let i = 0; i < rowCount; i += 1) {
    radii.push(INNER_RADIUS + ((OUTER_RADIUS - INNER_RADIUS) * i) / (rowCount - 1));
  }

  // Мест в ряду — пропорционально радиусу; остаток раздаём по наибольшей
  // дробной части, чтобы в сумме получилось ровно `total`.
  const radiiSum = radii.reduce((a, b) => a + b, 0);
  const exact = radii.map((r) => (seatCount * r) / radiiSum);
  const counts = exact.map((v) => Math.floor(v));
  const shortfall = seatCount - counts.reduce((a, b) => a + b, 0);
  const byRemainder = exact
    .map((v, i) => ({ remainder: v - Math.floor(v), index: i }))
    .sort((a, b) => b.remainder - a.remainder);
  for (let k = 0; k < shortfall; k += 1) {
    counts[byRemainder[k % rowCount].index] += 1;
  }

  // Плоский список цветов: сначала места партий по порядку, затем пустые.
  const colors = [];
  for (const part of dist) {
    const seats = Math.max(0, Math.round(part.seats || 0));
    for (let i = 0; i < seats; i += 1) colors.push(part.color);
  }
  while (colors.length < seatCount) colors.push(emptyColor);

  const placed = [];
  counts.forEach((countInRow, rowIndex) => {
    for (let j = 0; j < countInRow; j += 1) {
      placed.push({
        angle: Math.PI * (1 - (j + 0.5) / countInRow),
        radius: radii[rowIndex],
      });
    }
  });

  // Слева направо: угол π — левый край полукруга, 0 — правый.
  placed.sort((a, b) => b.angle - a.angle);

  const gap = (OUTER_RADIUS - INNER_RADIUS) / (rowCount - 1);
  const seatRadius = Math.min(gap * 0.3, 12.5);

  return placed.map((seat, index) => ({
    x: CENTER_X + seat.radius * Math.cos(seat.angle),
    y: CENTER_Y - seat.radius * Math.sin(seat.angle),
    r: seatRadius,
    color: colors[index] || emptyColor,
  }));
}

/**
 * Строит SVG-элемент схемы для экрана.
 *
 * @param {object} options те же, что у computeSeats
 * @param {string} [options.title] подпись для программ чтения с экрана
 * @returns {SVGSVGElement}
 */
export function renderSeatChart(options) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${CHART_VIEWBOX.width} ${CHART_VIEWBOX.height}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('role', 'img');
  svg.style.display = 'block';

  const label = document.createElementNS('http://www.w3.org/2000/svg', 'title');
  label.textContent = options.title || 'Схема состава парламента';
  svg.append(label);

  for (const seat of computeSeats(options)) {
    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('cx', seat.x.toFixed(2));
    circle.setAttribute('cy', seat.y.toFixed(2));
    circle.setAttribute('r', String(seat.r));
    circle.setAttribute('fill', seat.color);
    // Тонкая обводка цветом фона — соседние места разных партий не сливаются.
    circle.setAttribute('stroke', SEAT_STROKE);
    circle.setAttribute('stroke-width', String(SEAT_STROKE_WIDTH));
    svg.append(circle);
  }

  return svg;
}

/**
 * Рисует схему на 2D-контексте canvas — для экспорта в PNG.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {object} options параметры схемы плюс прямоугольник размещения
 * @param {number} options.x левый край области
 * @param {number} options.y верхний край области
 * @param {number} options.width ширина области; высота выводится из пропорций
 */
export function drawSeatChartOnCanvas(ctx, options) {
  const scale = options.width / CHART_VIEWBOX.width;
  const seats = computeSeats(options);

  ctx.save();
  ctx.translate(options.x, options.y);
  ctx.scale(scale, scale);
  ctx.lineWidth = SEAT_STROKE_WIDTH;
  ctx.strokeStyle = SEAT_STROKE;

  for (const seat of seats) {
    ctx.beginPath();
    ctx.arc(seat.x, seat.y, seat.r, 0, Math.PI * 2);
    ctx.fillStyle = seat.color;
    ctx.fill();
    ctx.stroke();
  }

  ctx.restore();
  return CHART_VIEWBOX.height * scale;
}
