/**
 * Мелкие помощники для сборки DOM.
 *
 * Текст всегда ставится через textContent, а не innerHTML: названия партий и
 * созывов вводит пользователь, и они не должны интерпретироваться как разметка.
 */

/**
 * Создаёт элемент.
 *
 * @param {string} tag имя тега, можно с классами: `div.card.elev-sm`
 * @param {object|string|Node|Array} [props] атрибуты и обработчики, либо сразу содержимое
 * @param {...(string|Node|Array|null|undefined|false)} children содержимое
 */
export function h(tag, props, ...children) {
  const [name, ...classes] = tag.split('.');
  const node = document.createElement(name);
  if (classes.length) node.className = classes.join(' ');

  if (isContent(props)) {
    children.unshift(props);
  } else if (props) {
    applyProps(node, props);
  }

  append(node, children);
  return node;
}

function isContent(value) {
  return typeof value === 'string'
    || typeof value === 'number'
    || value instanceof Node
    || Array.isArray(value);
}

function applyProps(node, props) {
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;

    if (key === 'class') {
      node.className = node.className ? `${node.className} ${value}` : value;
    } else if (key === 'style' && typeof value === 'object') {
      Object.assign(node.style, value);
    } else if (key === 'dataset') {
      Object.assign(node.dataset, value);
    } else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'text') {
      node.textContent = String(value);
    } else if (key in node && key !== 'list' && typeof value !== 'object') {
      node[key] = value;
    } else {
      node.setAttribute(key, value === true ? '' : String(value));
    }
  }
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === '') continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** Заменяет содержимое контейнера. */
export function replaceChildren(container, ...children) {
  container.replaceChildren();
  append(container, children);
  return container;
}

/** Квадратик цвета партии — легенда, таблица, списки. */
export function swatch(color, size = 12) {
  return h('span', {
    class: 'swatch',
    style: { width: `${size}px`, height: `${size}px`, background: color },
    'aria-hidden': 'true',
  });
}

/** «34 (28,3 %)» — проценты с запятой, как принято в русской типографике. */
export function formatPercent(value, total) {
  if (!total) return '0,0 %';
  return `${((value / total) * 100).toFixed(1).replace('.', ',')} %`;
}

/**
 * Склонение существительного при числе: 1 место, 2 места, 5 мест.
 *
 * @param {number} count
 * @param {[string, string, string]} forms формы для 1, 2 и 5
 */
export function plural(count, [one, few, many]) {
  const n = Math.abs(count) % 100;
  if (n >= 11 && n <= 19) return many;
  const last = n % 10;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
}

/** «5 мест» — число вместе со склонённым словом. */
export function pluralize(count, forms) {
  return `${count} ${plural(count, forms)}`;
}

export const SEATS_FORMS = ['место', 'места', 'мест'];
export const PARTIES_FORMS = ['партия', 'партии', 'партий'];
export const CONVOCATIONS_FORMS = ['созыве', 'созывах', 'созывах'];

/** «12 марта», «сейчас» — короткая дата для списка созывов. */
export function formatShortDate(iso) {
  if (!iso) return 'сейчас';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
}
