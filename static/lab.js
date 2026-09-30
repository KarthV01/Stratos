const studiesRoot = document.querySelector('#studies');
const navigation = document.querySelector('#study-nav');
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
})[character]);
const playbackTimers = new WeakMap();
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function renderStudy(study, index) {
  const number = String(index + 1).padStart(2, '0');
  const exchanges = study.steps.map((step, stepIndex) => `
    <div class="exchange" data-tone="${escapeHtml(step.tone)}" style="--step:${stepIndex}">
      <span class="actor">${escapeHtml(step.from)}</span>
      <span class="transfer" aria-hidden="true"></span>
      <span class="actor">${escapeHtml(step.to)}</span>
      <span class="exchange-message">${escapeHtml(step.message)}</span>
    </div>`).join('');
  return `
    <article class="study" id="${escapeHtml(study.id)}" data-study="${escapeHtml(study.id)}">
      <div class="study-meta"><span>Study ${number} / ${escapeHtml(study.category)}</span><span>${escapeHtml(study.difficulty)}</span></div>
      <h2>${escapeHtml(study.title)}</h2>
      <p class="study-summary">${escapeHtml(study.summary)}</p>
      <p class="study-problem">${escapeHtml(study.problem)}</p>
      <section class="demonstration" aria-label="${escapeHtml(study.title)} message flow demonstration">
        <div class="demo-head"><h3>Message flow</h3><span class="demo-state"><i></i><b>Waiting for scroll</b></span></div>
        <div class="exchanges">${exchanges}</div>
        <div class="legend"><span><i></i>handoff</span><span class="failed"><i></i>failure</span><span class="guarded"><i></i>failsafe</span></div>
        <div class="solution">
          <div class="safeguards"><h3>Failsafes to code into the system</h3><ol>${study.safeguards.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ol></div>
          <div class="guarantee"><h3>The resulting guarantee</h3><p>${escapeHtml(study.guarantee)}</p></div>
        </div>
      </section>
    </article>`;
}

function playStudy(article) {
  if (article.classList.contains('playing') || article.classList.contains('played')) return;
  const rows = [...article.querySelectorAll('.exchange')];
  const state = article.querySelector('.demo-state b');
  if (prefersReducedMotion) {
    rows.forEach(row => row.classList.add('revealed'));
    article.classList.add('played');
    state.textContent = 'Flow explained';
    return;
  }
  article.classList.add('playing');
  state.textContent = 'Following handoffs';
  let step = 0;
  const timer = window.setInterval(() => {
    rows.forEach(row => row.classList.remove('current'));
    if (step < rows.length) {
      rows[step].classList.add('revealed', 'current');
      step += 1;
      return;
    }
    window.clearInterval(timer);
    rows.at(-1)?.classList.remove('current');
    article.classList.remove('playing');
    article.classList.add('played');
    state.textContent = 'Failsafe applied';
  }, 620);
  playbackTimers.set(article, timer);
}

function installObservers() {
  const articles = [...document.querySelectorAll('.study')];
  const links = [...document.querySelectorAll('.study-nav-link')];
  const playbackObserver = new IntersectionObserver(entries => {
    entries.filter(entry => entry.isIntersecting).forEach(entry => playStudy(entry.target));
  }, {threshold: 0.28});
  articles.forEach(article => playbackObserver.observe(article));

  const navigationObserver = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      links.forEach(link => link.classList.toggle('active', link.hash === `#${entry.target.id}`));
    }
  }, {rootMargin: '-25% 0px -62% 0px'});
  articles.forEach(article => navigationObserver.observe(article));
}

fetch('/api/lab/challenges')
  .then(response => {
    if (!response.ok) throw new Error('Field guide unavailable');
    return response.json();
  })
  .then(studies => {
    navigation.innerHTML = studies.map((study, index) => `
      <a class="study-nav-link" href="#${escapeHtml(study.id)}"><span>${String(index + 1).padStart(2, '0')}</span>${escapeHtml(study.title)}</a>`).join('');
    studiesRoot.innerHTML = studies.map(renderStudy).join('');
    installObservers();
  })
  .catch(() => {
    studiesRoot.innerHTML = '<div class="guide-loading">The field guide could not be loaded.</div>';
  });
