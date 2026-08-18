'use strict';

/**
 * Мост к Python-бэкенду.
 *
 * Бэкенд запускается дочерним процессом, обмен — построчными JSON-сообщениями
 * через stdin/stdout (см. backend/parlament/rpc.py). Каждому запросу
 * присваивается номер; ответ находит свой промис по этому номеру, поэтому
 * запросы можно слать не дожидаясь предыдущих.
 */

const { spawn } = require('node:child_process');
const path = require('node:path');
const readline = require('node:readline');
const { EventEmitter } = require('node:events');

/** Сколько ждём ответа, прежде чем считать бэкенд зависшим. */
const REQUEST_TIMEOUT_MS = 15000;

class BackendError extends Error {
  constructor({ kind, message }) {
    super(message);
    this.name = 'BackendError';
    this.kind = kind || 'internal';
  }
}

class Backend extends EventEmitter {
  #child = null;
  #pending = new Map();
  #nextId = 1;
  #stopping = false;

  /**
   * @param {object} options
   * @param {string} options.dataFile путь к файлу проекта, открываемому при старте
   * @param {string} options.projectRoot корень репозитория (режим разработки)
   * @param {string} options.resourcesPath папка ресурсов собранного приложения
   * @param {boolean} options.isPackaged
   */
  constructor(options) {
    super();
    this.options = options;
  }

  /** Запускает процесс и ждёт от него сообщение `ready` с начальным состоянием. */
  start() {
    const { command, args } = this.#resolveCommand();
    this.#child = spawn(command, [...args, '--data-file', this.options.dataFile], {
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
      // Python по умолчанию буферизует stdout, когда пишет не в терминал —
      // без этого ответы копились бы в буфере и интерфейс замирал.
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
    });

    readline.createInterface({ input: this.#child.stdout }).on('line', (line) => {
      this.#onLine(line);
    });

    // stderr бэкенда — только диагностика; в протокол она не попадает.
    readline.createInterface({ input: this.#child.stderr }).on('line', (line) => {
      console.error('[backend]', line);
    });

    this.#child.on('error', (error) => {
      this.#failAll(new BackendError({
        kind: 'startup',
        message: `Не удалось запустить бэкенд (${command}): ${error.message}`,
      }));
      this.emit('crashed', error);
    });

    this.#child.on('exit', (code, signal) => {
      if (this.#stopping) return;
      const reason = signal ? `сигнал ${signal}` : `код ${code}`;
      this.#failAll(new BackendError({
        kind: 'crashed',
        message: `Бэкенд завершился неожиданно (${reason}).`,
      }));
      this.emit('crashed', new Error(reason));
    });

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new BackendError({ kind: 'startup', message: 'Бэкенд не ответил при запуске.' }));
      }, REQUEST_TIMEOUT_MS);

      this.once('ready', (state) => {
        clearTimeout(timer);
        resolve(state);
      });
      this.once('crashed', (error) => {
        clearTimeout(timer);
        reject(error);
      });
    });
  }

  /**
   * Вызывает метод бэкенда.
   * @returns {Promise<object>} результат метода
   */
  call(method, params = {}) {
    if (!this.#child || this.#child.exitCode !== null) {
      return Promise.reject(new BackendError({ kind: 'crashed', message: 'Бэкенд не запущен.' }));
    }

    const id = this.#nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        reject(new BackendError({ kind: 'timeout', message: `Бэкенд не ответил на «${method}».` }));
      }, REQUEST_TIMEOUT_MS);

      this.#pending.set(id, { resolve, reject, timer });
      this.#child.stdin.write(`${JSON.stringify({ id, method, params })}\n`, 'utf8');
    });
  }

  stop() {
    this.#stopping = true;
    if (this.#child && this.#child.exitCode === null) {
      // Закрытие stdin — штатный сигнал бэкенду завершить цикл чтения.
      this.#child.stdin.end();
      this.#child.kill();
    }
  }

  // -- внутреннее ---------------------------------------------------------

  #onLine(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      console.error('[backend] не JSON:', line);
      return;
    }

    if (message.event === 'ready') {
      this.emit('ready', message.state);
      return;
    }
    if (message.event === 'bootstrap-error') {
      this.emit('bootstrap-error', message.error);
      return;
    }

    const pending = this.#pending.get(message.id);
    if (!pending) return;
    this.#pending.delete(message.id);
    clearTimeout(pending.timer);

    if (message.ok) pending.resolve(message.result);
    else pending.reject(new BackendError(message.error || {}));
  }

  #failAll(error) {
    for (const { reject, timer } of this.#pending.values()) {
      clearTimeout(timer);
      reject(error);
    }
    this.#pending.clear();
  }

  /**
   * В собранном приложении бэкенд — отдельный exe рядом с ресурсами;
   * в разработке запускаем исходники системным Python.
   */
  #resolveCommand() {
    if (this.options.isPackaged) {
      const executable = process.platform === 'win32' ? 'parlament-backend.exe' : 'parlament-backend';
      return { command: path.join(this.options.resourcesPath, 'backend', executable), args: [] };
    }

    const entry = path.join(this.options.projectRoot, 'backend', 'main.py');
    const candidates = process.platform === 'win32'
      ? [process.env.PARLAMENT_PYTHON, 'python', 'py']
      : [process.env.PARLAMENT_PYTHON, 'python3', 'python'];

    for (const candidate of candidates) {
      if (candidate) return { command: candidate, args: [entry] };
    }
    return { command: 'python', args: [entry] };
  }
}

module.exports = { Backend, BackendError };
