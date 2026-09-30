const tasks = document.querySelector('#tasks');
const counts = document.querySelector('#task-counts');
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
})[character]);

async function refresh() {
  const response = await fetch('/api/tasks');
  const data = await response.json();
  const completed = data.filter(task => task.status === 'completed').length;
  const active = data.filter(task => ['queued', 'running'].includes(task.status)).length;
  counts.innerHTML = `<span><strong>${active}</strong> active</span><span><strong>${completed}</strong> completed</span>`;
  tasks.innerHTML = data.length ? data.map(task => `
    <article class="task-card">
      <span class="status ${escapeHtml(task.status)}">${escapeHtml(task.status)}</span>
      <div class="task-message">
        <div>${escapeHtml(task.payload.message)}</div>
        <small>${escapeHtml(task.id.slice(0, 8))}${task.result ? ` · ${escapeHtml(task.result.reply)}` : task.error ? ` · ${escapeHtml(task.error)}` : ''}</small>
      </div>
      <div class="task-worker">${escapeHtml(task.worker || 'unassigned')}</div>
    </article>`).join('') : '<div class="empty-state">No tasks have crossed the broker yet.</div>';
}

document.querySelector('#form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  button.disabled = true;
  try {
    const response = await fetch('/api/tasks', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({
        message: document.querySelector('#message').value,
        delay_seconds: Number(document.querySelector('#delay').value),
      }),
    });
    if (!response.ok) throw new Error('Task submission failed');
    await refresh();
  } finally {
    button.disabled = false;
  }
});

const source = new EventSource('/api/events');
source.onopen = () => {
  document.querySelector('#connection').classList.add('connected');
  document.querySelector('#connection span').textContent = 'System live';
};
source.onerror = () => {
  document.querySelector('#connection').classList.remove('connected');
  document.querySelector('#connection span').textContent = 'Reconnecting';
};
source.onmessage = refresh;
refresh();
