const state = {
  challenges: [],
  selectedId: 'vanishing-receipt',
  implementation: 'baseline',
  run: null,
  events: [],
  source: null,
  poller: null,
};

const $ = selector => document.querySelector(selector);
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
})[char]);

function currentChallenge() {
  return state.challenges.find(challenge => challenge.id === state.selectedId);
}

function renderCatalog() {
  const playable = state.challenges.filter(challenge => challenge.playable);
  $('#challenge-nav').innerHTML = playable.map((challenge, index) => `
    <button class="challenge-link ${challenge.id === state.selectedId ? 'selected' : ''}" data-challenge="${challenge.id}">
      <span>STUDY ${String(index + 1).padStart(2, '0')} · ${escapeHtml(challenge.difficulty)}</span>
      ${escapeHtml(challenge.title)}
    </button>`).join('');
  $('#roadmap').innerHTML = state.challenges.filter(challenge => !challenge.playable).map((challenge, index) => `
    <article class="roadmap-card">
      <span class="num">${String(index + 3).padStart(2, '0')}</span>
      <div><h3>${escapeHtml(challenge.title)}</h3><p>${escapeHtml(challenge.summary)}</p></div>
      <span class="difficulty">${escapeHtml(challenge.difficulty)}</span>
    </article>`).join('');
  document.querySelectorAll('[data-challenge]').forEach(button => button.addEventListener('click', () => {
    if (state.run?.status === 'running') return;
    state.selectedId = button.dataset.challenge;
    resetExperiment();
    renderCatalog();
    renderChallenge();
  }));
}

function renderChallenge() {
  const challenge = currentChallenge();
  $('#challenge-copy').innerHTML = `
    <span class="section-label">${escapeHtml(challenge.eyebrow)} · ${escapeHtml(challenge.difficulty)}</span>
    <h2>${escapeHtml(challenge.title)}</h2>
    <p class="summary">${escapeHtml(challenge.summary)}</p>
    <p class="brief">${escapeHtml(challenge.brief)} <strong>${escapeHtml(challenge.objective)}</strong></p>
    <div class="brief-grid">
      <div><h3>The contract</h3><ul>${challenge.requirements.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>
      <div><h3>Scope primer</h3><ul>${challenge.scope.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>
    </div>`;
}

function workerCards(workers = []) {
  const indexed = new Map(workers.map(worker => [worker.worker_id, worker]));
  return ['lab-worker-1', 'lab-worker-2', 'lab-worker-3'].map(workerId => {
    const worker = indexed.get(workerId) || {worker_id: workerId, state: 'unknown', communicating: false, heartbeat_age: null};
    const silent = worker.state === 'offline' || !worker.communicating;
    const status = worker.state === 'offline' ? 'offline' : silent ? 'silent' : worker.state;
    const detail = worker.task_id ? `${worker.task_id}${worker.generation ? ` · generation ${worker.generation}` : ''}` : 'No task claimed';
    return `<article class="worker-card ${escapeHtml(status)}">
      <div class="worker-top"><span>${escapeHtml(worker.worker_id.replace('lab-', ''))}</span><span class="signal">${silent ? 'no signal' : escapeHtml(worker.state)}</span></div>
      <div class="worker-visual"></div>
      <div class="worker-detail">${escapeHtml(detail)} · heartbeat ${worker.heartbeat_age ?? '—'}s</div>
    </article>`;
  }).join('');
}

function renderRun() {
  const run = state.run;
  $('#workers').innerHTML = workerCards(run?.workers);
  $('#phase').textContent = run?.phase || 'Awaiting experiment';
  const runState = $('#run-state');
  runState.className = `run-state ${run?.status || 'idle'}`;
  runState.querySelector('span').textContent = run?.status || 'Idle';
  $('#run-button').disabled = run?.status === 'running';
  $('#cancel-button').hidden = run?.status !== 'running';

  $('#event-count').textContent = `${String(state.events.length).padStart(2, '0')} entries`;
  $('#timeline').innerHTML = state.events.length ? state.events.map(event => `
    <li><time>${new Date(Number(event.at) * 1000).toLocaleTimeString([], {hour12: false})}</time><span class="actor">${escapeHtml(event.worker)}</span><span><b class="kind">${escapeHtml(event.kind)}</b><br>${escapeHtml(event.message)}</span></li>`).join('') : '<li class="empty">Run the experiment to open the ledger.</li>';
  $('#timeline').scrollTop = $('#timeline').scrollHeight;

  const invariants = run?.invariants || [];
  $('#invariants').innerHTML = invariants.length ? invariants.map(item => `
    <article class="invariant ${item.passed ? 'pass' : 'fail'}"><strong>${escapeHtml(item.label)}</strong><p>${escapeHtml(item.evidence)}</p></article>`).join('') : '<p class="empty">Evaluation appears when the run concludes.</p>';

  const metrics = Object.entries(run?.metrics || {}).filter(([name]) => !name.includes('visible') && !name.includes('completed')).slice(0, 4);
  $('#metrics').innerHTML = metrics.map(([name, value]) => `<div class="metric"><span>${escapeHtml(name.replaceAll('_', ' '))}</span><strong>${escapeHtml(value)}</strong></div>`).join('');

  const verdict = $('#verdict');
  if (run && ['passed', 'failed'].includes(run.status)) {
    verdict.hidden = false;
    verdict.textContent = run.status === 'passed' ? 'All measured invariants held.' : 'The solution is incomplete.';
  } else {
    verdict.hidden = true;
  }
}

function resetExperiment() {
  state.run = null;
  state.events = [];
  state.source?.close();
  clearInterval(state.poller);
  renderRun();
}

async function refreshRun() {
  if (!state.run) return;
  const response = await fetch(`/api/lab/runs/${state.run.id}`);
  if (!response.ok) return;
  state.run = await response.json();
  renderRun();
  if (state.run.status !== 'running') {
    clearInterval(state.poller);
    state.source?.close();
  }
}

function connectEvents(runId) {
  state.source?.close();
  state.source = new EventSource(`/api/lab/runs/${runId}/events`);
  state.source.onmessage = event => {
    const item = JSON.parse(event.data);
    if (!state.events.some(existing => existing.id === event.lastEventId)) {
      state.events.push({...item, id: event.lastEventId});
      renderRun();
    }
  };
}

async function startRun() {
  resetExperiment();
  $('#run-button').disabled = true;
  const response = await fetch('/api/lab/runs', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({challenge_id: state.selectedId, implementation: state.implementation}),
  });
  if (!response.ok) {
    const error = await response.json();
    $('#phase').textContent = typeof error.detail === 'string' ? error.detail : error.detail?.message || 'Unable to start';
    $('#run-button').disabled = false;
    return;
  }
  state.run = await response.json();
  renderRun();
  connectEvents(state.run.id);
  state.poller = setInterval(refreshRun, 500);
}

document.querySelectorAll('[data-implementation]').forEach(button => button.addEventListener('click', () => {
  if (state.run?.status === 'running') return;
  state.implementation = button.dataset.implementation;
  document.querySelectorAll('[data-implementation]').forEach(item => item.classList.toggle('selected', item === button));
}));
$('#run-button').addEventListener('click', startRun);
$('#cancel-button').addEventListener('click', async () => {
  if (state.run) await fetch(`/api/lab/runs/${state.run.id}/cancel`, {method: 'POST'});
  await refreshRun();
});

fetch('/api/lab/challenges').then(response => response.json()).then(challenges => {
  state.challenges = challenges;
  renderCatalog();
  renderChallenge();
  renderRun();
}).catch(() => { $('#phase').textContent = 'Lab catalog unavailable'; });
