/**
 * Модальные диалоги: партия, удаление, фиксация созыва, переименование, экспорт.
 *
 * Все они устроены одинаково: `dialogShell` даёт затемнение, рамку и общее
 * поведение (Esc закрывает, фокус уходит в первое поле, клик по фону закрывает),
 * а каждая функция ниже описывает только содержимое и кнопки.
 */

import { h, swatch, pluralize, plural, SEATS_FORMS, CONVOCATIONS_FORMS } from '../dom.js';
import { renderSeatChart } from '../seat-chart.js';
import { renderSeatBar } from './parliament.js';

/** Готовая палитра из макета — восемь цветов, различимых рядом на схеме. */
const PALETTE = [
  '#0088b0', '#d6006c', '#4c7a34', '#edbb00',
  '#2d2b2b', '#b3541e', '#7d7979', '#5b4b8a',
];

const RESOLUTIONS = [
  { label: '1920 × 1080', width: 1920, height: 1080 },
  { label: '2560 × 1440', width: 2560, height: 1440 },
  { label: '3840 × 2160', width: 3840, height: 2160 },
];

export function renderDialog(store, actions) {
  const dialog = store.dialog;
  if (!dialog) return null;

  switch (dialog.kind) {
    case 'party': return renderPartyDialog(store, actions, dialog);
    case 'delete-party': return renderDeletePartyDialog(store, actions, dialog);
    case 'new-convocation': return renderNewConvocationDialog(store, actions, dialog);
    case 'rename-convocation': return renderRenameDialog(store, actions, dialog);
    case 'export': return renderExportDialog(store, actions, dialog);
    case 'help': return renderHelpDialog(store, actions);
    default: return null;
  }
}

/**
 * Общая обёртка диалога.
 *
 * @param {object} options
 * @param {function} options.onClose закрытие по Esc, фону и кнопке «Отмена»
 * @param {function} [options.onSubmit] отправка формы (Enter в поле)
 */
function dialogShell({ onClose, onSubmit, width }, ...content) {
  const form = h('form.dialog', {
    style: width ? { width: `${width}px` } : null,
    role: 'dialog',
    'aria-modal': 'true',
    onSubmit: (event) => {
      event.preventDefault();
      if (onSubmit) onSubmit();
    },
  }, content);

  const backdrop = h('div.dialog-backdrop', {
    onMousedown: (event) => {
      // Закрываем только по клику мимо окна, а не по отпусканию мыши,
      // начатому внутри (выделение текста не должно закрывать диалог).
      if (event.target === backdrop) onClose();
    },
  }, form);

  backdrop.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
    }
  });

  // Фокус в первое поле — диалог сразу готов к вводу с клавиатуры.
  queueMicrotask(() => {
    const first = form.querySelector('input:not([type=color]), textarea, button.btn-primary');
    if (first) first.focus();
    if (first && first.select) first.select();
  });

  return backdrop;
}

function actionsRow(onCancel, submitLabel, { danger = false, disabled = false } = {}) {
  return h('div.dialog-actions',
    h('button.btn.btn-secondary', { type: 'button', onClick: onCancel }, 'Отмена'),
    h('button.btn', {
      type: 'submit',
      class: danger ? 'btn-primary btn-primary-danger' : 'btn-primary',
      disabled,
    }, submitLabel));
}

/* — создание и правка партии ————————————————————————————————————— */

function renderPartyDialog(store, actions, dialog) {
  const editing = Boolean(dialog.party);
  // Черновик живёт в самом диалоге: пока не нажали «Сохранить», состояние
  // приложения не трогаем.
  const draft = {
    name: dialog.party?.name || '',
    abbr: dialog.party?.abbr || '',
    color: dialog.party?.color || PALETTE[store.parties.length % PALETTE.length],
  };

  const preview = swatch(draft.color, 14);
  const hexInput = h('input.input', {
    type: 'text',
    value: draft.color.toUpperCase(),
    spellcheck: false,
    'aria-label': 'Цвет в формате HEX',
    style: { fontFamily: 'ui-monospace, Menlo, Consolas, monospace' },
    onInput: (event) => setColor(event.target.value, { fromHexField: true }),
  });
  const colorPicker = h('input', {
    type: 'color',
    value: draft.color,
    'aria-label': 'Выбрать цвет из палитры RGB',
    onInput: (event) => setColor(event.target.value),
  });
  const paletteRow = h('div.palette');
  const errorSlot = h('div');

  function setColor(value, { fromHexField = false } = {}) {
    draft.color = normalizeHex(value) || draft.color;
    if (!fromHexField) hexInput.value = draft.color.toUpperCase();
    colorPicker.value = draft.color;
    preview.style.background = draft.color;
    for (const button of paletteRow.querySelectorAll('.palette-swatch')) {
      button.setAttribute('aria-pressed', String(button.dataset.color === draft.color));
    }
  }

  paletteRow.append(
    ...PALETTE.map((color) => h('button.palette-swatch', {
      type: 'button',
      dataset: { color },
      style: { background: color },
      'aria-label': `Цвет ${color}`,
      'aria-pressed': String(color === draft.color.toLowerCase()),
      onClick: () => setColor(color),
    })),
    h('label.palette-custom', colorPicker, h('span', 'Своя палитра RGB…')));

  const nameInput = h('input.input', {
    type: 'text',
    value: draft.name,
    maxlength: '120',
    'aria-label': 'Название партии',
    onInput: (event) => { draft.name = event.target.value; },
  });
  const abbrInput = h('input.input', {
    type: 'text',
    value: draft.abbr,
    maxlength: '12',
    'aria-label': 'Сокращение партии',
    onInput: (event) => { draft.abbr = event.target.value; },
  });

  async function submit() {
    errorSlot.replaceChildren();

    if (!draft.name.trim()) {
      errorSlot.append(h('div.field-error', 'Введите название партии.'));
      nameInput.focus();
      return;
    }
    if (!normalizeHex(hexInput.value)) {
      errorSlot.append(h('div.field-error', 'Цвет должен быть в виде #3A7CA5.'));
      hexInput.focus();
      return;
    }
    draft.color = normalizeHex(hexInput.value);
    await actions.savedParty(dialog.party, draft);
  }

  return dialogShell({ onClose: actions.closeDialog, onSubmit: submit, width: 460 },
    h('div.dialog-title', editing ? 'Изменить партию' : 'Новая партия'),

    h('div.field', h('label', 'Название'), nameInput),
    h('div.field-row',
      h('div.field', { style: { width: '150px' } }, h('label', 'Сокращение'), abbrInput),
      h('div.field', { style: { flex: '1' } }, h('label', 'Цвет — HEX'), hexInput)),
    h('div.field', h('label', 'Палитра'), paletteRow),

    h('div.preview-strip', preview, h('span', 'Так партия выглядит на схеме и в легенде')),
    errorSlot,
    actionsRow(actions.closeDialog, 'Сохранить'));
}

/** Приводит ввод к `#rrggbb` или возвращает null, если это не цвет. */
function normalizeHex(value) {
  let text = String(value || '').trim();
  if (!text) return null;
  if (!text.startsWith('#')) text = `#${text}`;
  if (/^#[0-9a-fA-F]{3}$/.test(text)) {
    text = `#${text.slice(1).split('').map((c) => c + c).join('')}`;
  }
  return /^#[0-9a-fA-F]{6}$/.test(text) ? text.toLowerCase() : null;
}

/* — удаление партии ————————————————————————————————————————————— */

function renderDeletePartyDialog(store, actions, dialog) {
  const { party, usage } = dialog;
  const count = usage.length;

  const explanation = count === 0
    ? 'Партия не участвует ни в одном созыве — удаление ничего не изменит в истории.'
    : `Партия участвует в ${count} ${plural(count, CONVOCATIONS_FORMS)}. `
      + 'После удаления её места в этих составах станут нераспределёнными.';

  return dialogShell(
    { onClose: actions.closeDialog, onSubmit: () => actions.confirmDeleteParty(party), width: 460 },
    h('div.dialog-title', { text: `Удалить «${party.name}»?` }),
    h('div.dialog-body', { text: explanation }),
    count > 0
      ? h('div.inset-panel', h('div.inset-list',
          swatch(party.color, 12),
          h('ul', usage.map((entry) => h('li', {
            text: `${entry.convocationName} — ${pluralize(entry.seats, SEATS_FORMS)}`,
          })))))
      : null,
    actionsRow(actions.closeDialog, 'Удалить всё равно', { danger: true }));
}

/* — фиксация созыва ————————————————————————————————————————————— */

function renderNewConvocationDialog(store, actions, dialog) {
  const current = store.state.convocations.find((c) => c.id === store.state.activeConvocationId);
  const { used, distribution } = store.summaryOf(current.id);

  const nameInput = h('input.input', {
    type: 'text',
    value: dialog.suggestedName,
    maxlength: '120',
    'aria-label': 'Название нового созыва',
  });

  return dialogShell(
    {
      onClose: actions.closeDialog,
      onSubmit: () => actions.confirmNewConvocation(nameInput.value),
      width: 460,
    },
    h('div.dialog-title', { text: `Зафиксировать ${current.name}?` }),
    h('div.dialog-body', {
      text: `Текущее распределение сохранится в истории, и откроется пустой ${dialog.suggestedName}.`,
    }),
    h('div.inset-panel',
      renderSeatBar(store.barSegmentsOf(current.id), 8),
      h('span.muted', {
        text: `${used} из ${store.totalSeats} мест распределены · `
          + `${pluralize(distribution.length, ['партия', 'партии', 'партий'])}`,
      })),
    h('div.field', h('label', 'Название нового созыва'), nameInput),
    actionsRow(actions.closeDialog, 'Зафиксировать'));
}

/* — переименование созыва ————————————————————————————————————— */

function renderRenameDialog(store, actions, dialog) {
  const input = h('input.input', {
    type: 'text',
    value: dialog.convocation.name,
    maxlength: '120',
    'aria-label': 'Название созыва',
  });

  return dialogShell(
    {
      onClose: actions.closeDialog,
      onSubmit: () => actions.confirmRename(dialog.convocation.id, input.value),
      width: 440,
    },
    h('div.dialog-title', 'Переименовать созыв'),
    h('div.dialog-body', 'Например: «Третий состав (кризисные выборы)».'),
    h('div.field', h('label', 'Название'), input),
    actionsRow(actions.closeDialog, 'Сохранить'));
}

/* — экспорт в PNG ————————————————————————————————————————————————— */

function renderExportDialog(store, actions, dialog) {
  const convocation = store.state.convocations.find((c) => c.id === dialog.convocationId);
  const { distribution } = store.summaryOf(convocation.id);

  const options = {
    resolution: RESOLUTIONS[0],
    withLegend: true,
    withTitle: false,
  };

  const fileInput = h('input.input', {
    type: 'text',
    value: suggestFileName(convocation.name),
    'aria-label': 'Имя файла',
  });

  const resolutionPicker = h('div.seg', RESOLUTIONS.map((resolution, index) => h('label.seg-opt',
    h('input', {
      type: 'radio',
      name: 'resolution',
      checked: index === 0,
      onChange: () => { options.resolution = resolution; },
    }),
    resolution.label)));

  const legendPreview = h('div.export-legend', distribution.map((party) => h('div.export-legend-item',
    swatch(party.color, 7),
    h('span', { text: `${party.abbr || party.name} ${party.seats}` }))));

  return dialogShell(
    {
      onClose: actions.closeDialog,
      onSubmit: () => actions.confirmExport({
        convocationId: convocation.id,
        fileName: fileInput.value,
        ...options,
      }),
      width: 500,
    },
    h('div.dialog-title', 'Экспорт в PNG'),

    h('div.export-preview',
      renderSeatChart({
        total: store.totalSeats,
        rows: store.state.rows,
        dist: distribution,
        title: 'Предпросмотр экспорта',
      }),
      legendPreview),

    h('div.field', h('label', 'Имя файла'), fileInput),
    h('div.field', h('label', 'Разрешение'), resolutionPicker),

    h('div', { style: { display: 'flex', gap: '20px' } },
      checkbox('Легенда на картинке', true, (value) => { options.withLegend = value; }),
      checkbox('Название созыва', false, (value) => { options.withTitle = value; })),

    actionsRow(actions.closeDialog, 'Сохранить как…'));
}

function checkbox(label, checked, onChange) {
  return h('label.check',
    h('input', { type: 'checkbox', checked, onChange: (event) => onChange(event.target.checked) }),
    h('span.box', '✓'),
    label);
}

/**
 * «Парламент_Третий_состав.png» — пробелы в подчёркивания, запрещённые в
 * именах файлов Windows символы убираем.
 */
export function suggestFileName(convocationName) {
  const safe = convocationName
    .replace(/[\\/:*?"<>|]/g, '')
    .trim()
    .replace(/\s+/g, '_');
  return `Парламент_${safe}.png`;
}

/* — справка ————————————————————————————————————————————————————————— */

function renderHelpDialog(store, actions) {
  const steps = [
    'Создайте партии в справочнике («Партии» в шапке): название, цвет, при желании — сокращение.',
    `Распределите ${store.totalSeats} мест в правой панели — схема пересчитывается сразу.`,
    'Когда состав готов, нажмите «Новый созыв»: текущий уходит в историю, открывается следующий.',
    'Любой созыв из списка слева можно открыть и посмотреть; архивный правится по кнопке «Править состав».',
    'Кнопка «Экспортировать в PNG» сохраняет схему с легендой картинкой для чата или поста.',
  ];

  return dialogShell({ onClose: actions.closeDialog, onSubmit: actions.closeDialog, width: 520 },
    h('div.dialog-title', 'Как пользоваться'),
    h('ol', { style: { margin: '0', paddingLeft: '20px', fontSize: '14px', lineHeight: '1.7' } },
      steps.map((step) => h('li', { text: step }))),
    h('div.dialog-body',
      'Проект сохраняется сам после каждого изменения. Через «Файл» можно завести '
      + 'отдельный файл под каждую игру.'),
    h('div.dialog-actions', h('button.btn.btn-primary', { type: 'submit' }, 'Понятно')));
}
