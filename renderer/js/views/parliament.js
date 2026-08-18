/**
 * Главный экран: список созывов слева, схема в центре, распределение мест справа.
 */

import { h, swatch, formatPercent, formatShortDate, pluralize, SEATS_FORMS } from '../dom.js';
import { renderSeatChart } from '../seat-chart.js';

export function renderParliament(store, actions) {
  const convocation = store.selectedConvocation;
  if (!convocation) return h('div');

  const hasParties = store.parties.length > 0;

  return h('div.screen',
    renderConvocationRail(store, actions),
    hasParties ? renderStage(store, actions, convocation) : renderEmptyStage(store, actions),
    hasParties ? renderSeatRail(store, actions, convocation) : renderEmptyRail(store));
}

/* — левая колонка ————————————————————————————————————————————————— */

function renderConvocationRail(store, actions) {
  const cards = store.convocations.map((conv) => {
    const { used } = store.summaryOf(conv.id);
    const isSelected = conv.id === store.selectedConvocationId;
    const isOpen = !conv.fixedAt;

    return h('button.conv-card', {
      type: 'button',
      'aria-current': String(isSelected),
      onClick: () => actions.selectConvocation(conv.id),
    },
      h('span.conv-card-head',
        h('span.conv-card-name', { text: conv.name }),
        h('span.conv-card-date', { text: isOpen ? 'сейчас' : formatShortDate(conv.fixedAt) })),
      renderSeatBar(store.barSegmentsOf(conv.id)),
      h('span', {
        class: isOpen ? 'conv-card-note is-active' : 'conv-card-note',
        text: isOpen ? 'Редактируется' : `${used} из ${store.totalSeats} мест`,
      }));
  });

  return h('aside.rail-left',
    h('div.rail-label', 'Созывы'),
    h('div.conv-list', cards),
    h('button.btn.btn-ghost', {
      type: 'button',
      style: { justifyContent: 'flex-start', marginTop: '2px' },
      disabled: store.parties.length === 0,
      title: store.parties.length === 0 ? 'Сначала создайте партии' : '',
      onClick: actions.newConvocation,
    }, '＋ Новый созыв'));
}

/** Полоска состава: сегмент на партию плюс серый остаток. */
export function renderSeatBar(segments, height) {
  const total = segments.reduce((sum, segment) => sum + segment.flex, 0);
  const bar = h('div.seat-bar', segments.map((segment) => h('span', {
    style: { flex: String(segment.flex), background: segment.color },
  })));
  if (height) bar.style.height = `${height}px`;
  if (total === 0) bar.style.background = 'var(--color-neutral-300)';
  return bar;
}

/* — центральная колонка ——————————————————————————————————————————— */

function renderStage(store, actions, convocation) {
  const { distribution, used, largest, hasMajority } = store.summaryOf(convocation.id);
  const editable = store.isEditable;

  const legend = distribution.length
    ? distribution.map((party) => h('div.legend-item',
        swatch(party.color, 11),
        h('span', { text: party.name }),
        h('span.legend-seats', { text: String(party.seats) }),
        h('span.legend-percent', { text: formatPercent(party.seats, store.totalSeats) })))
    : h('div.legend-item', h('span.legend-percent',
        { text: 'Места пока не распределены — вся схема серая.' }));

  return h('main.stage',
    h('div.stage-head',
      h('h2', { text: convocation.name }),
      editable
        ? h('button.btn.btn-ghost', { type: 'button', onClick: actions.renameConvocation }, 'Переименовать')
        : h('span.stage-meta', { text: `зафиксирован ${formatShortDate(convocation.fixedAt)}` }),
      editable ? h('span.stage-meta', { text: `Распределено ${used} из ${store.totalSeats}` }) : null),

    h('div.seat-chart', renderSeatChart({
      total: store.totalSeats,
      rows: store.state.rows,
      dist: distribution,
      title: `${convocation.name}: ${pluralize(used, SEATS_FORMS)} из ${store.totalSeats}`,
    })),

    h('div.legend', legend),
    renderMajorityNote(store, largest, hasMajority));
}

function renderMajorityNote(store, largest, hasMajority) {
  const majority = store.majoritySeats;
  let note = 'Мест никому не отдано.';
  let tone = 'no-majority';

  if (largest && hasMajority) {
    note = `${largest.name} — абсолютное большинство`;
    tone = 'is-majority';
  } else if (largest) {
    note = `Крупнейшая фракция: ${largest.name} (${largest.seats}), большинства нет`;
  }

  return h('div.majority',
    h('span', { text: `Большинство — ${pluralize(majority, SEATS_FORMS)}.` }),
    h('span', { class: tone, text: note }));
}

/* — правая колонка ————————————————————————————————————————————————— */

function renderSeatRail(store, actions, convocation) {
  return store.isEditable
    ? renderEditableRail(store, actions, convocation)
    : renderReadonlyRail(store, actions, convocation);
}

function renderEditableRail(store, actions, convocation) {
  const { used, remaining } = store.summaryOf(convocation.id);
  const rows = store.rowsForConvocation(convocation.id);

  const inputs = rows.map((party) => {
    const input = h('input.input', {
      type: 'number',
      min: '0',
      max: String(store.totalSeats),
      step: '1',
      value: String(party.seats),
      'aria-label': `Мест у партии «${party.name}»`,
      onInput: (event) => {
        const applied = actions.setSeats(convocation.id, party.id, event.target.value);
        // Если ввод подрезан потолком, показываем в поле то, что реально
        // применилось, — но не мешаем стирать поле до пустого.
        if (event.target.value !== '' && Number(event.target.value) !== applied) {
          event.target.value = String(applied);
        }
      },
      onBlur: (event) => {
        // Пустое поле после ухода фокуса — это ноль, а не «не задано».
        if (event.target.value === '') event.target.value = '0';
        actions.flushSeats();
      },
    });

    return h('div.seat-row', { dataset: { partyId: party.id } },
      swatch(party.color, 12),
      h('span.seat-row-name', { text: party.name, title: party.name }),
      input);
  });

  return h('aside.rail-right',
    h('div.rail-label', 'Распределение мест'),
    h('div.seat-rows', inputs),
    renderSummary(store, used, remaining),
    h('div.rail-actions',
      h('button.btn.btn-secondary.btn-block', {
        type: 'button',
        disabled: used === 0,
        onClick: actions.resetSeats,
      }, 'Сбросить распределение'),
      h('button.btn.btn-primary.btn-block', {
        type: 'button',
        onClick: actions.exportPng,
      }, 'Экспортировать в PNG')));
}

function renderSummary(store, used, remaining) {
  const complete = remaining === 0;

  return h('dl.summary',
    h('div.summary-line', h('dt', 'Распределено'), h('dd', { text: `${used} / ${store.totalSeats}` })),
    h('div.summary-line',
      h('dt', 'Остаток'),
      h('dd', { class: complete ? '' : 'has-remainder', text: String(remaining) })),
    h('div.progress', {
      role: 'progressbar',
      'aria-valuenow': String(used),
      'aria-valuemin': '0',
      'aria-valuemax': String(store.totalSeats),
    }, h('span', { style: { width: `${(used / store.totalSeats) * 100}%` } })),
    h('span', {
      class: complete ? 'summary-note' : 'summary-note has-remainder',
      text: complete
        ? 'Все места распределены.'
        : `Осталось распределить ${pluralize(remaining, SEATS_FORMS)} — на схеме они серые.`,
    }));
}

function renderReadonlyRail(store, actions, convocation) {
  const rows = store.rowsForConvocation(convocation.id).filter((party) => party.seats > 0);

  return h('aside.rail-right',
    h('div.rail-label', 'Состав — только чтение'),
    h('div', { style: { display: 'flex', flexDirection: 'column', marginTop: '12px' } },
      rows.length
        ? rows.map((party) => h('div.seat-row-readonly',
            swatch(party.color, 12),
            h('span.seat-row-name', { text: party.name, title: party.name }),
            h('span.seat-row-readonly-count', { text: String(party.seats) })))
        : h('p.rail-hint', { text: 'В этом созыве места не распределялись.' })),
    h('p.rail-hint', {
      text: 'Прошлые составы можно править: нажмите «Править состав» — '
        + 'изменения сохранятся в этом же созыве.',
    }),
    h('div.rail-actions',
      h('button.btn.btn-secondary.btn-block', { type: 'button', onClick: actions.exportPng },
        'Экспортировать в PNG')));
}

/* — первый запуск ————————————————————————————————————————————————— */

function renderEmptyStage(store, actions) {
  return h('main.empty-stage',
    h('div.empty-chart', renderSeatChart({
      total: store.totalSeats,
      rows: store.state.rows,
      dist: [],
      title: 'Пустая схема парламента',
    })),
    h('h3', 'Партий пока нет'),
    h('p', `Создайте 3–6 партий с названиями и цветами — и распределите между ними `
      + `${store.totalSeats} мест первого состава.`),
    h('button.btn.btn-primary', { type: 'button', onClick: actions.newParty },
      'Создать первую партию'));
}

function renderEmptyRail(store) {
  return h('aside.rail-right',
    h('div.rail-label', 'Распределение мест'),
    h('p.rail-hint', {
      text: `Список появится после создания партий. Всего мест — ${store.totalSeats}.`,
    }));
}
