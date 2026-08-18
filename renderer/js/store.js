/**
 * Состояние окна и вызовы бэкенда.
 *
 * Источник правды — бэкенд: каждая мутация возвращает состояние целиком, и
 * окно перерисовывается из него. Исключение — поля ввода мест: там правка
 * применяется сразу локально (схема обязана пересчитываться мгновенно), а на
 * бэкенд уходит с небольшой задержкой. Если бэкенд правку отклонит,
 * возвращаемся к его версии.
 */

import { EMPTY_SEAT_COLOR } from './seat-chart.js';

/** Пауза перед отправкой набранного числа мест на бэкенд. */
const SEATS_FLUSH_DELAY_MS = 250;

export class Store extends EventTarget {
  /** @type {object|null} состояние проекта с бэкенда */
  state = null;

  /** Что показываем: 'parliament' | 'parties' */
  view = 'parliament';

  /** Какой созыв открыт на главном экране. */
  selectedConvocationId = null;

  /** Открытый диалог: null или { kind, ...данные }. */
  dialog = null;

  /** Короткое уведомление внизу экрана. */
  toast = null;

  /** Локальные, ещё не отправленные правки мест: convocationId → partyId → n. */
  #drafts = new Map();
  #flushTimer = null;
  #toastTimer = null;

  constructor(bridge) {
    super();
    this.bridge = bridge;
  }

  // -- подписка -----------------------------------------------------------

  onChange(handler) {
    this.addEventListener('change', handler);
  }

  emit() {
    this.dispatchEvent(new Event('change'));
  }

  // -- вызовы бэкенда ------------------------------------------------------

  /**
   * Вызывает метод бэкенда и применяет пришедшее состояние.
   * Ошибку показывает уведомлением и возвращает null — вызывающему коду
   * почти всегда достаточно знать, получилось или нет.
   *
   * @returns {Promise<object|null>} результат метода
   */
  async call(method, params, { silent = false } = {}) {
    const response = await this.bridge.call(method, params);

    if (!response.ok) {
      if (!silent) this.showToast(response.error.message, 'error');
      return null;
    }
    if (response.result && response.result.state) {
      this.applyState(response.result.state);
    }
    return response.result;
  }

  applyState(state) {
    this.state = state;

    // Выбранный созыв мог исчезнуть (открыли другой проект) — возвращаемся
    // к активному.
    const exists = state.convocations.some((c) => c.id === this.selectedConvocationId);
    if (!exists) this.selectedConvocationId = state.activeConvocationId;

    this.emit();
  }

  // -- выборки -------------------------------------------------------------

  get parties() {
    return this.state ? this.state.parties : [];
  }

  get convocations() {
    // Новейшие сверху — как в макете.
    return this.state ? [...this.state.convocations].reverse() : [];
  }

  get totalSeats() {
    return this.state ? this.state.totalSeats : 120;
  }

  /** Сколько мест даёт большинство: половина плюс одно. */
  get majoritySeats() {
    return Math.floor(this.totalSeats / 2) + 1;
  }

  get selectedConvocation() {
    if (!this.state) return null;
    return this.state.convocations.find((c) => c.id === this.selectedConvocationId)
      || this.state.convocations.find((c) => c.id === this.state.activeConvocationId)
      || null;
  }

  /** Id архивного созыва, который пользователь открыл на правку. */
  editingArchived = null;

  /** Открытый созыв правится всегда; архивный — только после «Править состав». */
  get isEditable() {
    const conv = this.selectedConvocation;
    if (!conv) return false;
    return !conv.fixedAt || this.editingArchived === conv.id;
  }

  /**
   * Распределение мест выбранного созыва с учётом локальных правок.
   * @returns {Record<string, number>}
   */
  seatsOf(convocationId) {
    const conv = this.state?.convocations.find((c) => c.id === convocationId);
    if (!conv) return {};
    const draft = this.#drafts.get(convocationId);
    return draft ? { ...conv.seats, ...draft } : { ...conv.seats };
  }

  /** Партии выбранного созыва с числом мест — в порядке справочника. */
  rowsForConvocation(convocationId) {
    const seats = this.seatsOf(convocationId);
    return this.parties.map((party) => ({ ...party, seats: seats[party.id] || 0 }));
  }

  /**
   * Данные для схемы и легенды: только партии с местами, по убыванию.
   * Крупнейшая фракция оказывается у левого края — классический вид.
   */
  distributionOf(convocationId) {
    return this.rowsForConvocation(convocationId)
      .filter((party) => party.seats > 0)
      .sort((a, b) => b.seats - a.seats);
  }

  /** Сводка: занято, остаток, крупнейшая фракция, есть ли большинство. */
  summaryOf(convocationId) {
    const distribution = this.distributionOf(convocationId);
    const used = distribution.reduce((sum, party) => sum + party.seats, 0);
    const largest = distribution[0] || null;
    return {
      distribution,
      used,
      remaining: this.totalSeats - used,
      largest,
      hasMajority: Boolean(largest && largest.seats >= this.majoritySeats),
    };
  }

  /** Сегменты полоски в карточке созыва: партии плюс серый хвост остатка. */
  barSegmentsOf(convocationId) {
    const { distribution, remaining } = this.summaryOf(convocationId);
    const segments = distribution.map((party) => ({ color: party.color, flex: party.seats }));
    if (remaining > 0) segments.push({ color: EMPTY_SEAT_COLOR, flex: remaining });
    return segments;
  }

  /** В скольких созывах партия имеет места — для справочника и удаления. */
  convocationCountFor(partyId) {
    if (!this.state) return 0;
    return this.state.convocations.filter((c) => (c.seats[partyId] || 0) > 0).length;
  }

  // -- правка мест ---------------------------------------------------------

  /**
   * Ставит партии число мест. Правка применяется сразу (схема пересчитывается
   * мгновенно), на бэкенд уходит с задержкой.
   *
   * @returns {number} фактически применённое число — с учётом потолка
   */
  setSeats(convocationId, partyId, requested) {
    const seats = this.seatsOf(convocationId);
    const others = Object.entries(seats)
      .filter(([id]) => id !== partyId)
      .reduce((sum, [, n]) => sum + n, 0);

    // Тот же потолок, что и на бэкенде: сумма не может превысить общее число
    // мест. Правку не отклоняем, а подрезаем — так поле ведёт себя предсказуемо.
    const ceiling = Math.max(0, this.totalSeats - others);
    const value = Math.min(Math.max(0, Math.floor(Number(requested) || 0)), ceiling);

    const draft = this.#drafts.get(convocationId) || {};
    draft[partyId] = value;
    this.#drafts.set(convocationId, draft);

    this.emit();
    this.#scheduleFlush();
    return value;
  }

  #scheduleFlush() {
    clearTimeout(this.#flushTimer);
    this.#flushTimer = setTimeout(() => this.flushSeats(), SEATS_FLUSH_DELAY_MS);
  }

  /** Отправляет накопленные правки мест на бэкенд. */
  async flushSeats() {
    clearTimeout(this.#flushTimer);
    if (this.#drafts.size === 0) return;

    const pending = [...this.#drafts.entries()];
    this.#drafts.clear();

    for (const [convocationId, draft] of pending) {
      const conv = this.state?.convocations.find((c) => c.id === convocationId);
      if (!conv) continue;

      const seats = { ...conv.seats, ...draft };
      const response = await this.bridge.call('seats.setAll', { convocationId, seats });
      if (!response.ok) {
        this.showToast(response.error.message, 'error');
        this.emit();   // откатываемся к состоянию бэкенда
        return;
      }
      this.applyState(response.result.state);
    }
  }

  // -- диалоги и уведомления -----------------------------------------------

  openDialog(dialog) {
    this.dialog = dialog;
    this.emit();
  }

  closeDialog() {
    this.dialog = null;
    this.emit();
  }

  setView(view) {
    this.view = view;
    this.emit();
  }

  selectConvocation(id) {
    this.flushSeats();
    this.selectedConvocationId = id;
    this.editingArchived = null;
    this.emit();
  }

  /** @param {'info'|'error'|'success'} tone */
  showToast(message, tone = 'info', action = null) {
    clearTimeout(this.#toastTimer);
    this.toast = { message, tone, action };
    this.emit();
    this.#toastTimer = setTimeout(() => {
      this.toast = null;
      this.emit();
    }, tone === 'error' ? 7000 : 4500);
  }

  dismissToast() {
    clearTimeout(this.#toastTimer);
    this.toast = null;
    this.emit();
  }
}
