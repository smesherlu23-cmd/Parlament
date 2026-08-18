/**
 * Справочник партий: таблица со всеми партиями и действиями над ними.
 *
 * Правки здесь отражаются во всех созывах — партия хранится один раз,
 * созывы ссылаются на неё по идентификатору.
 */

import { h, swatch, pluralize, PARTIES_FORMS } from '../dom.js';

export function renderParties(store, actions) {
  const parties = store.parties;

  return h('div.parties-screen',
    h('div.parties-head',
      h('h3', { text: pluralize(parties.length, PARTIES_FORMS) }),
      h('span', 'Цвет и название обновляются во всех созывах, где партия участвует.')),
    parties.length ? renderTable(store, actions, parties) : renderEmpty(actions));
}

function renderTable(store, actions, parties) {
  const seatsNow = store.seatsOf(store.selectedConvocationId);

  const rows = parties.map((party) => h('tr',
    h('td', swatch(party.color, 20)),
    h('td.name', { text: party.name }),
    h('td.muted', { text: party.abbr || '—' }),
    h('td.hex', { text: party.color }),
    h('td.muted.numeric', { text: String(store.convocationCountFor(party.id)) }),
    h('td.numeric', { text: String(seatsNow[party.id] || 0) }),
    h('td', h('div.row-actions',
      h('button.btn.btn-ghost', { type: 'button', onClick: () => actions.editParty(party) },
        'Изменить'),
      h('button.btn.btn-ghost.btn-danger', { type: 'button', onClick: () => actions.deleteParty(party) },
        'Удалить')))));

  return h('table.table.parties-table',
    h('thead', h('tr',
      h('th', { style: { width: '44px' } }),
      h('th', 'Название'),
      h('th', { style: { width: '110px' } }, 'Сокращение'),
      h('th', { style: { width: '130px' } }, 'Цвет'),
      h('th', { style: { width: '130px' } }, 'Созывов'),
      h('th', { style: { width: '120px' } }, 'Мест сейчас'),
      h('th', { style: { width: '150px' } }))),
    h('tbody', rows));
}

function renderEmpty(actions) {
  return h('div.parties-empty',
    h('p', 'Справочник пуст. Начните с 3–6 партий — их можно будет переименовать и перекрасить в любой момент.'),
    h('button.btn.btn-primary', { type: 'button', onClick: actions.newParty }, 'Новая партия'));
}
