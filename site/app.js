const home = document.querySelector('#home');
const reader = document.querySelector('#reader');
const form = document.querySelector('#url-form');
const urlInput = document.querySelector('#source-url');
const readButton = document.querySelector('#read-button');
const status = document.querySelector('#status');
const book = document.querySelector('#book');
const barTitle = document.querySelector('#bar-title');
const barProgress = document.querySelector('#bar-progress');
const progressFill = document.querySelector('#progress-fill');
const back = document.querySelector('#back');
const prefsButton = document.querySelector('#prefs-button');
const prefs = document.querySelector('#prefs');
const fontSize = document.querySelector('#font-size');
const lineWidth = document.querySelector('#line-width');
const lineHeight = document.querySelector('#line-height');
const preparation = document.querySelector('#preparation');
const readHero = document.querySelector('#read-hero');
const readSupport = document.querySelector('#read-support');
const changeSource = document.querySelector('#change-source');
const sourcePassport = document.querySelector('.source-passport');
const passportType = document.querySelector('#passport-type');
const passportHost = document.querySelector('#passport-host');
const passportTitle = document.querySelector('#passport-title');
const passportAuthor = document.querySelector('#passport-author');
const passportFacts = document.querySelector('#passport-facts');
const passportOpening = document.querySelector('#passport-opening');
const preparationState = document.querySelector('#preparation-state');
const preparationSteps = [...document.querySelectorAll('.preparation-steps [data-stage]')];
const reloadSource = document.querySelector('#reload-source');
const clearCache = document.querySelector('#clear-cache');

const defaults = { theme: 'paper', fontSize: 20, lineWidth: 700, lineHeight: 1.7 };
const storedSettings = localStorage.getItem('paper:prefs') || '{}';
const settings = { ...defaults, ...JSON.parse(storedSettings) };
const stageOrder = ['fetching', 'downloading', 'checking', 'extracting', 'structuring', 'validating'];
const stageLabels = {
  fetching: 'Finding the source',
  downloading: 'Downloading the source',
  checking: 'Checking it is readable',
  extracting: 'Extracting the text',
  structuring: 'Finding the reading flow',
  validating: 'Checking the final copy',
  complete: 'Your reading copy is ready',
};
// Let the finished checklist land before the reader replaces it.
const COMPLETION_PAUSE_MILLISECONDS = 360;
let activeUrl = '';
let saveTimer = null;
// Marks worth reporting on the way through a document. Kept few so a long
// scroll sends a handful of events, not one per screen.
const PROGRESS_MILESTONES = [25, 50, 75, 100];
let activeReadId = null;
let furthestPercent = 0;
let reportedPercent = 0;
let finalReported = false;
const launchPath = window.location.pathname || '/';
const isReaderEntry = preparation !== null;

function applySettings() {
  document.body.dataset.theme = settings.theme;
  document.documentElement.style.setProperty('--reader-size', `${settings.fontSize}px`);
  document.documentElement.style.setProperty('--reader-width', `${settings.lineWidth}px`);
  document.documentElement.style.setProperty('--reader-line-height', settings.lineHeight);
  fontSize.value = settings.fontSize;
  lineWidth.value = settings.lineWidth;
  lineHeight.value = settings.lineHeight;
  localStorage.setItem('paper:prefs', JSON.stringify(settings));
}

function setLoading(loading) {
  readButton.disabled = loading;
  readButton.innerHTML = loading
    ? 'Opening…'
    : 'Read this document <span aria-hidden="true">&rarr;</span>';
}

function escapeText(value) {
  const span = document.createElement('span');
  span.textContent = value;
  return span.innerHTML;
}

function apiBaseUrl() {
  const configured = document.querySelector('meta[name="paper-api-base"]')?.content?.trim();
  // Source HTML keeps this deployment token. When the backend serves that
  // source locally, use the same origin instead of requesting a fake path.
  if (!configured || configured === '__PAPER_API_URL__') return '';
  return configured.replace(/\/$/, '');
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

// Reading copies saved on this device
// ------------------------------------------------------------------
// Preparing a source runs a full download, extraction, and several model calls.
// A document already read here opens from storage instead of repeating any of
// it. IndexedDB rather than localStorage: one 700-page book serialises to about
// 2.9 MB, which on its own exceeds the whole localStorage quota. Document text
// stays on the device and is never sent anywhere.
const CACHE_DATABASE = 'paper';
const CACHE_DATABASE_VERSION = 2;
const CACHE_DOCUMENTS = 'documents';
const CACHE_ENTRIES = 'entries';
// This browser's own identifier, kept beside its reading copies. It says only
// that two reads came from the same storage, which is the closest thing to a
// returning reader that Paper can know without accounts. Clearing site data
// takes the reading copies and this together, and that is the honest outcome:
// a browser with no library is a new one.
const CACHE_DEVICE = 'device';
const CACHE_DEVICE_KEY = 'this-browser';
const CACHE_DOCUMENT_SCHEMA = 'paper.document.v1';
const CACHE_MAX_DOCUMENTS = 12;
const CACHE_MAX_BYTES = 64 * 1024 * 1024;
const CACHE_MAX_AGE_MILLISECONDS = 30 * 24 * 60 * 60 * 1000;
// Per block, an id, a type, and a locator cost roughly this much beside the
// text. Eviction only needs the order of magnitude, not an exact figure.
const CACHE_BLOCK_OVERHEAD_BYTES = 200;

let cacheDatabase;

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function transactionDone(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

function openCacheDatabase() {
  // Storage can be unavailable: private windows, disabled site data, old
  // browsers. Every caller treats a null database as a plain miss.
  if (cacheDatabase) return cacheDatabase;
  cacheDatabase = new Promise((resolve) => {
    let request;
    try {
      request = indexedDB.open(CACHE_DATABASE, CACHE_DATABASE_VERSION);
    } catch {
      resolve(null);
      return;
    }
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(CACHE_DOCUMENTS)) {
        database.createObjectStore(CACHE_DOCUMENTS, { keyPath: 'url' });
      }
      if (!database.objectStoreNames.contains(CACHE_ENTRIES)) {
        database.createObjectStore(CACHE_ENTRIES, { keyPath: 'url' });
      }
      if (!database.objectStoreNames.contains(CACHE_DEVICE)) {
        database.createObjectStore(CACHE_DEVICE, { keyPath: 'key' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
  return cacheDatabase;
}

function newIdentifier() {
  if (typeof crypto?.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

let deviceIdentityPromise;

function deviceIdentity() {
  // Resolved once and reused: every event carries it, and none of them should
  // wait on storage to find out who is asking.
  if (deviceIdentityPromise) return deviceIdentityPromise;
  deviceIdentityPromise = (async () => {
    try {
      const database = await openCacheDatabase();
      if (!database) return null;
      const existing = await requestResult(
        database.transaction(CACHE_DEVICE, 'readonly').objectStore(CACHE_DEVICE).get(CACHE_DEVICE_KEY)
      );
      if (existing?.id) return existing;
      const created = { key: CACHE_DEVICE_KEY, id: newIdentifier(), createdAt: Date.now() };
      const transaction = database.transaction(CACHE_DEVICE, 'readwrite');
      transaction.objectStore(CACHE_DEVICE).put(created);
      await transactionDone(transaction);
      return created;
    } catch {
      // Storage can be unavailable. Telemetry then simply carries no device.
      return null;
    }
  })();
  return deviceIdentityPromise;
}

async function keepStorage() {
  // Asked only once a reading copy exists, so the browser is deciding about
  // something the reader would actually miss. Without it a saved book can be
  // evicted under disk pressure and has to be prepared all over again.
  try { await navigator.storage?.persist?.(); } catch { /* not offered here */ }
}

async function postTelemetry(path, body) {
  const identity = await deviceIdentity();
  // Best effort throughout: reading must never wait on, or fail because of,
  // a telemetry request. `keepalive` lets it survive the page going away.
  void fetch(`${apiBaseUrl()}/api/telemetry/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, device_id: identity?.id || null }),
    keepalive: true,
  }).catch(() => {});
}

function documentBytes(documentData) {
  let total = 0;
  for (const block of documentData.blocks) total += block.text.length + CACHE_BLOCK_OVERHEAD_BYTES;
  return total;
}

async function readCachedDocument(url) {
  try {
    const database = await openCacheDatabase();
    if (!database) return null;

    // The entry is small, so an expired or outdated copy is discarded without
    // ever deserialising the document beside it.
    const entry = await requestResult(
      database.transaction(CACHE_ENTRIES, 'readonly').objectStore(CACHE_ENTRIES).get(url)
    );
    if (!entry) return null;
    if (entry.schema !== CACHE_DOCUMENT_SCHEMA || Date.now() - entry.savedAt > CACHE_MAX_AGE_MILLISECONDS) {
      await forgetCachedDocuments([url]);
      return null;
    }

    const record = await requestResult(
      database.transaction(CACHE_DOCUMENTS, 'readonly').objectStore(CACHE_DOCUMENTS).get(url)
    );
    if (!record?.document) {
      await forgetCachedDocuments([url]);
      return null;
    }

    const touched = database.transaction(CACHE_ENTRIES, 'readwrite');
    touched.objectStore(CACHE_ENTRIES).put({ ...entry, readAt: Date.now() });
    transactionDone(touched).catch(() => {});
    return { document: record.document, readId: entry.readId || null };
  } catch {
    return null;
  }
}

async function saveCachedDocument(url, documentData, readId) {
  try {
    const database = await openCacheDatabase();
    if (!database) return;
    const now = Date.now();
    const transaction = database.transaction([CACHE_DOCUMENTS, CACHE_ENTRIES], 'readwrite');
    transaction.objectStore(CACHE_DOCUMENTS).put({ url, document: documentData });
    transaction.objectStore(CACHE_ENTRIES).put({
      url,
      schema: documentData.schema,
      bytes: documentBytes(documentData),
      savedAt: now,
      readAt: now,
      readId,
    });
    await transactionDone(transaction);
    await evictCachedDocuments();
    await keepStorage();
  } catch {
    // A source that will not fit is simply not cached; the reader is unaffected.
  }
}

async function forgetCachedDocuments(urls) {
  if (!urls.length) return;
  const database = await openCacheDatabase();
  if (!database) return;
  const transaction = database.transaction([CACHE_DOCUMENTS, CACHE_ENTRIES], 'readwrite');
  for (const url of urls) {
    transaction.objectStore(CACHE_DOCUMENTS).delete(url);
    transaction.objectStore(CACHE_ENTRIES).delete(url);
  }
  await transactionDone(transaction);
}

async function evictCachedDocuments() {
  const database = await openCacheDatabase();
  if (!database) return;
  const entries = await requestResult(
    database.transaction(CACHE_ENTRIES, 'readonly').objectStore(CACHE_ENTRIES).getAll()
  );

  const now = Date.now();
  const drop = [];
  const live = [];
  for (const entry of entries) {
    if (now - entry.savedAt > CACHE_MAX_AGE_MILLISECONDS) drop.push(entry.url);
    else live.push(entry);
  }

  // Keep what was read most recently, up to both caps.
  live.sort((first, second) => second.readAt - first.readAt);
  let kept = 0;
  let bytes = 0;
  for (const entry of live) {
    bytes += entry.bytes;
    if (kept < CACHE_MAX_DOCUMENTS && bytes <= CACHE_MAX_BYTES) kept += 1;
    else drop.push(entry.url);
  }
  await forgetCachedDocuments(drop);
}

async function clearCachedDocuments() {
  try {
    const database = await openCacheDatabase();
    if (!database) return;
    // Named stores only: clearing saved copies is not the same as asking to be
    // treated as a different browser, so the device record is left alone.
    const transaction = database.transaction([CACHE_DOCUMENTS, CACHE_ENTRIES], 'readwrite');
    transaction.objectStore(CACHE_DOCUMENTS).clear();
    transaction.objectStore(CACHE_ENTRIES).clear();
    await transactionDone(transaction);
  } catch {
    // Nothing to clear is the same outcome as a cleared cache.
  }
}

function readerEntryUrl(url, origin) {
  const query = new URLSearchParams({ url: url.trim() });
  // Carried across the hop to the reader page so it still knows how this
  // source was chosen.
  if (origin) query.set('from', origin);
  // Resolved against the page this is running on rather than the domain root.
  // Paper is served from / when the API serves the site and from /paper/ on
  // GitHub Pages, where a root-absolute path sent every visitor to a 404. The
  // deploy rewrites site-absolute links in HTML and CSS, but never in here.
  return new URL(`read?${query}`, document.baseURI).toString();
}

function hostname(url) {
  try { return new URL(url).hostname; } catch { return url; }
}

function sourceTypeLabel(type) {
  if (!type) return 'PUBLIC DOCUMENT';
  return type === 'pdf' ? 'PUBLIC PDF' : 'PUBLIC WEB DOCUMENT';
}

function megabytes(bytes) {
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

function updatePreparationStage(stage, detail = {}) {
  if (!preparationState) return;
  const activeIndex = stage === 'complete' ? stageOrder.length : Math.max(0, stageOrder.indexOf(stage));
  const label = stageLabels[stage] || stageLabels.fetching;
  const total = Number(detail.total) || 0;
  const received = Number(detail.received) || 0;
  // A large source can take a minute to transfer. Show the real figure rather
  // than leaving the step to look stalled.
  preparationState.textContent = stage === 'downloading' && total
    ? `${label} — ${megabytes(received)} of ${megabytes(total)}`
    : label;
  preparationSteps.forEach((item, index) => {
    item.classList.toggle('complete', index < activeIndex);
    item.classList.toggle('active', index === activeIndex && stage !== 'complete');
    const measured = item.dataset.stage === 'downloading' && stage === 'downloading' && total > 0;
    item.classList.toggle('measured', measured);
    if (measured) item.style.setProperty('--stage-progress', `${Math.min(100, (received / total) * 100)}%`);
  });
}

function addPassportFact(label, value) {
  if (!passportFacts || !value) return;
  const wrapper = document.createElement('div');
  const term = document.createElement('dt');
  const detail = document.createElement('dd');
  term.textContent = label;
  detail.textContent = value;
  wrapper.append(term, detail);
  passportFacts.append(wrapper);
}

function renderPassport(passport = {}) {
  if (!passportTitle) return;
  passportType.textContent = sourceTypeLabel(passport.source_type);
  passportHost.textContent = passport.source_host || hostname(activeUrl);
  passportTitle.textContent = passport.title || 'A public document';
  passportAuthor.textContent = passport.author || '';
  passportAuthor.classList.toggle('hidden', !passport.author);
  passportFacts.replaceChildren();
  if (passport.source_type) addPassportFact('FORMAT', passport.source_type === 'pdf' ? 'PDF' : 'HTML');
  addPassportFact('PAGES', passport.page_count ? String(passport.page_count) : '');
  addPassportFact('READ', passport.reading_minutes ? `~${passport.reading_minutes} min` : '');
  addPassportFact('SECTIONS', passport.section_count ? String(passport.section_count) : '');
  addPassportFact('LANGUAGE', passport.language || '');
  passportOpening.textContent = passport.opening_text || '';
  passportOpening.classList.toggle('hidden', !passport.opening_text);
  sourcePassport?.classList.toggle('is-pending', !passport.source_type && !passport.title);
}

function showPreparation() {
  readHero?.classList.add('hidden');
  readSupport?.classList.add('hidden');
  preparation?.classList.remove('hidden');
  home.classList.add('is-preparing');
  document.title = 'Preparing your reading copy — Paper';
  renderPassport();
  updatePreparationStage('fetching');
  window.scrollTo(0, 0);
}

function showLaunch() {
  preparation?.classList.add('hidden');
  readHero?.classList.remove('hidden');
  readSupport?.classList.remove('hidden');
  home.classList.remove('is-preparing');
  if (isReaderEntry) document.title = 'Open a document — Paper';
}

function parseSseEvent(message) {
  const lines = message.split('\n');
  const data = [];
  let event = 'message';
  for (const line of lines) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) data.push(line.slice(5).trim());
  }
  try { return { event, payload: JSON.parse(data.join('\n')) }; } catch { return { event, payload: {} }; }
}

async function streamReadPreparation(url, origin = 'unknown') {
  const identity = await deviceIdentity();
  const query = new URLSearchParams({ url, origin });
  if (identity?.id) query.set('device', identity.id);
  const response = await fetch(`${apiBaseUrl()}/api/read/events?${query}`, {
    headers: { Accept: 'text/event-stream' },
  });
  if (!response.ok || !response.body) throw new Error('Paper could not start preparing that document.');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let boundary = buffer.indexOf('\n\n');
    while (boundary >= 0) {
      const { event, payload } = parseSseEvent(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (event === 'progress') {
        if (payload.passport) renderPassport(payload.passport);
        updatePreparationStage(payload.stage || 'fetching', payload);
      }
      if (event === 'complete' && payload.document) {
        return { document: payload.document, readId: payload.read_id || null };
      }
      if (event === 'error') throw new Error(payload.detail || 'Could not prepare that document.');
      boundary = buffer.indexOf('\n\n');
    }
    if (done) break;
  }
  throw new Error('Paper stopped preparing that document. Please try again.');
}

function renderBook(data) {
  const metadata = data.metadata || {};
  const title = metadata.title || 'Untitled document';
  const fragments = [];
  fragments.push(`<h1 class="book-title">${escapeText(title)}</h1>`);
  if (metadata.author) fragments.push(`<div class="book-author">${escapeText(metadata.author)}</div>`);
  for (const block of data.blocks) {
    if (block.type === 'heading') fragments.push(`<h2>${escapeText(block.text)}</h2>`);
    else if (block.type === 'quote') fragments.push(`<blockquote>${escapeText(block.text)}</blockquote>`);
    else if (block.type === 'code') fragments.push(`<pre>${escapeText(block.text)}</pre>`);
    else if (block.type === 'list_item') fragments.push(`<p class="book-list-item">${escapeText(block.text)}</p>`);
    else fragments.push(`<p>${escapeText(block.text)}</p>`);
  }
  book.innerHTML = fragments.join('');
  barTitle.textContent = title;
  document.title = `${title} — Paper`;
}

function storageKey() {
  return `paper:progress:${activeUrl}`;
}

function restoreProgress() {
  const ratio = Number(localStorage.getItem(storageKey()) || 0);
  requestAnimationFrame(() => {
    const max = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    window.scrollTo(0, Math.min(max, max * ratio));
    updateProgress();
  });
}

function reportReadingProgress(percent, final) {
  if (!activeUrl) return;
  // A milestone only counts once. The closing figure is always sent, even when
  // it repeats the last mark, because "this is where they stopped" is the
  // whole question and it cannot be inferred from a mark they passed.
  if (final ? finalReported || percent <= 0 : percent <= reportedPercent) return;
  if (final) finalReported = true;
  reportedPercent = Math.max(reportedPercent, percent);
  void postTelemetry('reading-progress', {
    source_url: activeUrl,
    read_id: activeReadId,
    percent,
    final,
  });
}

function updateProgress() {
  if (reader.classList.contains('hidden')) return;
  const max = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
  const ratio = Math.max(0, Math.min(1, window.scrollY / max));
  const pct = Math.round(ratio * 100);
  progressFill.style.width = `${pct}%`;
  barProgress.textContent = `${pct}%`;
  furthestPercent = Math.max(furthestPercent, pct);
  // Preparing a document is not the point; finishing one is. Reporting a few
  // marks on the way tells opened apart from actually read.
  const passed = PROGRESS_MILESTONES.filter((mark) => furthestPercent >= mark).pop();
  if (passed) reportReadingProgress(passed, false);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => localStorage.setItem(storageKey(), ratio.toString()), 120);
}

function reportFinalProgress() {
  // A reader who stops at 40% never crosses another mark, so the figure they
  // actually reached is only knowable as the page goes away.
  if (reader.classList.contains('hidden')) return;
  reportReadingProgress(furthestPercent, true);
}

function openReader(documentData, pushState) {
  renderBook(documentData);
  home.classList.add('hidden');
  reader.classList.remove('hidden');
  if (pushState) history.pushState({ url: activeUrl }, '', readerEntryUrl(activeUrl));
  restoreProgress();
}

function openedBeforeInThisTab(sourceUrl) {
  // Back and forward reload the page, so one visit can render a document
  // several times. That is still worth recording — reopening a saved book is
  // exactly the signal a cache is for — but it is marked, so counting first
  // opens does not have to mean counting keystrokes.
  try {
    const key = `paper:opened:${sourceUrl}`;
    const seen = Boolean(sessionStorage.getItem(key));
    sessionStorage.setItem(key, '1');
    return seen;
  } catch {
    return false;
  }
}

function reportReaderOpened(documentData, readId, cacheHit, origin) {
  const sourceUrl = documentData.source?.url || activeUrl;
  if (!sourceUrl) return;
  // A server-side "prepared" event remains useful if this request is lost, so
  // nothing here is worth making the reader wait for.
  void postTelemetry('reader-opened', {
    source_url: sourceUrl,
    read_id: readId,
    cache_hit: cacheHit,
    origin,
    repeat: openedBeforeInThisTab(sourceUrl),
  });
}

async function openSource(url, pushState = true, origin = 'unknown') {
  activeUrl = url.trim();
  if (!activeUrl) return;
  status.className = 'status';
  status.textContent = '';
  furthestPercent = 0;
  reportedPercent = 0;
  finalReported = false;

  const saved = await readCachedDocument(activeUrl);
  if (saved) {
    activeReadId = saved.readId;
    openReader(saved.document, pushState);
    reportReaderOpened(saved.document, saved.readId, true, origin);
    return;
  }

  showPreparation();
  try {
    const { document: documentData, readId } = await streamReadPreparation(activeUrl, origin);
    activeReadId = readId;
    updatePreparationStage('complete');
    await delay(COMPLETION_PAUSE_MILLISECONDS);
    openReader(documentData, pushState);
    reportReaderOpened(documentData, readId, false, origin);
    saveCachedDocument(activeUrl, documentData, readId);
  } catch (err) {
    showLaunch();
    home.classList.remove('hidden');
    reader.classList.add('hidden');
    status.className = 'status error';
    status.textContent = err.message || String(err);
  } finally {
    setLoading(false);
  }
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;
  if (!isReaderEntry) {
    setLoading(true);
    window.location.assign(readerEntryUrl(url, 'pasted'));
    return;
  }
  openSource(url, true, 'pasted');
});

// A one-click sample is a different signal from a document someone brought
// themselves, and a launch needs to tell them apart.
document.querySelectorAll('[data-sample-url]').forEach((sample) => {
  sample.addEventListener('click', (event) => {
    event.preventDefault();
    const url = sample.dataset.sampleUrl;
    if (!url) return;
    urlInput.value = url;
    if (!isReaderEntry) {
      setLoading(true);
      window.location.assign(readerEntryUrl(url, 'sample'));
      return;
    }
    openSource(url, true, 'sample');
  });
});

changeSource?.addEventListener('click', () => {
  showLaunch();
  history.replaceState({}, '', launchPath);
  urlInput.focus();
});

back.addEventListener('click', () => {
  reader.classList.add('hidden');
  home.classList.remove('hidden');
  prefs.classList.add('hidden');
  progressFill.style.width = '0';
  document.title = 'Paper — the best way to read anything from the public internet';
  if (isReaderEntry) showLaunch();
  history.pushState({}, '', launchPath);
  window.scrollTo(0, 0);
});

reloadSource?.addEventListener('click', async () => {
  const url = activeUrl;
  if (!url) return;
  prefs.classList.add('hidden');
  await forgetCachedDocuments([url]).catch(() => {});
  if (!isReaderEntry) {
    window.location.assign(readerEntryUrl(url, 'reload'));
    return;
  }
  reader.classList.add('hidden');
  home.classList.remove('hidden');
  openSource(url, false, 'reload');
});

clearCache?.addEventListener('click', async () => {
  clearCache.disabled = true;
  const label = clearCache.textContent;
  await clearCachedDocuments();
  clearCache.textContent = 'Cleared';
  window.setTimeout(() => {
    clearCache.textContent = label;
    clearCache.disabled = false;
  }, 1600);
});

prefsButton.addEventListener('click', (event) => {
  event.stopPropagation();
  prefs.classList.toggle('hidden');
});
document.addEventListener('click', (event) => {
  if (!prefs.contains(event.target) && event.target !== prefsButton) prefs.classList.add('hidden');
});
prefs.addEventListener('click', (event) => event.stopPropagation());

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') prefs.classList.add('hidden');
});

document.querySelectorAll('[data-theme]').forEach((button) => {
  button.addEventListener('click', () => { settings.theme = button.dataset.theme; applySettings(); });
});
fontSize.addEventListener('input', () => { settings.fontSize = Number(fontSize.value); applySettings(); updateProgress(); });
lineWidth.addEventListener('input', () => { settings.lineWidth = Number(lineWidth.value); applySettings(); updateProgress(); });
lineHeight.addEventListener('input', () => { settings.lineHeight = Number(lineHeight.value); applySettings(); updateProgress(); });
window.addEventListener('scroll', updateProgress, { passive: true });
window.addEventListener('resize', updateProgress);
window.addEventListener('popstate', () => location.reload());
// Both, because a closing desktop tab fires pagehide while a phone switching
// away usually only fires the visibility change.
window.addEventListener('pagehide', reportFinalProgress);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') reportFinalProgress();
});

applySettings();
const initialQuery = new URLSearchParams(location.search);
const initialUrl = initialQuery.get('url');
// A source that arrives in the address bar with no hint came from a shared or
// bookmarked link rather than from anything on this site.
const initialOrigin = initialQuery.get('from') || 'link';
if (initialUrl && isReaderEntry) {
  urlInput.value = initialUrl;
  openSource(initialUrl, false, initialOrigin);
} else if (initialUrl) {
  location.replace(readerEntryUrl(initialUrl, initialOrigin));
}
