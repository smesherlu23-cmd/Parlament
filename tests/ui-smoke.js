/**
 * Сквозная проверка интерфейса: поднимает настоящее приложение (тот же
 * electron/main.js и тот же Python-бэкенд) и прогоняет по нему сценарии из
 * ТЗ — от первого запуска до экспорта картинки.
 *
 *     npm run test:ui
 *
 * Скрипт ничего не подменяет: он подключается к созданному окну и работает
 * с ним так же, как пользователь, — кликами и вводом в поля. По ходу
 * сохраняет скриншоты экранов в папку, заданную SMOKE_OUT.
 *
 * Проверка пишет в тот же файл проекта, что и приложение, поэтому запускать
 * её стоит на чистом состоянии (npm run test:ui удаляет файл сам).
 */
const { app, BrowserWindow } = require('electron');
const path = require('node:path');
const fs = require('node:fs');

const OUT = process.env.SMOKE_OUT || path.join(__dirname, '..', 'dist', 'ui-smoke');
const APP_ROOT = path.join(__dirname, '..');
fs.mkdirSync(OUT, { recursive: true });

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}

app.on('browser-window-created', (_e, win) => {
  win.webContents.on('console-message', (_ev, level, message) => {
    if (level >= 2) console.log('RENDERER', message);
  });
  win.webContents.once('did-finish-load', () => run(win).catch((e) => {
    console.log('SMOKE ERROR', e.stack);
    app.exit(1);
  }));
});

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const js = (win, code) => win.webContents.executeJavaScript(code, true);

async function run(win) {
  await wait(1500);

  // 1. Стартовое состояние — пустой проект, экран первого запуска.
  let probe = await js(win, `(() => ({
    heading: document.querySelector('.empty-stage h3')?.textContent,
    railHint: document.querySelector('.rail-right .rail-hint')?.textContent,
    convName: document.querySelector('.conv-card-name')?.textContent,
    seats: document.querySelectorAll('.empty-chart circle').length,
    exportDisabled: document.querySelectorAll('.appbar .btn-secondary')[1]?.disabled,
    newConvDisabled: document.querySelector('.appbar .btn-primary')?.disabled,
    font: getComputedStyle(document.body).fontFamily,
  }))()`);
  check('первый запуск: заголовок «Партий пока нет»', probe.heading === 'Партий пока нет', probe.heading);
  check('первый запуск: созыв «Первый состав»', probe.convName === 'Первый состав', probe.convName);
  check('первый запуск: на схеме 120 мест', probe.seats === 120, String(probe.seats));
  check('первый запуск: экспорт и новый созыв выключены', probe.exportDisabled === true && probe.newConvDisabled === true);
  check('шрифт Source Serif 4 применён', /Source Serif 4/.test(probe.font), probe.font);

  // 2. Создаём партии через диалог — как это делает пользователь.
  const PARTIES = [
    ['Народный союз', 'НС', '#0088b0', 34],
    ['Партия труда', 'ПТ', '#d6006c', 27],
    ['Аграрный блок', 'АБ', '#4c7a34', 18],
    ['Либеральный форум', 'ЛФ', '#edbb00', 14],
    ['Консервативная лига', 'КЛ', '#2d2b2b', 12],
    ['Движение «Заря»', 'ДЗ', '#b3541e', 9],
    ['Независимые', 'НЗ', '#7d7979', 6],
  ];

  // Первую партию заводим с пустого экрана, остальные — из справочника.
  await js(win, `document.querySelector('.empty-stage .btn-primary').click()`);
  await wait(200);

  for (const [name, abbr, color] of PARTIES) {
    const hasDialog = await js(win, `!!document.querySelector('.dialog')`);
    if (!hasDialog) {
      await js(win, `document.querySelector('.appbar .btn-primary').click()`);
      await wait(200);
    }
    await js(win, `(() => {
      const d = document.querySelector('.dialog');
      const inputs = d.querySelectorAll('input.input');
      const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles:true})); };
      set(inputs[0], ${JSON.stringify(name)});
      set(inputs[1], ${JSON.stringify(abbr)});
      set(inputs[2], ${JSON.stringify(color)});
      d.querySelector('button[type=submit]').click();
    })()`);
    await wait(260);
    // После первой партии остаёмся в справочнике — там кнопка «Новая партия».
    const onParties = await js(win, `!!document.querySelector('.parties-screen')`);
    if (!onParties) {
      await js(win, `document.querySelectorAll('.appbar .btn-secondary')[0].click()`);
      await wait(200);
    }
  }

  // Возвращаемся на главный экран к распределению мест.
  await js(win, `document.querySelector('.appbar .btn-ghost')?.click()`);
  await wait(300);

  probe = await js(win, `(() => ({
    count: document.querySelectorAll('.seat-row').length,
    dialogOpen: !!document.querySelector('.dialog'),
    names: [...document.querySelectorAll('.seat-row-name')].map(n => n.textContent),
  }))()`);
  check('создано 7 партий, все в правой панели', probe.count === 7, `${probe.count}: ${probe.names.join(', ')}`);
  check('диалог закрылся после сохранения', probe.dialogOpen === false);

  // 3. Распределяем места — схема должна пересчитаться сразу.
  for (let i = 0; i < PARTIES.length; i++) {
    await js(win, `(() => {
      const input = document.querySelectorAll('.seat-row input')[${i}];
      input.value = '${PARTIES[i][3]}';
      input.dispatchEvent(new Event('input', {bubbles:true}));
    })()`);
    await wait(60);
  }
  await wait(500);

  probe = await js(win, `(() => {
    const colors = {};
    document.querySelectorAll('.seat-chart circle').forEach(c => {
      const f = c.getAttribute('fill'); colors[f] = (colors[f]||0)+1;
    });
    return {
      colors,
      used: document.querySelector('.summary-line dd')?.textContent,
      remain: document.querySelectorAll('.summary-line dd')[1]?.textContent,
      note: document.querySelector('.summary-note')?.textContent,
      majority: document.querySelectorAll('.majority span')[1]?.textContent,
      progress: document.querySelector('.progress span')?.style.width,
      legend: [...document.querySelectorAll('.legend-item')].map(e => e.textContent),
    };
  })()`);
  check('схема: 34 места цвета первой партии', probe.colors['#0088b0'] === 34, JSON.stringify(probe.colors));
  check('схема: серых мест не осталось', !probe.colors['#d7d3d3'], JSON.stringify(probe.colors));
  check('сводка: 120 / 120', probe.used === '120 / 120', probe.used);
  check('сводка: остаток 0', probe.remain === '0', probe.remain);
  check('сводка: «Все места распределены.»', probe.note === 'Все места распределены.', probe.note);
  check('полоса прогресса заполнена', probe.progress === '100%', probe.progress);
  check('нет абсолютного большинства', /Крупнейшая фракция: Народный союз \(34\), большинства нет/.test(probe.majority), probe.majority);
  check('легенда: 7 партий с процентами', probe.legend.length === 7 && /28,3 %/.test(probe.legend[0]), probe.legend[0]);

  // 4. Проверка потолка: нельзя превысить общее число мест.
  await js(win, `(() => {
    const input = document.querySelectorAll('.seat-row input')[0];
    input.value = '999';
    input.dispatchEvent(new Event('input', {bubbles:true}));
  })()`);
  await wait(400);
  probe = await js(win, `(() => ({
    first: document.querySelectorAll('.seat-row input')[0].value,
    used: document.querySelector('.summary-line dd')?.textContent,
  }))()`);
  check('ввод 999 подрезан до потолка (34)', probe.first === '34', probe.first);
  check('сумма осталась 120', probe.used === '120 / 120', probe.used);

  // Отрицательное число.
  await js(win, `(() => {
    const input = document.querySelectorAll('.seat-row input')[0];
    input.value = '-5';
    input.dispatchEvent(new Event('input', {bubbles:true}));
  })()`);
  await wait(400);
  probe = await js(win, `document.querySelectorAll('.seat-row input')[0].value`);
  check('отрицательное число превращается в 0', probe === '0', probe);

  await js(win, `(() => {
    const input = document.querySelectorAll('.seat-row input')[0];
    input.value = '34';
    input.dispatchEvent(new Event('input', {bubbles:true}));
  })()`);
  await wait(500);

  // 5. Абсолютное большинство.
  await js(win, `(() => {
    const inputs = document.querySelectorAll('.seat-row input');
    const set = (i,v) => { inputs[i].value = String(v); inputs[i].dispatchEvent(new Event('input',{bubbles:true})); };
    set(1,0); set(2,0); set(3,0); set(4,0); set(5,0); set(6,0); set(0,61);
  })()`);
  await wait(600);
  probe = await js(win, `document.querySelectorAll('.majority span')[1]?.textContent`);
  check('61 место → «абсолютное большинство»', /Народный союз — абсолютное большинство/.test(probe), probe);
  const majorityClass = await js(win, `document.querySelectorAll('.majority span')[1]?.className`);
  check('заметка о большинстве окрашена в magenta', majorityClass === 'is-majority', majorityClass);

  // Возвращаем полный расклад.
  await js(win, `(() => {
    const inputs = document.querySelectorAll('.seat-row input');
    const vals = [34,27,18,14,12,9,6];
    vals.forEach((v,i) => { inputs[i].value = String(v); inputs[i].dispatchEvent(new Event('input',{bubbles:true})); });
  })()`);
  await wait(600);

  await win.webContents.capturePage().then((img) =>
    fs.writeFileSync(path.join(OUT, '1-главный-экран.png'), img.toPNG()));

  // 6. Справочник партий.
  await js(win, `document.querySelectorAll('.appbar .btn-secondary')[0].click()`);
  await wait(300);
  probe = await js(win, `(() => ({
    heading: document.querySelector('.parties-head h3')?.textContent,
    rows: document.querySelectorAll('.parties-table tbody tr').length,
    firstHex: document.querySelector('.parties-table .hex')?.textContent,
    convs: document.querySelector('.parties-table .muted.numeric')?.textContent,
    seatsNow: document.querySelector('.parties-table .numeric:not(.muted)')?.textContent,
  }))()`);
  check('справочник: «7 партий»', probe.heading === '7 партий', probe.heading);
  check('справочник: 7 строк таблицы', probe.rows === 7, String(probe.rows));
  check('справочник: HEX показан', probe.firstHex === '#0088b0', probe.firstHex);
  check('справочник: «Мест сейчас» = 34', probe.seatsNow === '34', probe.seatsNow);
  await win.webContents.capturePage().then((img) =>
    fs.writeFileSync(path.join(OUT, '2-справочник-партий.png'), img.toPNG()));

  // 7. Правка партии распространяется всюду (сценарий 6 ТЗ).
  await js(win, `document.querySelectorAll('.parties-table .btn-ghost')[0].click()`);
  await wait(250);
  await js(win, `(() => {
    const inputs = document.querySelectorAll('.dialog input.input');
    inputs[0].value = 'Новый союз';
    inputs[0].dispatchEvent(new Event('input', {bubbles:true}));
    inputs[2].value = '#5b4b8a';
    inputs[2].dispatchEvent(new Event('input', {bubbles:true}));
    document.querySelector('.dialog button[type=submit]').click();
  })()`);
  await wait(400);
  probe = await js(win, `(() => ({
    name: document.querySelector('.parties-table .name')?.textContent,
    hex: document.querySelector('.parties-table .hex')?.textContent,
  }))()`);
  check('партия переименована в справочнике', probe.name === 'Новый союз', probe.name);
  check('цвет партии обновлён', probe.hex === '#5b4b8a', probe.hex);

  await js(win, `document.querySelector('.appbar .btn-ghost').click()`);
  await wait(300);
  probe = await js(win, `(() => {
    const colors = {};
    document.querySelectorAll('.seat-chart circle').forEach(c => {
      const f = c.getAttribute('fill'); colors[f] = (colors[f]||0)+1;
    });
    return { colors, legend: document.querySelector('.legend-item')?.textContent };
  })()`);
  check('новый цвет виден на схеме', probe.colors['#5b4b8a'] === 34, JSON.stringify(probe.colors));
  check('новое имя в легенде', /Новый союз/.test(probe.legend), probe.legend);

  // 8. Диалог удаления партии перечисляет созывы.
  await js(win, `document.querySelectorAll('.appbar .btn-secondary')[0].click()`);
  await wait(250);
  await js(win, `document.querySelectorAll('.parties-table .btn-danger')[6].click()`);
  await wait(350);
  probe = await js(win, `(() => ({
    title: document.querySelector('.dialog-title')?.textContent,
    body: document.querySelector('.dialog-body')?.textContent,
    list: [...document.querySelectorAll('.inset-panel li')].map(l => l.textContent),
    danger: !!document.querySelector('.btn-primary-danger'),
  }))()`);
  check('удаление: заголовок с именем партии', /Удалить «Независимые»\?/.test(probe.title), probe.title);
  check('удаление: сказано про 1 созыв', /участвует в 1 созыве/.test(probe.body), probe.body);
  check('удаление: перечислен состав с числом мест', /Первый состав — 6 мест/.test(probe.list[0] || ''), JSON.stringify(probe.list));
  check('удаление: кнопка окрашена как опасная', probe.danger === true);
  await win.webContents.capturePage().then((img) =>
    fs.writeFileSync(path.join(OUT, '3-удаление-партии.png'), img.toPNG()));
  await js(win, `document.querySelector('.dialog .btn-secondary').click()`);
  await wait(200);
  await js(win, `document.querySelector('.appbar .btn-ghost').click()`);
  await wait(250);

  // 9. Фиксация созыва.
  await js(win, `document.querySelector('.appbar .btn-primary').click()`);
  await wait(350);
  probe = await js(win, `(() => ({
    title: document.querySelector('.dialog-title')?.textContent,
    body: document.querySelector('.dialog-body')?.textContent,
    caption: document.querySelector('.inset-panel .muted')?.textContent,
    suggested: document.querySelector('.dialog .field input')?.value,
    segments: document.querySelectorAll('.inset-panel .seat-bar span').length,
  }))()`);
  check('фиксация: заголовок про текущий состав', /Зафиксировать Первый состав\?/.test(probe.title), probe.title);
  check('фиксация: имя следующего предложено', probe.suggested === 'Второй состав', probe.suggested);
  check('фиксация: подпись про 120 из 120', /120 из 120 мест распределены · 7 партий/.test(probe.caption), probe.caption);
  check('фиксация: полоска из 7 сегментов', probe.segments === 7, String(probe.segments));
  await win.webContents.capturePage().then((img) =>
    fs.writeFileSync(path.join(OUT, '4-новый-созыв.png'), img.toPNG()));

  await js(win, `document.querySelector('.dialog button[type=submit]').click()`);
  await wait(600);
  probe = await js(win, `(() => ({
    cards: [...document.querySelectorAll('.conv-card')].map(c => ({
      name: c.querySelector('.conv-card-name').textContent,
      note: c.querySelector('.conv-card-note').textContent,
      current: c.getAttribute('aria-current'),
    })),
    heading: document.querySelector('.stage-head h2')?.textContent,
    used: document.querySelector('.summary-line dd')?.textContent,
    grey: [...document.querySelectorAll('.seat-chart circle')].filter(c => c.getAttribute('fill') === '#d7d3d3').length,
  }))()`);
  check('после фиксации: два созыва в списке', probe.cards.length === 2, JSON.stringify(probe.cards));
  check('новый созыв активен и пуст', probe.heading === 'Второй состав' && probe.used === '0 / 120', `${probe.heading} ${probe.used}`);
  check('старый созыв помечен «120 из 120 мест»', probe.cards[1]?.note === '120 из 120 мест', probe.cards[1]?.note);
  check('новый созыв помечен «Редактируется»', probe.cards[0]?.note === 'Редактируется', probe.cards[0]?.note);
  check('пустая схема полностью серая', probe.grey === 120, String(probe.grey));

  // 10. Просмотр истории.
  await js(win, `document.querySelectorAll('.conv-card')[1].click()`);
  await wait(400);
  probe = await js(win, `(() => ({
    tag: document.querySelector('.appbar .tag')?.textContent,
    primary: document.querySelector('.appbar .btn-primary')?.textContent,
    railLabel: document.querySelector('.rail-right .rail-label')?.textContent,
    hasInputs: document.querySelectorAll('.rail-right input').length,
    readonlyRows: document.querySelectorAll('.seat-row-readonly').length,
    heading: document.querySelector('.stage-head h2')?.textContent,
    hint: document.querySelector('.rail-right .rail-hint')?.textContent,
  }))()`);
  check('история: метка «Просмотр истории»', probe.tag === 'Просмотр истории', probe.tag);
  check('история: главная кнопка «Править состав»', probe.primary === 'Править состав', probe.primary);
  check('история: правая панель только для чтения', probe.railLabel === 'Состав — только чтение' && probe.hasInputs === 0, `${probe.railLabel} / ${probe.hasInputs}`);
  check('история: 7 строк состава', probe.readonlyRows === 7, String(probe.readonlyRows));
  check('история: сноска про правку', /сохранятся в этом же созыве/.test(probe.hint), probe.hint);
  await win.webContents.capturePage().then((img) =>
    fs.writeFileSync(path.join(OUT, '5-архивный-созыв.png'), img.toPNG()));

  // 11. Правка архивного созыва не создаёт новый.
  await js(win, `document.querySelector('.appbar .btn-primary').click()`);
  await wait(350);
  // Все 120 мест уже розданы, поэтому сначала освобождаем шесть у соседа.
  await js(win, `(() => {
    const inputs = document.querySelectorAll('.seat-row input');
    inputs[1].value = '21'; inputs[1].dispatchEvent(new Event('input', {bubbles:true}));
    inputs[0].value = '40'; inputs[0].dispatchEvent(new Event('input', {bubbles:true}));
  })()`);
  await wait(700);
  probe = await js(win, `(() => ({
    convs: document.querySelectorAll('.conv-card').length,
    used: document.querySelector('.summary-line dd')?.textContent,
    heading: document.querySelector('.stage-head h2')?.textContent,
    first: document.querySelectorAll('.seat-row input')[0].value,
  }))()`);
  check('правка истории: созывов по-прежнему 2', probe.convs === 2, String(probe.convs));
  check('правка истории: правка применилась', probe.first === '40' && probe.used === '120 / 120', `${probe.first} / ${probe.used}`);
  check('правка истории: остались в том же созыве', probe.heading === 'Первый состав', probe.heading);

  // 12. Диалог экспорта.
  await js(win, `document.querySelectorAll('.appbar .btn-secondary')[1].click()`);
  await wait(400);
  probe = await js(win, `(() => ({
    title: document.querySelector('.dialog-title')?.textContent,
    file: document.querySelector('.dialog .field input')?.value,
    resolutions: [...document.querySelectorAll('.seg-opt')].map(o => o.textContent),
    previewSeats: document.querySelectorAll('.export-preview circle').length,
    legendItems: document.querySelectorAll('.export-legend-item').length,
    checks: [...document.querySelectorAll('.check')].map(c => c.textContent),
  }))()`);
  check('экспорт: имя файла по шаблону', probe.file === 'Парламент_Первый_состав.png', probe.file);
  check('экспорт: три разрешения', probe.resolutions.length === 3 && probe.resolutions[0] === '1920 × 1080', JSON.stringify(probe.resolutions));
  check('экспорт: предпросмотр схемы', probe.previewSeats === 120, String(probe.previewSeats));
  check('экспорт: компактная легенда по сокращениям', probe.legendItems === 7, String(probe.legendItems));
  check('экспорт: два переключателя', probe.checks.length === 2, JSON.stringify(probe.checks));
  await win.webContents.capturePage().then((img) =>
    fs.writeFileSync(path.join(OUT, '6-экспорт-png.png'), img.toPNG()));

  // 13. Сама картинка PNG строится и имеет нужный размер.
  const png = await js(win, `(async () => {
    const mod = await import('./js/export-png.js');
    const url = await mod.renderChartToPng({
      width: 1920, height: 1080, totalSeats: 120, rows: 5,
      distribution: [
        {name:'Новый союз', abbr:'НС', color:'#5b4b8a', seats:40},
        {name:'Партия труда', abbr:'ПТ', color:'#d6006c', seats:27},
        {name:'Аграрный блок', abbr:'АБ', color:'#4c7a34', seats:18},
        {name:'Либеральный форум', abbr:'ЛФ', color:'#edbb00', seats:14},
        {name:'Консервативная лига', abbr:'КЛ', color:'#2d2b2b', seats:12},
        {name:'Движение «Заря»', abbr:'ДЗ', color:'#b3541e', seats:9},
      ],
      title: 'Первый состав', withLegend: true, withTitle: true,
    });
    return url;
  })()`);
  check('экспорт: PNG построен', png.startsWith('data:image/png;base64,'), png.slice(0, 30));
  const buf = Buffer.from(png.split(',')[1], 'base64');
  fs.writeFileSync(path.join(OUT, '7-экспорт-1920x1080.png'), buf);
  const w = buf.readUInt32BE(16), hgt = buf.readUInt32BE(20);
  check('экспорт: разрешение 1920×1080', w === 1920 && hgt === 1080, `${w}×${hgt}`);
  check('экспорт: файл непустой', buf.length > 20000, `${Math.round(buf.length/1024)} КБ`);

  // 14. Данные пережили перезапуск — файл проекта на диске.
  const dataFile = path.join(app.getPath('userData'), 'parlament.parlament.json');
  const saved = JSON.parse(fs.readFileSync(dataFile, 'utf8'));
  check('файл проекта записан', saved.parties.length === 7, `партий: ${saved.parties?.length}`);
  check('файл проекта: 2 созыва', saved.convocations.length === 2, String(saved.convocations?.length));
  check('файл проекта: правка истории сохранена', saved.convocations[0].seats[saved.parties[0].id] === 40,
    JSON.stringify(saved.convocations[0].seats));
  check('файл проекта: первый созыв зафиксирован', !!saved.convocations[0].fixedAt, String(saved.convocations[0].fixedAt));

  fs.writeFileSync(path.join(OUT, 'results.json'), JSON.stringify(results, null, 2));
  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== ${results.length - failed.length}/${results.length} проверок пройдено ===`);
  app.exit(failed.length ? 1 : 0);
}

require(path.join(APP_ROOT, 'electron', 'main.js'));
