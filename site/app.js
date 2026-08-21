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

function readerEntryUrl(url) {
  return `/read?url=${encodeURIComponent(url.trim())}`;
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

async function streamReadPreparation(url) {
  const response = await fetch(`${apiBaseUrl()}/api/read/events?url=${encodeURIComponent(url)}`, {
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
      if (event === 'complete' && payload.document) return payload.document;
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

function updateProgress() {
  if (reader.classList.contains('hidden')) return;
  const max = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
  const ratio = Math.max(0, Math.min(1, window.scrollY / max));
  const pct = Math.round(ratio * 100);
  progressFill.style.width = `${pct}%`;
  barProgress.textContent = `${pct}%`;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => localStorage.setItem(storageKey(), ratio.toString()), 120);
}

async function openSource(url, pushState = true) {
  activeUrl = url.trim();
  if (!activeUrl) return;
  status.className = 'status';
  status.textContent = '';
  showPreparation();
  try {
    const documentData = await streamReadPreparation(activeUrl);
    updatePreparationStage('complete');
    await delay(COMPLETION_PAUSE_MILLISECONDS);
    renderBook(documentData);
    home.classList.add('hidden');
    reader.classList.remove('hidden');
    if (pushState) history.pushState({ url: activeUrl }, '', readerEntryUrl(activeUrl));
    restoreProgress();
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
    window.location.assign(readerEntryUrl(url));
    return;
  }
  openSource(url);
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

applySettings();
const initialUrl = new URLSearchParams(location.search).get('url');
if (initialUrl && isReaderEntry) {
  urlInput.value = initialUrl;
  openSource(initialUrl, false);
} else if (initialUrl) {
  location.replace(readerEntryUrl(initialUrl));
}
