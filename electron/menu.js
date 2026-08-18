'use strict';

/**
 * Меню приложения — «Файл · Правка · Справка» из макета.
 *
 * Пункты не делают работу сами: они отправляют окну команду, а окно уже
 * решает, что показать (диалог, подтверждение) и что вызвать у бэкенда.
 * Так одно и то же действие ведёт себя одинаково, вызвано оно из меню или
 * с кнопки на экране.
 */

const { app, Menu, shell } = require('electron');

function buildMenu({ send }) {
  const command = (name) => () => send('menu:command', name);

  return Menu.buildFromTemplate([
    {
      label: 'Файл',
      submenu: [
        { label: 'Новый проект', accelerator: 'CmdOrCtrl+N', click: command('project:new') },
        { label: 'Открыть…', accelerator: 'CmdOrCtrl+O', click: command('project:open') },
        { type: 'separator' },
        { label: 'Сохранить как…', accelerator: 'CmdOrCtrl+Shift+S', click: command('project:saveAs') },
        { type: 'separator' },
        { label: 'Экспортировать в PNG…', accelerator: 'CmdOrCtrl+E', click: command('export:png') },
        { type: 'separator' },
        { label: 'Выход', role: 'quit' },
      ],
    },
    {
      label: 'Правка',
      submenu: [
        { label: 'Партии', accelerator: 'CmdOrCtrl+P', click: command('view:parties') },
        { label: 'Новая партия', accelerator: 'CmdOrCtrl+Shift+N', click: command('party:new') },
        { type: 'separator' },
        { label: 'Новый созыв', accelerator: 'CmdOrCtrl+Shift+K', click: command('convocation:new') },
        { label: 'Сбросить распределение', click: command('seats:reset') },
        { type: 'separator' },
        { label: 'Отменить', role: 'undo' },
        { label: 'Повторить', role: 'redo' },
        { type: 'separator' },
        { label: 'Вырезать', role: 'cut' },
        { label: 'Копировать', role: 'copy' },
        { label: 'Вставить', role: 'paste' },
        { label: 'Выделить всё', role: 'selectAll' },
      ],
    },
    {
      label: 'Вид',
      submenu: [
        { label: 'Парламент', accelerator: 'CmdOrCtrl+1', click: command('view:parliament') },
        { label: 'Справочник партий', accelerator: 'CmdOrCtrl+2', click: command('view:parties') },
        { type: 'separator' },
        { label: 'Увеличить', role: 'zoomIn' },
        { label: 'Уменьшить', role: 'zoomOut' },
        { label: 'Обычный размер', role: 'resetZoom' },
        { type: 'separator' },
        { label: 'Во весь экран', role: 'togglefullscreen' },
        { label: 'Инструменты разработчика', role: 'toggleDevTools' },
      ],
    },
    {
      label: 'Справка',
      submenu: [
        { label: 'Как пользоваться', click: command('help:about') },
        {
          label: 'Исходный код',
          click: () => shell.openExternal('https://github.com/smesherlu23-cmd/Parlament'),
        },
        { type: 'separator' },
        { label: `Версия ${app.getVersion()}`, enabled: false },
      ],
    },
  ]);
}

module.exports = { buildMenu };
