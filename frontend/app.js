const HOLD_SECONDS = 120;
const DISPLAY_NAME_KEY = 'seatAllocation.displayName';
const CLIENT_TOKEN_KEY = 'seatAllocation.clientToken';
const GUEST_CLIENT_TOKEN_KEY = 'seatAllocation.guestClientToken';
const ACTIVE_HOLD_KEY = 'seatAllocation.activeHold';

const elements = Object.fromEntries([
  'connection-status', 'connection-label', 'edit-name', 'visitor-initial', 'visitor-label',
  'session-id', 'seat-map', 'map-instructions', 'map-message', 'map-version', 'group-size',
  'suggest-seats', 'selected-list', 'seat-count', 'hold-block', 'hold-title', 'hold-caption',
  'countdown', 'countdown-progress', 'hold-seats', 'confirm-booking', 'release-hold',
  'booking-result', 'name-dialog', 'name-form', 'display-name', 'clear-name', 'toast-region',
  'held-seat-timers', 'demo-profile', 'show-seats', 'show-bookings', 'seats-page',
  'my-bookings-page', 'bookings-list', 'back-to-seats',
  'event-type-label', 'event-title', 'event-venue', 'event-starts-at',
].map((id) => [id.replaceAll('-', '_'), document.getElementById(id)]));

let visitorName = sessionStorage.getItem(DISPLAY_NAME_KEY) || '';
let guestClientToken = sessionStorage.getItem(GUEST_CLIENT_TOKEN_KEY) || sessionStorage.getItem(CLIENT_TOKEN_KEY);
if (!guestClientToken) {
  guestClientToken = crypto.randomUUID();
  sessionStorage.setItem(GUEST_CLIENT_TOKEN_KEY, guestClientToken);
}
let clientToken = sessionStorage.getItem(CLIENT_TOKEN_KEY) || guestClientToken;
let activePage = 'seats';
const DEMO_PROFILE_KEY = 'seatAllocation.demoProfile';
const savedDemoProfile = sessionStorage.getItem(DEMO_PROFILE_KEY) || '';
if (['demo-avery', 'demo-blair', 'demo-casey'].includes(savedDemoProfile)) {
  clientToken = savedDemoProfile;
  sessionStorage.setItem(CLIENT_TOKEN_KEY, clientToken);
}

let sessionId = elements.session_id.value.trim() || 'SESSION-001';
let seatMap = null;
let mapRequestSequence = 0;
let selectedSeatIds = new Set();
let activeHold = readActiveHold(clientToken);
let eventSource = null;
let pollingTimer = null;
let serverClock = { epoch: Date.now(), monotonic: performance.now() };
let focusedSeatId = null;
let suggestionTimer = null;

function readActiveHold(token) {
  try {
    const key = ACTIVE_HOLD_KEY + '.' + token;
    const saved = sessionStorage.getItem(key) || (token === guestClientToken ? sessionStorage.getItem(ACTIVE_HOLD_KEY) : null);
    return JSON.parse(saved || 'null');
  } catch {
    return null;
  }
}

function persistActiveHold() {
  const key = ACTIVE_HOLD_KEY + '.' + clientToken;
  if (activeHold) sessionStorage.setItem(key, JSON.stringify(activeHold));
  else sessionStorage.removeItem(key);
  if (clientToken === guestClientToken) {
    if (activeHold) sessionStorage.setItem(ACTIVE_HOLD_KEY, JSON.stringify(activeHold));
    else sessionStorage.removeItem(ACTIVE_HOLD_KEY);
  }
}

function announce(message) {
  elements.toast_region.textContent = '';
  window.setTimeout(() => { elements.toast_region.textContent = message; }, 30);
}

function showMessage(message, kind) {
  elements.map_message.textContent = message || '';
  elements.map_message.className = 'inline-message' + (kind ? ' ' + kind : '');
  if (message) announce(message);
}

function setConnection(state, label) {
  elements.connection_status.className = 'connection ' + state;
  elements.connection_label.textContent = label;
}

function setServerClock(response) {
  const headerDate = response.headers.get('Date');
  if (headerDate) {
    const parsed = Date.parse(headerDate);
    if (Number.isFinite(parsed)) serverClock = { epoch: parsed, monotonic: performance.now() };
  }
}

function serverNow() {
  return serverClock.epoch + (performance.now() - serverClock.monotonic);
}

function formatTime(milliseconds) {
  const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000));
  return String(Math.floor(totalSeconds / 60)).padStart(2, '0') + ':' + String(totalSeconds % 60).padStart(2, '0');
}

function safeLabel(value) {
  return String(value == null ? '' : value).replace(/[<>]/g, '');
}

async function apiRequest(path, options) {
  options = options || {};
  const headers = new Headers(options.headers || {});
  headers.set('Accept', 'application/json');
  headers.set('X-Client-Token', clientToken);
  if (options.body !== undefined) headers.set('Content-Type', 'application/json');
  const response = await fetch('/api/v1' + path, {
    ...options,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  setServerClock(response);
  const requestId = response.headers.get('X-Request-Id') || '';
  let payload = null;
  if (response.status !== 204) {
    const text = await response.text();
    if (text) {
      try { payload = JSON.parse(text); }
      catch { throw new Error('The server returned an unreadable response (' + response.status + ').'); }
    }
  }
  if (!response.ok) {
    const error = payload && payload.error ? payload.error : {};
    const failure = new Error(error.message || 'Request failed (' + response.status + ').');
    failure.status = response.status;
    failure.code = error.code;
    failure.details = error.details;
    failure.requestId = (payload && payload.requestId) || requestId;
    throw failure;
  }
  return { payload, response };
}

function mapData(payload) {
  return (payload && payload.data) || payload;
}

function canonicalSeatState(seat) {
  if (seat.state === 'held') return seat.ownership === 'self' ? 'self-held' : 'other-held';
  if (seat.state === 'booked') return seat.ownership === 'self' ? 'booked-self' : 'booked';
  if (seat.state === 'blocked') return seat.state;
  return 'available';
}

function displaySeatId(seat) {
  return seat.label || (String(seat.row) + '-' + String(seat.number).padStart(2, '0'));
}

function sortSeats(a, b) {
  return String(a.row).localeCompare(String(b.row), undefined, { numeric: true })
    || Number(a.number) - Number(b.number)
    || String(a.id).localeCompare(String(b.id));
}

function seatStateText(seat, state) {
  const labels = {
    available: 'available',
    'self-held': 'held by you',
    'other-held': 'held by someone else',
    booked: 'booked',
    'booked-self': 'booked by you',
    blocked: 'unavailable',
  };
  const remaining = seat.heldUntil ? Date.parse(seat.heldUntil) - serverNow() : null;
  const timer = state === 'other-held' && Number.isFinite(remaining) ? ', hold expires in ' + formatTime(remaining) : '';
  return labels[state] + timer;
}

function renderMap() {
  if (!seatMap || !seatMap.seats) return;
  const seats = [...seatMap.seats].sort(sortSeats);
  const rows = new Map();
  for (const seat of seats) {
    const key = String(seat.row == null ? seat.id : seat.row);
    if (!rows.has(key)) rows.set(key, []);
    rows.get(key).push(seat);
  }
  elements.seat_map.replaceChildren();
  for (const [rowName, rowSeats] of rows) {
    const row = document.createElement('div');
    row.className = 'seat-row';
    row.style.setProperty('--seat-count', rowSeats.length);
    row.setAttribute('role', 'group');
    row.setAttribute('aria-label', 'Row ' + rowName);
    const makeRowLabel = () => {
      const label = document.createElement('span');
      label.className = 'row-label';
      label.setAttribute('aria-hidden', 'true');
      label.textContent = rowName;
      return label;
    };
    row.append(makeRowLabel());
    rowSeats.forEach((seat) => {
      const state = canonicalSeatState(seat);
      const isSelected = selectedSeatIds.has(seat.id);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'seat ' + (isSelected ? 'selected' : state);
      button.dataset.seatId = seat.id;
      button.textContent = String(seat.number == null ? (seat.label || seat.id) : seat.number);
      button.setAttribute('aria-label', 'Row ' + safeLabel(seat.row) + ', seat ' + safeLabel(seat.number == null ? (seat.label || seat.id) : seat.number) + ', ' + (isSelected ? 'selected' : seatStateText(seat, state)));
      button.setAttribute('aria-pressed', String(isSelected));
      button.disabled = !(state === 'available' || isSelected) || Boolean(activeHold);
      button.tabIndex = seat.id === focusedSeatId ? 0 : -1;
      button.addEventListener('click', () => toggleSeat(seat));
      button.addEventListener('keydown', handleSeatNavigation);
      row.append(button);
    });
    row.append(makeRowLabel());
    elements.seat_map.append(row);
  }
  const seatsWithFocus = elements.seat_map.querySelectorAll('.seat:not(:disabled)');
  if (seatsWithFocus.length && !focusedSeatId) {
    focusedSeatId = seatsWithFocus[0].dataset.seatId;
    seatsWithFocus[0].tabIndex = 0;
  }
  updateSelection();
}

function handleSeatNavigation(event) {
  const directions = {
    ArrowRight: [0, 1], ArrowLeft: [0, -1], ArrowDown: [1, 0], ArrowUp: [-1, 0],
  };
  const direction = directions[event.key];
  if (!direction) return;
  event.preventDefault();
  const buttons = [...elements.seat_map.querySelectorAll('.seat:not(:disabled)')];
  if (!buttons.length) return;
  const current = buttons.indexOf(event.currentTarget);
  const delta = direction[0] ? direction[0] * countSeatsInRow(event.currentTarget) : direction[1];
  const next = buttons[Math.min(buttons.length - 1, Math.max(0, current + delta))];
  if (next) {
    focusedSeatId = next.dataset.seatId;
    buttons.forEach((button) => { button.tabIndex = button === next ? 0 : -1; });
    next.focus();
  }
}

function countSeatsInRow(button) {
  return button.closest('.seat-row') ? button.closest('.seat-row').querySelectorAll('.seat:not(:disabled)').length : 1;
}

function toggleSeat(seat) {
  if (activeHold) return;
  const state = canonicalSeatState(seat);
  if (state !== 'available' && !selectedSeatIds.has(seat.id)) return;
  if (selectedSeatIds.has(seat.id)) selectedSeatIds.delete(seat.id);
  else selectedSeatIds.add(seat.id);
  focusedSeatId = seat.id;
  showMessage('');
  renderMap();
}

function updateSelection() {
  const seats = seatMap && seatMap.seats ? seatMap.seats : [];
  const selected = [...selectedSeatIds].map((id) => seats.find((seat) => seat.id === id)).filter(Boolean).sort(sortSeats);
  elements.seat_count.textContent = String(selected.length);
  elements.selected_list.replaceChildren();
  if (!selected.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-selection';
    empty.textContent = activeHold ? 'Your held seats are shown above.' : 'Choose seats from the map';
    elements.selected_list.append(empty);
  } else {
    for (const seat of selected) {
      const chip = document.createElement('span');
      chip.className = 'selected-chip';
      chip.append(document.createTextNode(displaySeatId(seat)));
      if (!activeHold) {
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.setAttribute('aria-label', 'Remove ' + displaySeatId(seat));
        remove.textContent = '×';
        remove.addEventListener('click', () => toggleSeat(seat));
        chip.append(remove);
      }
      elements.selected_list.append(chip);
    }
  }
  elements.hold_seats.disabled = selected.length === 0 || Boolean(activeHold);
}

function availableSeats() {
  return (seatMap && seatMap.seats ? seatMap.seats : []).filter((seat) => canonicalSeatState(seat) === 'available').sort(sortSeats);
}

function suggestContiguousSeats(count) {
  const groups = new Map();
  for (const seat of availableSeats()) {
    const rowKey = [seat.section || '', seat.row || '', seat.price == null ? '' : seat.price].join('|');
    if (!groups.has(rowKey)) groups.set(rowKey, []);
    groups.get(rowKey).push(seat);
  }
  for (const rowSeats of groups.values()) {
    rowSeats.sort((a, b) => Number(a.number) - Number(b.number));
    for (let start = 0; start <= rowSeats.length - count; start += 1) {
      const group = rowSeats.slice(start, start + count);
      const contiguous = group.every((seat, index) => index === 0 || Number(seat.number) === Number(group[index - 1].number) + 1);
      if (contiguous) return group;
    }
  }
  return null;
}

function suggestSeats() {
  if (!seatMap || activeHold) return;
  const count = Number(elements.group_size.value);
  const group = suggestContiguousSeats(count);
  if (!group) {
    showMessage('We could not find ' + count + ' seats together. Try a smaller group.', 'error');
    return;
  }
  selectedSeatIds = new Set(group.map((seat) => seat.id));
  focusedSeatId = group[0].id;
  showMessage(count + ' seats together suggested in row ' + group[0].row + '.', 'success');
  renderMap();
  const first = [...elements.seat_map.querySelectorAll('.seat')].find((button) => button.dataset.seatId === group[0].id);
  if (first) first.focus();
}

async function loadSeatMap(options) {
  options = options || {};
  const requestSequence = ++mapRequestSequence;
  if (!options.quiet) {
    const loading = document.createElement('div');
    loading.className = 'loading-map';
    loading.textContent = 'Finding your seats…';
    elements.seat_map.replaceChildren(loading);
  }
  try {
    const result = await apiRequest('/sessions/' + encodeURIComponent(sessionId) + '/seats');
    if (requestSequence !== mapRequestSequence) return;
    const data = mapData(result.payload);
    if (!data || !Array.isArray(data.seats)) throw new Error('The seat map response is missing its seats.');
    seatMap = data;
    if (data.title) elements.event_title.textContent = data.title;
    if (data.eventType) elements.event_type_label.textContent = data.eventType === 'concert' ? 'LIVE EVENT' : 'MOVIE SCREENING';
    if (data.venue) elements.event_venue.textContent = data.venue;
    if (data.startsAt) {
      const startsAt = new Date(data.startsAt);
      elements.event_starts_at.textContent = Number.isNaN(startsAt.valueOf()) ? data.startsAt : startsAt.toLocaleString();
    }
    const serverNowValue = data.serverNow ? Date.parse(data.serverNow) : NaN;
    if (Number.isFinite(serverNowValue)) serverClock = { epoch: serverNowValue, monotonic: performance.now() };
    else setServerClock(result.response);
    selectedSeatIds = new Set([...selectedSeatIds].filter((id) => data.seats.some((seat) => seat.id === id && canonicalSeatState(seat) === 'available')));
    elements.map_version.textContent = 'Seat map · v' + (data.version == null ? '—' : data.version);
    renderMap();
    updateHoldUi();
    if (!options.quiet) showMessage('');
    setConnection('connected', 'Live seat map');
  } catch (error) {
    if (requestSequence !== mapRequestSequence) return;
    setConnection('disconnected', 'Trying to reconnect');
    elements.seat_map.replaceChildren();
    const container = document.createElement('div');
    container.className = 'loading-map';
    container.textContent = error.message + ' ';
    const retry = document.createElement('button');
    retry.className = 'text-button';
    retry.type = 'button';
    retry.textContent = 'Retry';
    retry.addEventListener('click', () => loadSeatMap());
    container.append(retry);
    elements.seat_map.append(container);
    showMessage(error.requestId ? error.message + ' Reference: ' + error.requestId : error.message, 'error');
  }
}

function connectToEvents() {
  if (eventSource) eventSource.close();
  if (pollingTimer) window.clearInterval(pollingTimer);
  if (!('EventSource' in window)) {
    setConnection('connected', 'Seat map refreshes periodically');
    pollingTimer = window.setInterval(() => loadSeatMap({ quiet: true }), 15000);
    return;
  }
  const source = new EventSource('/api/v1/sessions/' + encodeURIComponent(sessionId) + '/seat-events');
  eventSource = source;
  source.onopen = () => {
    setConnection('connected', 'Live seat map');
    loadSeatMap({ quiet: true });
  };
  source.onmessage = handleSeatEvent;
  source.addEventListener('seat.state_changed', handleSeatEvent);
  source.onerror = () => setConnection('disconnected', 'Reconnecting to live map');
}

function showPage(page) {
  activePage = page;
  const showSeats = page === 'seats';
  elements.seats_page.hidden = !showSeats;
  elements.my_bookings_page.hidden = showSeats;
  elements.show_seats.classList.toggle('active', showSeats);
  elements.show_bookings.classList.toggle('active', !showSeats);
  if (showSeats) {
    elements.show_seats.setAttribute('aria-current', 'page');
    elements.show_bookings.removeAttribute('aria-current');
  } else {
    elements.show_bookings.setAttribute('aria-current', 'page');
    elements.show_seats.removeAttribute('aria-current');
    loadBookings();
  }
}

async function loadBookings() {
  elements.bookings_list.replaceChildren();
  const loading = document.createElement('p');
  loading.className = 'loading-map';
  loading.textContent = 'Loading your bookings…';
  elements.bookings_list.append(loading);
  try {
    const result = await apiRequest('/bookings');
    const bookings = mapData(result.payload).bookings || [];
    elements.bookings_list.replaceChildren();
    if (!bookings.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-bookings panel';
      const title = document.createElement('h2');
      title.textContent = 'No confirmed bookings yet';
      const text = document.createElement('p');
      text.textContent = 'When you confirm a reservation, you’ll find it here.';
      const browse = document.createElement('button');
      browse.className = 'primary-button';
      browse.type = 'button';
      browse.textContent = 'Browse seats →';
      browse.addEventListener('click', () => showPage('seats'));
      empty.append(title, text, browse);
      elements.bookings_list.append(empty);
      return;
    }
    for (const booking of bookings) {
      const card = document.createElement('article');
      card.className = 'booking-card panel';
      const top = document.createElement('div');
      top.className = 'booking-card-top';
      const heading = document.createElement('div');
      const title = document.createElement('h2');
      title.textContent = booking.title || 'Seat reservation';
      const venue = document.createElement('p');
      venue.textContent = booking.venue || booking.sessionId;
      heading.append(title, venue);
      const badge = document.createElement('span');
      badge.className = 'booking-confirmed';
      badge.textContent = 'Confirmed';
      top.append(heading, badge);
      const details = document.createElement('div');
      details.className = 'booking-card-details';
      const seats = document.createElement('span');
      seats.textContent = booking.seatIds.length + (booking.seatIds.length === 1 ? ' seat' : ' seats') + ': ' + booking.seatIds.join(', ');
      const date = document.createElement('span');
      const startsAt = booking.startsAt ? new Date(booking.startsAt) : null;
      date.textContent = startsAt && !Number.isNaN(startsAt.valueOf()) ? startsAt.toLocaleString() : booking.sessionId;
      details.append(seats, date);
      const reference = document.createElement('small');
      reference.textContent = 'Booking ' + booking.bookingId;
      card.append(top, details, reference);
      elements.bookings_list.append(card);
    }
  } catch (error) {
    elements.bookings_list.replaceChildren();
    const message = document.createElement('p');
    message.className = 'inline-message error';
    message.textContent = error.message || 'Could not load your bookings.';
    elements.bookings_list.append(message);
  }
}

function changeDemoProfile() {
  if (activeHold) {
    elements.demo_profile.value = sessionStorage.getItem(DEMO_PROFILE_KEY) || '';
    showMessage('Release or confirm this visitor’s hold before switching demo profiles.', 'warning');
    return;
  }
  const profile = elements.demo_profile.value;
  const allowed = ['', 'demo-avery', 'demo-blair', 'demo-casey'];
  if (!allowed.includes(profile)) return;
  sessionStorage.setItem(DEMO_PROFILE_KEY, profile);
  clientToken = profile || guestClientToken;
  sessionStorage.setItem(CLIENT_TOKEN_KEY, clientToken);
  activeHold = readActiveHold(clientToken);
  selectedSeatIds.clear();
  elements.booking_result.hidden = true;
  connectToEvents();
  loadSeatMap();
  if (activePage === 'bookings') loadBookings();
}

function handleSeatEvent(event) {
  let update;
  try { update = JSON.parse(event.data); }
  catch { loadSeatMap({ quiet: true }); return; }
  if (update.sessionId && update.sessionId !== sessionId) return;
  const currentVersion = Number(seatMap && seatMap.version != null ? seatMap.version : 0);
  const nextVersion = Number(update.version || 0);
  if (nextVersion && nextVersion <= currentVersion) return;
  if (nextVersion && currentVersion && nextVersion !== currentVersion + 1) {
    showMessage('Seat updates were missed. Refreshing the latest map.', 'warning');
  }
  loadSeatMap({ quiet: true });
}

function updateVisitorUi() {
  elements.visitor_label.textContent = visitorName || 'Add your name';
  elements.visitor_initial.textContent = visitorName ? visitorName.trim().charAt(0).toUpperCase() : '?';
}

function openNameDialog() {
  elements.display_name.value = visitorName;
  if (!elements.name_dialog.open) elements.name_dialog.showModal();
  window.setTimeout(() => elements.display_name.focus(), 0);
}

function saveName(event) {
  event.preventDefault();
  const name = elements.display_name.value.trim();
  if (!name) {
    elements.display_name.setCustomValidity('Enter a name to continue.');
    elements.display_name.reportValidity();
    return;
  }
  elements.display_name.setCustomValidity('');
  visitorName = name.slice(0, 48);
  sessionStorage.setItem(DISPLAY_NAME_KEY, visitorName);
  updateVisitorUi();
  elements.name_dialog.close();
}

function clearName() {
  visitorName = '';
  sessionStorage.removeItem(DISPLAY_NAME_KEY);
  updateVisitorUi();
  elements.display_name.value = '';
  elements.display_name.focus();
}

function startHoldClock() {
  if (suggestionTimer) window.clearInterval(suggestionTimer);
  suggestionTimer = window.setInterval(() => {
    renderOtherHoldTimers();
    if (!activeHold) return;
    const remaining = Date.parse(activeHold.expiresAt) - serverNow();
    elements.countdown.textContent = formatTime(remaining);
    elements.countdown_progress.style.transform = 'scaleX(' + Math.max(0, remaining / (HOLD_SECONDS * 1000)) + ')';
    if (remaining <= 0) {
      activeHold = null;
      selectedSeatIds.clear();
      persistActiveHold();
      elements.hold_title.textContent = 'Your hold has expired';
      elements.hold_caption.textContent = 'Refreshing availability…';
      elements.confirm_booking.hidden = true;
      elements.release_hold.hidden = true;
      elements.hold_seats.hidden = false;
      loadSeatMap({ quiet: true }).then(() => {
        elements.hold_block.hidden = true;
        showMessage('Your hold expired. The latest seat availability is shown.', 'warning');
      });
    }
  }, 250);
}

function renderOtherHoldTimers() {
  const heldSeats = (seatMap && seatMap.seats ? seatMap.seats : [])
    .filter((seat) => canonicalSeatState(seat) === 'other-held' && seat.heldUntil)
    .sort(sortSeats);
  elements.held_seat_timers.replaceChildren();
  if (!heldSeats.length) return;
  const label = document.createElement('span');
  label.className = 'held-timers-label';
  label.textContent = 'Other active holds';
  elements.held_seat_timers.append(label);
  for (const seat of heldSeats) {
    const pill = document.createElement('span');
    pill.className = 'held-timer-chip';
    pill.textContent = displaySeatId(seat) + ' · ' + formatTime(Date.parse(seat.heldUntil) - serverNow()) + ' left';
    elements.held_seat_timers.append(pill);
  }
}

function updateHoldUi() {
  const hasHold = Boolean(activeHold);
  elements.hold_block.hidden = !hasHold;
  elements.confirm_booking.hidden = !hasHold;
  elements.release_hold.hidden = !hasHold;
  elements.hold_seats.hidden = hasHold;
  elements.hold_seats.disabled = selectedSeatIds.size === 0 || hasHold;
  if (hasHold) {
    elements.hold_title.textContent = 'Your seats are on hold';
    elements.hold_caption.textContent = activeHold.seatIds.map(safeLabel).join(', ') + ' · finish before time runs out';
    selectedSeatIds = new Set(activeHold.seatIds);
  }
  updateSelection();
}

function isConflict(error) {
  return error.status === 409 || error.code === 'SEAT_UNAVAILABLE';
}

async function createHold() {
  if (selectedSeatIds.size === 0 || activeHold) return;
  elements.hold_seats.disabled = true;
  elements.hold_seats.querySelector('span:first-child').textContent = 'Holding seats…';
  showMessage('');
  try {
    const seatIds = [...selectedSeatIds];
    const idempotencyKey = crypto.randomUUID();
    const result = await apiRequest('/sessions/' + encodeURIComponent(sessionId) + '/holds', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: { seatIds, clientToken },
    });
    const data = mapData(result.payload);
    if (!data || !data.holdId || !data.expiresAt) throw new Error('The server did not return a hold and expiry.');
    activeHold = { holdId: data.holdId, seatIds: data.seatIds || seatIds, expiresAt: data.expiresAt, confirmKey: crypto.randomUUID() };
    selectedSeatIds = new Set(activeHold.seatIds);
    persistActiveHold();
    updateHoldUi();
    renderMap();
    showMessage('Seats ' + activeHold.seatIds.join(', ') + ' are held for 2 minutes.', 'success');
    await loadSeatMap({ quiet: true });
  } catch (error) {
    if (isConflict(error)) {
      selectedSeatIds.clear();
      await loadSeatMap({ quiet: true });
      showMessage(error.message + ' The seat map has been refreshed.', 'error');
    } else {
      showMessage(error.requestId ? error.message + ' Reference: ' + error.requestId : error.message, 'error');
    }
  } finally {
    elements.hold_seats.querySelector('span:first-child').textContent = 'Hold my seats';
    updateHoldUi();
  }
}

async function releaseHold() {
  if (!activeHold) return;
  elements.release_hold.disabled = true;
  try {
    await apiRequest('/holds/' + encodeURIComponent(activeHold.holdId), { method: 'DELETE' });
    activeHold = null;
    selectedSeatIds.clear();
    persistActiveHold();
    updateHoldUi();
    await loadSeatMap({ quiet: true });
    showMessage('Your seats have been released.', 'success');
  } catch (error) {
    showMessage(error.requestId ? error.message + ' Reference: ' + error.requestId : error.message, 'error');
    await loadSeatMap({ quiet: true });
  } finally {
    elements.release_hold.disabled = false;
  }
}

async function confirmBooking() {
  if (!activeHold) return;
  elements.confirm_booking.disabled = true;
  elements.confirm_booking.textContent = 'Confirming…';
  try {
    const result = await apiRequest('/holds/' + encodeURIComponent(activeHold.holdId) + '/confirm', {
      method: 'POST',
      headers: { 'Idempotency-Key': activeHold.confirmKey },
      body: { customer: {} },
    });
    const booking = mapData(result.payload);
    const seats = (booking.seatIds || activeHold.seatIds).join(', ');
    activeHold = null;
    selectedSeatIds.clear();
    persistActiveHold();
    elements.booking_result.hidden = false;
    elements.booking_result.replaceChildren();
    const title = document.createElement('strong');
    title.textContent = 'You’re all set!';
    const detail = document.createElement('span');
    detail.textContent = (visitorName ? visitorName + ', ' : '') + 'your reservation for seats ' + seats + ' is confirmed. Booking ' + (booking.bookingId || '') + '.';
    elements.booking_result.append(title, detail);
    updateHoldUi();
    await loadSeatMap({ quiet: true });
    showMessage('Your reservation is confirmed.', 'success');
  } catch (error) {
    if (error.code === 'HOLD_EXPIRED') {
      activeHold = null;
      selectedSeatIds.clear();
      persistActiveHold();
      updateHoldUi();
    }
    await loadSeatMap({ quiet: true });
    showMessage(error.requestId ? error.message + ' Reference: ' + error.requestId : error.message, 'error');
  } finally {
    elements.confirm_booking.disabled = false;
    elements.confirm_booking.textContent = 'Confirm reservation →';
  }
}

function changeSession() {
  const nextSession = elements.session_id.value.trim();
  if (!nextSession || nextSession === sessionId) return;
  if (activeHold) {
    elements.session_id.value = sessionId;
    showMessage('Release or confirm your current hold before changing sessions.', 'warning');
    return;
  }
  sessionId = nextSession;
  selectedSeatIds.clear();
  elements.booking_result.hidden = true;
  connectToEvents();
  loadSeatMap();
}

elements.name_form.addEventListener('submit', saveName);
elements.edit_name.addEventListener('click', openNameDialog);
elements.clear_name.addEventListener('click', clearName);
elements.suggest_seats.addEventListener('click', suggestSeats);
elements.hold_seats.addEventListener('click', createHold);
elements.confirm_booking.addEventListener('click', confirmBooking);
elements.release_hold.addEventListener('click', releaseHold);
elements.show_seats.addEventListener('click', () => showPage('seats'));
elements.show_bookings.addEventListener('click', () => showPage('bookings'));
elements.back_to_seats.addEventListener('click', () => showPage('seats'));
elements.demo_profile.addEventListener('change', changeDemoProfile);
elements.session_id.addEventListener('change', changeSession);
elements.session_id.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    changeSession();
    elements.session_id.blur();
  }
});

updateVisitorUi();
elements.demo_profile.value = ['demo-avery', 'demo-blair', 'demo-casey'].includes(savedDemoProfile) ? savedDemoProfile : '';
updateHoldUi();
if (!visitorName) openNameDialog();
connectToEvents();
loadSeatMap();
startHoldClock();
