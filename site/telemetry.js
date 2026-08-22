const eventsBody = document.querySelector('#telemetry-events');
const emptyState = document.querySelector('#telemetry-empty');
const status = document.querySelector('#telemetry-status');
const refreshButton = document.querySelector('#refresh-events');
const counters = {
  attempts: document.querySelector('#attempt-count'),
  ready: document.querySelector('#ready-count'),
  opened: document.querySelector('#opened-count'),
  openRate: document.querySelector('#open-rate'),
  readers: document.querySelector('#reader-count'),
  finished: document.querySelector('#finished-count'),
};

const labels = {
  read_attempted: 'Preparing',
  read_prepared: 'Ready',
  read_rejected: 'Rejected',
  read_failed: 'Failed',
  read_abandoned: 'Left while preparing',
  reader_opened: 'Opened',
  reader_cache_opened: 'Opened from cache',
  reading_progress: 'Reading',
};

function seconds(milliseconds) {
  return `${(milliseconds / 1000).toFixed(1)}s`;
}

function slowestStage(stageMs) {
  // One number for where the time went. The whole breakdown is in the event.
  const stages = Object.entries(stageMs || {});
  if (!stages.length) return '';
  const [stage, spent] = stages.sort((first, second) => second[1] - first[1])[0];
  return `slowest ${stage} ${seconds(spent)}`;
}

function textCell(value = '') {
  const cell = document.createElement('td');
  cell.textContent = value;
  return cell;
}

function eventDetails(event, run) {
  const parts = [];
  if (event.event === 'read_prepared') {
    parts.push(event.source_type?.toUpperCase());
    if (Number.isFinite(event.block_count)) parts.push(`${event.block_count.toLocaleString()} blocks`);
    if (Number.isFinite(event.elapsed_ms)) parts.push(seconds(event.elapsed_ms));
    parts.push(slowestStage(event.stage_ms));
    if (event.chunk_count > 1) parts.push(`${event.chunk_count} chunks`);
    // A retry that then succeeded leaves no other trace of the near miss.
    if (event.retry_count) parts.push(`${event.retry_count} retried`);
  } else if (event.event === 'reading_progress') {
    parts.push(`${event.percent}%${event.final ? ' when they left' : ''}`);
  } else {
    if (event.reason) parts.push(event.reason);
    else if (event.stage) parts.push(event.stage);
    if (event.status_code) parts.push(`HTTP ${event.status_code}`);
    parts.push(slowestStage(event.stage_ms));
  }
  if (run?.furthest) parts.push(`read to ${run.furthest}%`);
  return parts.filter(Boolean).join(' · ') || '—';
}

function setSummary(events) {
  const attemptIds = new Set(events.filter((event) => event.event === 'read_attempted').map((event) => event.read_id));
  const ready = new Set(events.filter((event) => event.event === 'read_prepared').map((event) => event.read_id)).size;
  const openedIds = new Set(
    events
      .filter((event) => event.event === 'reader_opened' || event.event === 'reader_cache_opened')
      .map((event) => event.read_id)
  );
  const opened = [...openedIds].filter((id) => attemptIds.has(id)).length;
  const devices = new Set(events.map((event) => event.device_id).filter(Boolean));
  // Preparing a document is not the point; finishing one is.
  const finished = new Set(
    events.filter((event) => event.event === 'reading_progress' && event.percent >= 100).map((event) => event.source_url)
  );
  counters.attempts.textContent = attemptIds.size;
  counters.ready.textContent = ready;
  counters.opened.textContent = opened;
  counters.openRate.textContent = attemptIds.size ? `${Math.round((opened / attemptIds.size) * 100)}%` : '—';
  counters.readers.textContent = devices.size || '—';
  counters.finished.textContent = finished.size;
}

function runsFromEvents(events) {
  const runs = new Map();
  // Oldest first, so the browser a device is first seen on is the one that
  // counts as new. Everything after that is a reader who came back.
  const seenDevices = new Set();
  for (const event of [...events].reverse()) {
    const id = event.read_id || `${event.observed_at}:${event.source_url}`;
    const existing = runs.get(id);
    if (existing) {
      // Progress arrives after the run's outcome and should not replace it.
      if (event.event === 'reading_progress') existing.furthest = Math.max(existing.furthest, event.percent || 0);
      else existing.latest = event;
      existing.title = event.title || existing.title;
      existing.sourceUrl = event.source_url || existing.sourceUrl;
      existing.deviceId = existing.deviceId || event.device_id || '';
      continue;
    }
    const deviceId = event.device_id || '';
    const returning = Boolean(deviceId) && seenDevices.has(deviceId);
    if (deviceId) seenDevices.add(deviceId);
    runs.set(id, {
      id,
      startedAt: event.observed_at,
      latest: event,
      title: event.title || '',
      sourceUrl: event.source_url || '',
      deviceId,
      returning,
      origin: event.origin || '',
      furthest: event.event === 'reading_progress' ? event.percent || 0 : 0,
    });
  }
  return [...runs.values()].sort((first, second) => second.startedAt.localeCompare(first.startedAt));
}

function browserCell(run) {
  const cell = document.createElement('td');
  cell.className = 'telemetry-source';
  if (!run.deviceId) {
    cell.textContent = '—';
    return cell;
  }
  const who = document.createElement('strong');
  who.textContent = run.returning ? 'Returning' : 'New';
  const detail = document.createElement('span');
  detail.textContent = [run.deviceId.slice(0, 8), run.origin].filter(Boolean).join(' · ');
  detail.title = run.deviceId;
  cell.append(who, detail);
  return cell;
}

function sourceCell(run) {
  const cell = document.createElement('td');
  cell.className = 'telemetry-source';
  if (run.title) {
    const title = document.createElement('strong');
    title.textContent = run.title;
    cell.append(title);
  }
  const url = document.createElement('span');
  url.textContent = run.sourceUrl;
  url.title = run.sourceUrl;
  cell.append(url);
  return cell;
}

function renderEvents(events) {
  eventsBody.replaceChildren();
  setSummary(events);
  const runs = runsFromEvents(events);
  emptyState.classList.toggle('hidden', runs.length > 0);
  for (const run of runs) {
    const event = run.latest;
    const row = document.createElement('tr');
    row.append(textCell(new Date(run.startedAt).toLocaleTimeString()));
    const outcome = textCell(labels[event.event] || event.event);
    outcome.className = `telemetry-outcome ${event.event}`;
    row.append(outcome);
    row.append(sourceCell(run), browserCell(run), textCell(eventDetails(event, run)));
    eventsBody.append(row);
  }
}

async function loadEvents() {
  refreshButton.disabled = true;
  try {
    const response = await fetch('/api/telemetry/events?limit=100', { cache: 'no-store' });
    if (!response.ok) throw new Error('Could not load local telemetry.');
    const payload = await response.json();
    renderEvents(payload.events || []);
    status.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    status.textContent = error.message || 'Could not load local telemetry.';
  } finally {
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener('click', loadEvents);
window.setInterval(loadEvents, 5000);
loadEvents();
