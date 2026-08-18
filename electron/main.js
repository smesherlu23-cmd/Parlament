'use strict';

/**
 * Главный процесс Electron: окно, меню, системные диалоги и мост к Python.
 *
 * Вся работа с данными идёт через бэкенд — здесь только то, что умеет
 * исключительно главный процесс: окно, нативные диалоги открытия/сохранения
 * и запись PNG на диск.
 */

const { app, BrowserWindow, dialog, ipcMain, Menu, shell } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');

const { Backend } = require('./backend');
const { buildMenu } = require('./menu');

const PROJECT_ROOT = path.join(__dirname, '..');
const PROJECT_FILTERS = [
  { name: 'Проект «Парламент»', extensions: ['json'] },
  { name: 'Все файлы', extensions: ['*'] },
];

let mainWindow = null;
let backend = null;

/** Файл, который открывается при старте, если пользователь не выбрал другой. */
function defaultDataFile() {
  return path.join(app.getPath('userData'), 'parlament.parlament.json');
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1300,
    height: 820,
    minWidth: 1080,
    minHeight: 700,
    backgroundColor: '#f3f2f2',
    title: 'Парламент',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.loadFile(path.join(PROJECT_ROOT, 'renderer', 'index.html'));

  // Внешние ссылки — в системном браузере, а не в окне приложения.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

/** Сообщение окну — например, пункт меню просит выполнить действие. */
function send(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

async function startBackend() {
  backend = new Backend({
    dataFile: defaultDataFile(),
    projectRoot: PROJECT_ROOT,
    resourcesPath: process.resourcesPath,
    isPackaged: app.isPackaged,
  });

  backend.on('bootstrap-error', (error) => send('backend:bootstrap-error', error));
  backend.on('crashed', () => send('backend:crashed'));

  return backend.start();
}

app.whenReady().then(async () => {
  try {
    await startBackend();
  } catch (error) {
    dialog.showErrorBox(
      'Не удалось запустить бэкенд',
      `${error.message}\n\nПроверьте, что установлен Python 3.9 или новее ` +
      'и он доступен как «python» в PATH.\n' +
      'Можно указать путь вручную через переменную окружения PARLAMENT_PYTHON.'
    );
    app.quit();
    return;
  }

  createWindow();
  Menu.setApplicationMenu(buildMenu({ send }));

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (backend) backend.stop();
});

// -- IPC: вызовы бэкенда ---------------------------------------------------

/**
 * Единственный канал к бэкенду. Ошибку приводим к простому объекту: класс
 * Error через IPC не переживает структурированное клонирование, а окну нужен
 * `kind`, чтобы отличить ошибку ввода от падения процесса.
 */
ipcMain.handle('backend:call', async (_event, method, params) => {
  try {
    return { ok: true, result: await backend.call(method, params) };
  } catch (error) {
    return { ok: false, error: { kind: error.kind || 'internal', message: error.message } };
  }
});

// -- IPC: системные диалоги ------------------------------------------------

ipcMain.handle('dialog:openProject', async () => {
  const { canceled, filePaths } = await dialog.showOpenDialog(mainWindow, {
    title: 'Открыть проект',
    filters: PROJECT_FILTERS,
    properties: ['openFile'],
  });
  return canceled ? null : filePaths[0];
});

ipcMain.handle('dialog:saveProjectAs', async (_event, suggestedName) => {
  const { canceled, filePath } = await dialog.showSaveDialog(mainWindow, {
    title: 'Сохранить проект как',
    defaultPath: suggestedName || 'Парламент.parlament.json',
    filters: PROJECT_FILTERS,
  });
  return canceled ? null : filePath;
});

/**
 * Сохраняет PNG, полученный из окна в виде data-URL.
 * @returns {{saved: boolean, path?: string, error?: string}}
 */
ipcMain.handle('dialog:savePng', async (_event, { dataUrl, suggestedName }) => {
  const { canceled, filePath } = await dialog.showSaveDialog(mainWindow, {
    title: 'Экспорт в PNG',
    defaultPath: suggestedName || 'Парламент.png',
    filters: [{ name: 'Изображение PNG', extensions: ['png'] }],
  });
  if (canceled || !filePath) return { saved: false };

  try {
    const base64 = String(dataUrl).replace(/^data:image\/png;base64,/, '');
    await fs.writeFile(filePath, Buffer.from(base64, 'base64'));
    return { saved: true, path: filePath };
  } catch (error) {
    return { saved: false, error: `Не удалось записать файл: ${error.message}` };
  }
});

/** Показать сохранённый файл в проводнике — по ссылке в уведомлении. */
ipcMain.handle('shell:showItem', (_event, filePath) => {
  shell.showItemInFolder(filePath);
});

ipcMain.handle('dialog:confirm', async (_event, { title, message, detail, confirmLabel, danger }) => {
  const { response } = await dialog.showMessageBox(mainWindow, {
    type: danger ? 'warning' : 'question',
    buttons: [confirmLabel || 'Продолжить', 'Отмена'],
    defaultId: 0,
    cancelId: 1,
    title: title || 'Подтверждение',
    message: message || '',
    detail: detail || undefined,
    noLink: true,
  });
  return response === 0;
});
