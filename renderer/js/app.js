/**
 * Сборка приложения: состояние, действия, перерисовка, команды меню.
 *
 * Перерисовка простая — при любом изменении состояния экран собирается
 * заново. На 120 местах и десятке партий это доли миллисекунды, а состояние
 * гарантированно совпадает с тем, что в бэкенде. Исключение — поле ввода,
 * в котором сейчас печатают: его значение сохраняем, чтобы курсор не прыгал.
 */

import { h, replaceChildren } from './dom.js';
import { Store } from './store.js';
import { renderParliament } from './views/parliament.js';
import { renderParties } from './views/parties.js';
import { renderDialog, suggestFileName } from './views/dialogs.js';
import { renderChartToPng } from './export-png.js';

const bridge = window.parlament;
const store = new Store(bridge);
const root = document.getElementById('app');

/* — действия ————————————————————————————————————————————————————— */

const actions = {
  // -- навигация
  showParliament: () => store.setView('parliament'),
  showParties: () => store.setView('parties'),
  selectConvocation: (id) => store.selectConvocation(id),
  closeDialog: () => store.closeDialog(),

  // -- места
  setSeats: (convocationId, partyId, value) => store.setSeats(convocationId, partyId, value),
  flushSeats: () => store.flushSeats(),

  async resetSeats() {
    const convocation = store.selectedConvocation;
    const confirmed = await bridge.dialogs.confirm({
      title: 'Сбросить распределение',
      message: `Обнулить места в составе «${convocation.name}»?`,
      detail: 'Партии останутся в справочнике, все места станут нераспределёнными.',
      confirmLabel: 'Сбросить',
      danger: true,
    });
    if (!confirmed) return;

    await store.call('seats.reset', { convocationId: convocation.id });
    store.showToast('Распределение сброшено.');
  },

  // -- партии
  newParty: () => store.openDialog({ kind: 'party', party: null }),
  editParty: (party) => store.openDialog({ kind: 'party', party }),

  async savedParty(existing, draft) {
    const params = { name: draft.name, color: draft.color, abbr: draft.abbr };
    const result = existing
      ? await store.call('party.update', { id: existing.id, ...params })
      : await store.call('party.create', params);

    if (!result) return;      // ошибку уже показали уведомлением
    store.closeDialog();
    store.showToast(existing ? `Партия «${draft.name}» обновлена.` : `Партия «${draft.name}» создана.`);
  },

  async deleteParty(party) {
    const result = await store.call('party.usage', { id: party.id });
    if (!result) return;
    store.openDialog({ kind: 'delete-party', party, usage: result.usage });
  },

  async confirmDeleteParty(party) {
    const result = await store.call('party.delete', { id: party.id });
    if (!result) return;
    store.closeDialog();
    store.showToast(`Партия «${party.name}» удалена.`);
  },

  // -- созывы
  renameConvocation: () => store.openDialog({
    kind: 'rename-convocation',
    convocation: store.selectedConvocation,
  }),

  async confirmRename(id, name) {
    if (!name.trim()) {
      store.showToast('Название не может быть пустым.', 'error');
      return;
    }
    const result = await store.call('convocation.rename', { id, name });
    if (result) store.closeDialog();
  },

  newConvocation() {
    if (!store.parties.length) {
      store.showToast('Сначала создайте хотя бы одну партию.', 'error');
      return;
    }
    store.openDialog({ kind: 'new-convocation', suggestedName: store.state.nextConvocationName });
  },

  async confirmNewConvocation(name) {
    await store.flushSeats();
    const result = await store.call('convocation.fix', { name });
    if (!result) return;

    store.closeDialog();
    store.selectedConvocationId = result.convocationId;
    store.editingArchived = null;
    store.showToast('Созыв зафиксирован, открыт новый состав.', 'success');
  },

  /** Открыть архивный созыв на правку — по продуктовому решению без подтверждения. */
  editArchived() {
    store.editingArchived = store.selectedConvocationId;
    store.emit();
    store.showToast('Правки сохранятся в этот же созыв — новый не создаётся.');
  },

  // -- экспорт
  exportPng: () => store.openDialog({ kind: 'export', convocationId: store.selectedConvocationId }),

  async confirmExport(settings) {
    const convocation = store.state.convocations.find((c) => c.id === settings.convocationId);
    const { distribution } = store.summaryOf(convocation.id);

    if (!distribution.length) {
      store.showToast('Нечего экспортировать: места не распределены.', 'error');
      return;
    }

    store.closeDialog();
    try {
      const dataUrl = await renderChartToPng({
        width: settings.resolution.width,
        height: settings.resolution.height,
        totalSeats: store.totalSeats,
        rows: store.state.rows,
        distribution,
        title: convocation.name,
        withLegend: settings.withLegend,
        withTitle: settings.withTitle,
      });

      const fileName = settings.fileName.trim() || suggestFileName(convocation.name);
      const outcome = await bridge.dialogs.savePng({
        dataUrl,
        suggestedName: fileName.endsWith('.png') ? fileName : `${fileName}.png`,
      });

      if (outcome.error) store.showToast(outcome.error, 'error');
      else if (outcome.saved) {
        store.showToast('Картинка сохранена.', 'success', {
          label: 'Показать в папке',
          run: () => bridge.showItemInFolder(outcome.path),
        });
      }
    } catch (error) {
      store.showToast(`Не удалось построить картинку: ${error.message}`, 'error');
    }
  },

  // -- файл проекта
  async newProject() {
    const confirmed = await bridge.dialogs.confirm({
      title: 'Новый проект',
      message: 'Начать новый проект?',
      detail: 'Текущий проект уже сохранён в своём файле — открыть его снова можно через «Файл → Открыть».',
      confirmLabel: 'Начать новый',
    });
    if (!confirmed) return;

    await store.flushSeats();
    const result = await store.call('project.new');
    if (result) store.showToast('Создан новый проект.');
  },

  async openProject() {
    const path = await bridge.dialogs.openProject();
    if (!path) return;

    await store.flushSeats();
    const result = await store.call('project.open', { path });
    if (result) store.showToast(`Открыт проект «${result.state.projectName}».`, 'success');
  },

  async saveProjectAs() {
    const suggested = store.state.isDefaultProject
      ? 'Парламент.parlament.json'
      : store.state.projectName;

    const path = await bridge.dialogs.saveProjectAs(suggested);
    if (!path) return;

    await store.flushSeats();
    const result = await store.call('project.saveAs', { path });
    if (result) store.showToast(`Проект сохранён как «${result.state.projectName}».`, 'success');
  },

  showHelp: () => store.openDialog({ kind: 'help' }),
};

/* — отрисовка ————————————————————————————————————————————————————— */

function renderAppBar() {
  const convocation = store.selectedConvocation;
  const isArchiveView = store.view === 'parliament'
    && convocation && convocation.fixedAt && store.editingArchived !== convocation.id;
  const hasParties = store.parties.length > 0;

  if (store.view === 'parties') {
    return h('header.appbar',
      h('button.btn.btn-ghost', { type: 'button', onClick: actions.showParliament }, '← К парламенту'),
      h('span.appbar-brand', 'Партии'),
      h('div.appbar-actions',
        h('button.btn.btn-primary', { type: 'button', onClick: actions.newParty }, 'Новая партия')));
  }

  return h('header.appbar',
    h('span.appbar-brand', 'Парламент'),
    isArchiveView ? h('span.tag.tag-neutral', 'Просмотр истории') : null,
    store.state.savedToDisk && !store.state.isDefaultProject
      ? h('span.appbar-file', { text: store.state.projectName, title: store.state.projectPath })
      : null,
    h('div.appbar-actions',
      h('button.btn.btn-secondary', { type: 'button', onClick: actions.showParties }, 'Партии'),
      h('button.btn.btn-secondary', {
        type: 'button',
        disabled: !hasParties,
        onClick: actions.exportPng,
      }, 'Экспорт в PNG'),
      isArchiveView
        ? h('button.btn.btn-primary', { type: 'button', onClick: actions.editArchived }, 'Править состав')
        : h('button.btn.btn-primary', {
            type: 'button',
            disabled: !hasParties,
            onClick: actions.newConvocation,
          }, 'Новый созыв')));
}

function renderToast() {
  if (!store.toast) return null;
  const { message, tone, action } = store.toast;

  return h('div', {
    class: `toast is-${tone}`,
    role: tone === 'error' ? 'alert' : 'status',
  },
    h('span', { text: message }),
    action ? h('button', { type: 'button', onClick: () => { action.run(); store.dismissToast(); } },
      action.label) : null,
    h('button', { type: 'button', onClick: () => store.dismissToast(), 'aria-label': 'Закрыть' }, '✕'));
}

function render() {
  if (!store.state) return;

  // Запоминаем поле под курсором: перерисовка не должна сбивать ввод.
  // Ключ — id партии: названия могут совпадать, идентификаторы нет.
  const active = document.activeElement;
  const focusedRow = active && active.closest ? active.closest('.seat-row') : null;
  const focusedPartyId = focusedRow ? focusedRow.dataset.partyId : null;
  const selectionStart = focusedPartyId ? active.selectionStart : null;

  replaceChildren(root,
    renderAppBar(),
    store.view === 'parties' ? renderParties(store, actions) : renderParliament(store, actions),
    renderDialog(store, actions),
    renderToast());

  if (focusedPartyId) {
    const row = root.querySelector(`.seat-row[data-party-id="${focusedPartyId}"]`);
    const input = row && row.querySelector('input');
    if (input) {
      input.focus();
      if (selectionStart !== null) {
        try {
          input.setSelectionRange(selectionStart, selectionStart);
        } catch {
          // number-поля не везде поддерживают выделение — не страшно.
        }
      }
    }
  }
}

/* — команды меню ————————————————————————————————————————————————— */

const MENU_COMMANDS = {
  'project:new': actions.newProject,
  'project:open': actions.openProject,
  'project:saveAs': actions.saveProjectAs,
  'export:png': () => { if (store.parties.length) actions.exportPng(); },
  'view:parliament': actions.showParliament,
  'view:parties': actions.showParties,
  'party:new': actions.newParty,
  'convocation:new': actions.newConvocation,
  'seats:reset': () => { if (store.isEditable) actions.resetSeats(); },
  'help:about': actions.showHelp,
};

/* — запуск ————————————————————————————————————————————————————————— */

async function boot() {
  store.onChange(render);

  bridge.onMenuCommand((name) => {
    const command = MENU_COMMANDS[name];
    if (command) command();
  });

  bridge.onBootstrapError((error) => {
    store.showToast(`${error.message} Приложение открыто с чистым проектом.`, 'error');
  });

  bridge.onBackendCrashed(() => {
    store.showToast('Бэкенд остановился. Перезапустите приложение — данные сохранены в файле.', 'error');
  });

  const response = await bridge.call('state.get', {});
  if (!response.ok) {
    root.append(h('div.parties-empty', { text: response.error.message }));
    return;
  }
  store.applyState(response.result);

  // Незаписанные правки мест не должны пропасть при закрытии окна.
  window.addEventListener('beforeunload', () => store.flushSeats());
}

boot();
