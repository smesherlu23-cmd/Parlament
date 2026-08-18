/**
 * Проверка меню и работы с файлом проекта.
 *
 *     npm run test:menus
 *
 * Команды меню приходят в окно тем же каналом, что и от настоящего меню,
 * а нативные диалоги (открыть, сохранить как, подтверждение, сохранение
 * PNG) подменяются заглушками до загрузки главного процесса — иначе
 * проверка встала бы на системном окне. Всё остальное — настоящее
 * приложение с настоящим Python-бэкендом.
 */
const { app, dialog } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'parl-'));
const SAVE_AS = path.join(TMP, 'вторая-игра.parlament.json');
const results = [];
const check = (n, ok, d) => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d ? '  — ' + d : ''}`); };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// Заглушки нативных диалогов.
dialog.showSaveDialog = async (_w, opts) => {
  if ((opts.filters || []).some((f) => f.extensions.includes('png'))) {
    return { canceled: false, filePath: path.join(TMP, opts.defaultPath) };
  }
  return { canceled: false, filePath: SAVE_AS };
};
dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [SAVE_AS] });
dialog.showMessageBox = async () => ({ response: 0 });   // всегда «да»

app.on('browser-window-created', (_e, win) => {
  win.webContents.on('console-message', (_ev, l, m) => { if (l >= 2) console.log('RENDERER', m); });
  win.webContents.once('did-finish-load', () => run(win).catch((e) => {
    console.log('ERROR', e.stack); app.exit(1);
  }));
});

async function run(win) {
  const js = (c) => win.webContents.executeJavaScript(c, true);
  const menu = (cmd) => win.webContents.send('menu:command', cmd);
  await wait(1800);

  // Меню «Правка → Новая партия» открывает диалог.
  menu('party:new');
  await wait(400);
  check('меню: «Новая партия» открывает диалог',
    await js(`document.querySelector('.dialog-title')?.textContent`) === 'Новая партия');

  await js(`(() => {
    const i = document.querySelectorAll('.dialog input.input');
    const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles:true})); };
    set(i[0], 'Народный союз'); set(i[1], 'НС'); set(i[2], '#0088b0');
    document.querySelector('.dialog button[type=submit]').click();
  })()`);
  await wait(400);

  // Меню «Вид → Справочник партий».
  menu('view:parties');
  await wait(300);
  check('меню: переход в справочник', !!(await js(`!!document.querySelector('.parties-screen')`)));

  menu('view:parliament');
  await wait(300);
  check('меню: возврат к парламенту', !!(await js(`!!document.querySelector('.stage')`)));

  // Меню «Справка → Как пользоваться».
  menu('help:about');
  await wait(300);
  check('меню: справка открывается',
    await js(`document.querySelector('.dialog-title')?.textContent`) === 'Как пользоваться');
  await js(`document.querySelector('.dialog button[type=submit]').click()`);
  await wait(250);

  // Раздаём места и сбрасываем через меню (подтверждение — заглушка).
  await js(`(() => {
    const i = document.querySelector('.seat-row input');
    i.value = '55'; i.dispatchEvent(new Event('input', {bubbles:true}));
  })()`);
  await wait(600);
  check('места распределены', await js(`document.querySelector('.summary-line dd')?.textContent`) === '55 / 120');

  menu('seats:reset');
  await wait(700);
  check('меню: сброс распределения',
    await js(`document.querySelector('.summary-line dd')?.textContent`) === '0 / 120');

  // Возвращаем места и фиксируем созыв, чтобы было что сохранять.
  await js(`(() => {
    const i = document.querySelector('.seat-row input');
    i.value = '61'; i.dispatchEvent(new Event('input', {bubbles:true}));
  })()`);
  await wait(600);

  // «Файл → Сохранить как…» — пишем во второй файл и продолжаем в нём.
  menu('project:saveAs');
  await wait(900);
  check('меню: «Сохранить как» создал файл', fs.existsSync(SAVE_AS), SAVE_AS);
  const saved = JSON.parse(fs.readFileSync(SAVE_AS, 'utf8'));
  check('файл содержит партию и места', saved.parties.length === 1
    && saved.convocations[0].seats[saved.parties[0].id] === 61,
    JSON.stringify(saved.convocations[0].seats));
  check('шапка показывает имя файла',
    (await js(`document.querySelector('.appbar-file')?.textContent`)) === 'вторая-игра.parlament.json');

  // Дальнейшие правки уходят уже в новый файл.
  await js(`(() => {
    const i = document.querySelector('.seat-row input');
    i.value = '70'; i.dispatchEvent(new Event('input', {bubbles:true}));
  })()`);
  await wait(800);
  check('правки пишутся в новый файл',
    JSON.parse(fs.readFileSync(SAVE_AS, 'utf8')).convocations[0].seats[saved.parties[0].id] === 70);

  // «Файл → Новый проект» — чистое состояние.
  menu('project:new');
  await wait(900);
  check('меню: новый проект пуст',
    (await js(`!!document.querySelector('.empty-stage')`)) === true);

  // «Файл → Открыть» — возвращаемся во вторую игру.
  menu('project:open');
  await wait(900);
  const after = await js(`(() => ({
    parties: document.querySelectorAll('.seat-row').length,
    used: document.querySelector('.summary-line dd')?.textContent,
  }))()`);
  check('меню: «Открыть» восстановил проект',
    after.parties === 1 && after.used === '70 / 120', JSON.stringify(after));

  // «Файл → Экспорт в PNG» открывает диалог экспорта.
  menu('export:png');
  await wait(500);
  check('меню: экспорт открывает диалог',
    (await js(`document.querySelector('.dialog-title')?.textContent`)) === 'Экспорт в PNG');

  // Доводим экспорт до записи файла.
  await js(`document.querySelector('.dialog button[type=submit]').click()`);
  await wait(1800);
  const pngs = fs.readdirSync(TMP).filter((f) => f.endsWith('.png'));
  check('экспорт записал PNG на диск', pngs.length === 1, pngs.join(', '));
  if (pngs.length) {
    const buf = fs.readFileSync(path.join(TMP, pngs[0]));
    check('PNG корректного размера 1920×1080',
      buf.readUInt32BE(16) === 1920 && buf.readUInt32BE(20) === 1080,
      `${buf.readUInt32BE(16)}×${buf.readUInt32BE(20)}, ${Math.round(buf.length/1024)} КБ`);
  }

  const failed = results.filter((r) => !r).length;
  console.log(`\n=== ${results.length - failed}/${results.length} проверок пройдено ===`);
  app.exit(failed ? 1 : 0);
}

require(path.join(__dirname, '..', 'electron', 'main.js'));
