'use strict';

/**
 * Мост между окном и главным процессом.
 *
 * Окно не получает ни Node, ни ipcRenderer напрямую — только те несколько
 * функций, что перечислены ниже (contextIsolation включён). Поверхность
 * узкая намеренно: renderer работает с недоверенным содержимым (названия
 * партий вводит пользователь), и расширять её без нужды не стоит.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('parlament', {
  /**
   * Вызов метода бэкенда.
   * @returns {Promise<{ok: boolean, result?: object, error?: {kind: string, message: string}}>}
   */
  call: (method, params) => ipcRenderer.invoke('backend:call', method, params ?? {}),

  dialogs: {
    openProject: () => ipcRenderer.invoke('dialog:openProject'),
    saveProjectAs: (suggestedName) => ipcRenderer.invoke('dialog:saveProjectAs', suggestedName),
    savePng: (payload) => ipcRenderer.invoke('dialog:savePng', payload),
    confirm: (payload) => ipcRenderer.invoke('dialog:confirm', payload),
  },

  showItemInFolder: (filePath) => ipcRenderer.invoke('shell:showItem', filePath),

  /** Команды из меню приложения. Возвращает функцию отписки. */
  onMenuCommand: (handler) => {
    const listener = (_event, name) => handler(name);
    ipcRenderer.on('menu:command', listener);
    return () => ipcRenderer.removeListener('menu:command', listener);
  },

  /** Бэкенд не смог прочитать файл проекта при запуске. */
  onBootstrapError: (handler) => {
    ipcRenderer.on('backend:bootstrap-error', (_event, error) => handler(error));
  },

  /** Процесс бэкенда упал — дальше работать нельзя. */
  onBackendCrashed: (handler) => {
    ipcRenderer.on('backend:crashed', () => handler());
  },
});
