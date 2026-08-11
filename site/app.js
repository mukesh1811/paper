const home = document.querySelector('#home');
const reader = document.querySelector('#reader');
const form = document.querySelector('#url-form');
const urlInput = document.querySelector('#pdf-url');
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

const defaults = { theme: 'paper', fontSize: 20, lineWidth: 700, lineHeight: 1.7 };
const storedSettings = localStorage.getItem('paper:prefs') || '{}';
let settings = { ...defaults, ...JSON.parse(storedSettings) };
let activeUrl = '';
let saveTimer = null;
const launchPath = window.location.pathname || '/';

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
  readButton.textContent = loading ? 'Opening…' : 'Read this document';
}

function escapeText(value) {
  const span = document.createElement('span');
  span.textContent = value;
  return span.innerHTML;
}

function renderBook(data) {
  const fragments = [];
  fragments.push(`<h1 class="book-title">${escapeText(data.title)}</h1>`);
  if (data.author) fragments.push(`<div class="book-author">${escapeText(data.author)}</div>`);
  for (const block of data.blocks) {
    if (block.type === 'heading') fragments.push(`<h2>${escapeText(block.text)}</h2>`);
    else fragments.push(`<p>${escapeText(block.text)}</p>`);
  }
  book.innerHTML = fragments.join('');
  barTitle.textContent = data.title;
  document.title = `${data.title} — Paper`;
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

async function openPdf(url, pushState = true) {
  activeUrl = url.trim();
  status.className = 'status';
  status.textContent = 'Fetching and reflowing…';
  setLoading(true);
  try {
    const apiBase = document.querySelector('meta[name="paper-api-base"]')?.content?.replace(/\/$/, '') || '';
    const response = await fetch(`${apiBase}/api/read?url=${encodeURIComponent(activeUrl)}`);
    let payload;
    try { payload = await response.json(); } catch { payload = {}; }
    if (!response.ok) throw new Error(payload.detail || 'Could not open that PDF.');
    renderBook(payload);
    home.classList.add('hidden');
    reader.classList.remove('hidden');
    status.textContent = '';
    if (pushState) history.pushState({ url: activeUrl }, '', `${launchPath}?url=${encodeURIComponent(activeUrl)}`);
    restoreProgress();
  } catch (err) {
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
  openPdf(urlInput.value);
});

back.addEventListener('click', () => {
  reader.classList.add('hidden');
  home.classList.remove('hidden');
  prefs.classList.add('hidden');
  progressFill.style.width = '0';
  document.title = 'Paper — the best way to read anything from the public internet';
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
  if (event.key === 'Escape') {
    prefs.classList.add('hidden');
  }
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
if (initialUrl) {
  urlInput.value = initialUrl;
  openPdf(initialUrl, false);
}
