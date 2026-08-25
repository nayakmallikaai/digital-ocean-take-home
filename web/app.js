const form = document.querySelector('#compare-form');
const picker = document.querySelector('#model-picker');
const promptBox = document.querySelector('#prompt');
const promptFields = document.querySelector('#prompt-fields');
const evaluationFields = document.querySelector('#evaluation-fields');
const runButton = document.querySelector('#run-button');
const runButtonLabel = document.querySelector('#run-button-label');
const runSection = document.querySelector('#run-section');
const runEyebrow = document.querySelector('#run-eyebrow');
const runTitle = document.querySelector('#run-title');
const grid = document.querySelector('#result-grid');
const progress = document.querySelector('#progress');
const comparison = document.querySelector('#comparison');
const errorBox = document.querySelector('#form-error');
const historyList = document.querySelector('#history-list');
const HISTORY_KEY = 'model-fomo-recent-v1';
const MAX_HISTORY = 6;
let config;

const esc = (value = '') => String(value).replace(/[&<>'"]/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[character]));
const mode = () => document.querySelector('input[name="run-mode"]:checked').value;
const selected = () => [...picker.querySelectorAll('select')].map(select => select.value).filter(Boolean);

function updateSelection() {
  const models = selected();
  const count = models.length;
  document.querySelector('#selection-count').textContent = `${count} selected`;
  picker.querySelectorAll('select').forEach(select => [...select.options].forEach(option => {
    option.disabled = Boolean(option.value && option.value !== select.value && models.includes(option.value));
  }));
  runButton.disabled = mode() === 'evaluate' ? count !== 3 : count === 0 || new Set(models).size !== count;
}

function updateMode() {
  const evaluating = mode() === 'evaluate';
  promptFields.hidden = evaluating;
  evaluationFields.hidden = !evaluating;
  promptBox.required = !evaluating;
  runButtonLabel.textContent = evaluating ? 'Run 15-prompt evaluation' : 'Compare models';
  errorBox.textContent = evaluating && !config?.evaluation_configured
    ? 'Add DIGITALOCEAN_TOKEN on the server to enable native DigitalOcean Evaluations.' : '';
  updateSelection();
}

function pendingCard(model) {
  return `<article class="card" id="card-${CSS.escape(model)}"><div class="card-head"><h3>${esc(model)}</h3><span class="badge running">Running</span></div><div class="metrics"><span>Latency —</span><span>Tokens —</span></div><div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton" style="width:60%"></div></div></article>`;
}

function evaluationCard(model) {
  return `<article class="card eval-card" id="eval-${CSS.escape(model)}"><div class="card-head"><h3>${esc(config.model_labels?.[model] || model)}</h3><span class="badge running">Preparing</span></div><div class="empty">Waiting for DigitalOcean Evaluations…</div></article>`;
}

function renderResult(result) {
  const card = document.getElementById(`card-${CSS.escape(result.model)}`);
  const className = result.status.toLowerCase().replaceAll(' ', '-');
  const latency = result.latency_seconds == null ? 'n/a' : `${result.latency_seconds.toFixed(2)}s`;
  const usage = result.prompt_tokens == null && result.completion_tokens == null
    ? 'Tokens n/a' : `In ${result.prompt_tokens ?? 'n/a'} · Out ${result.completion_tokens ?? 'n/a'}`;
  card.innerHTML = `<div class="card-head"><h3>${esc(result.model)}</h3><span class="badge ${className}">${esc(result.status)}</span></div><div class="metrics"><span>${latency}</span><span>${usage}</span></div>${result.answer ? `<div class="answer">${esc(result.answer)}</div><button class="copy" type="button">Copy response</button>` : `<div class="empty">${esc(result.error || 'No usable answer was returned.')}</div>`}`;
  if (result.answer) card.querySelector('.copy').onclick = async event => {
    await navigator.clipboard.writeText(result.answer);
    event.target.textContent = 'Copied';
    setTimeout(() => { event.target.textContent = 'Copy response'; }, 1200);
  };
}

function renderComparison(result) {
  comparison.className = 'comparison';
  comparison.hidden = false;
  comparison.innerHTML = `<p class="eyebrow">COMBINED COMPARISON</p><h2>What happened in this run</h2><div class="compare-metrics"><div><small>Fastest successful model</small><strong>${result.fastest_model ? `${esc(result.fastest_model)} · ${result.fastest_seconds.toFixed(2)}s` : 'Not available'}</strong></div><div><small>Shortest successful output</small><strong>${result.shortest_model ? `${esc(result.shortest_model)} · ${result.shortest_output_tokens} tokens` : 'Not available'}</strong></div></div><p class="recommendation">${esc(result.recommendation)}</p>`;
}

function readableEvalStatus(status) {
  return status.replace('MODEL_EVALUATION_RUN_', '').replaceAll('_', ' ').toLowerCase().replace(/\b\w/g, character => character.toUpperCase());
}

function renderEvaluationProgress(result) {
  const card = document.getElementById(`eval-${CSS.escape(result.model)}`);
  const terminal = ['MODEL_EVALUATION_RUN_SUCCESSFUL', 'MODEL_EVALUATION_RUN_PARTIALLY_SUCCESSFUL'].includes(result.status);
  const failed = ['MODEL_EVALUATION_RUN_FAILED', 'MODEL_EVALUATION_RUN_CANCELLED'].includes(result.status);
  const className = terminal ? 'complete' : failed ? 'failed' : 'running';
  card.innerHTML = `<div class="card-head"><h3>${esc(config.model_labels?.[result.model] || result.model)}</h3><span class="badge ${className}">${esc(readableEvalStatus(result.status))}</span></div><div class="metrics"><span>${esc(result.rows_evaluated)} of ${esc(result.total_rows)} judged</span></div><div class="empty">${esc(result.error || (terminal ? 'Aggregate results ready.' : 'DigitalOcean is generating and judging responses…'))}</div>`;
}

function renderEvaluationReport(report) {
  comparison.className = 'evaluation-report';
  comparison.hidden = false;
  const rows = report.models.map(result => `<tr><td>${esc(config.model_labels?.[result.model] || result.model)}</td><td>${esc(readableEvalStatus(result.status))}</td><td>${result.overall_score_percent == null ? 'n/a' : `${esc(result.overall_score_percent)}%`}</td><td>${result.duration_seconds == null ? 'n/a' : `${esc(result.duration_seconds)}s`}</td><td>${esc(result.rows_evaluated)}/${esc(result.total_rows)}</td></tr>`).join('');
  comparison.innerHTML = `<p class="eyebrow">DIGITALOCEAN EVALUATION REPORT</p><h2>15-prompt aggregate results</h2><table class="evaluation-table"><thead><tr><th>Model</th><th>Status</th><th>Overall score</th><th>Duration</th><th>Judged rows</th></tr></thead><tbody>${rows}</tbody></table><p class="recommendation">${esc(report.summary)}</p><p class="evaluation-disclaimer">Judge: ${esc(config.model_labels?.[report.judge_model] || report.judge_model)} · Metrics: ${esc(report.metric_names.join(', '))}. Scores are advisory and must be calibrated with human review. Per-prompt responses and judge rationale are intentionally not displayed.</p>`;
}

async function readEvents(response, onEvent) {
  if (!response.ok) {
    const body = await response.json();
    throw new Error(body.error || 'The run could not start.');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, {stream: true});
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) if (line) onEvent(JSON.parse(line));
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer));
}

function readHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); } catch { return []; }
}

function saveHistory(prompt, models, statuses) {
  const items = readHistory();
  items.unshift({prompt, models, timestamp: new Date().toISOString(), statuses});
  localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, MAX_HISTORY)));
  renderHistory();
}

function renderHistory() {
  const items = readHistory();
  historyList.innerHTML = items.length ? items.map(item => `<div class="history-item"><time datetime="${esc(item.timestamp)}">${new Date(item.timestamp).toLocaleString()}</time><div class="history-prompt" title="${esc(item.prompt)}">${esc(item.prompt)}</div><div class="history-status">${item.models.map((model, index) => `${esc(model)}: ${esc(item.statuses[index] || 'Unknown')}`).join(' · ')}</div></div>`).join('') : '<div class="history-empty">Your completed comparisons will appear here. Nothing is sent to a history database.</div>';
}

async function startComparison(models) {
  const prompt = promptBox.value.trim();
  if (!prompt) return;
  runEyebrow.textContent = 'LIVE RESULTS';
  runTitle.textContent = 'Models run in parallel';
  grid.innerHTML = models.map(pendingCard).join('');
  progress.textContent = `0 of ${models.length} finished`;
  const statuses = {};
  let done = 0;
  await readEvents(await fetch('/api/compare', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt, models})}), event => {
    if (event.type === 'result') {
      renderResult(event.result);
      statuses[event.result.model] = event.result.status;
      progress.textContent = `${++done} of ${models.length} finished`;
    } else if (event.type === 'comparison') renderComparison(event.comparison);
  });
  saveHistory(prompt, models, models.map(model => statuses[model] || 'Failed'));
}

async function startEvaluation(models) {
  runEyebrow.textContent = 'DIGITALOCEAN EVALUATIONS';
  runTitle.textContent = 'Evaluating 15 prompts across 3 models';
  grid.innerHTML = models.map(evaluationCard).join('');
  progress.textContent = 'Preparing dataset…';
  let report;
  await readEvents(await fetch('/api/evaluate', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({models})}), event => {
    if (event.type === 'evaluation_phase') progress.textContent = event.phase;
    else if (event.type === 'evaluation_started') progress.textContent = 'Native evaluation runs started';
    else if (event.type === 'evaluation_progress') renderEvaluationProgress(event.result);
    else if (event.type === 'evaluation_report') { report = event.report; renderEvaluationReport(report); progress.textContent = 'Evaluation complete'; }
    else if (event.type === 'evaluation_error') throw new Error(event.error);
  });
  if (!report) throw new Error('DigitalOcean Evaluations ended without a report.');
  saveHistory('15-prompt DigitalOcean Evaluation', models, report.models.map(result => readableEvalStatus(result.status)));
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  const models = selected();
  if (!models.length || (mode() === 'evaluate' && models.length !== 3)) return;
  runButton.disabled = true;
  errorBox.textContent = '';
  runSection.hidden = false;
  comparison.hidden = true;
  runSection.scrollIntoView({behavior: 'smooth', block: 'start'});
  try {
    if (mode() === 'evaluate') await startEvaluation(models);
    else await startComparison(models);
  } catch (error) {
    errorBox.textContent = error.message;
    progress.textContent = 'Run stopped';
  } finally {
    updateSelection();
  }
});

async function start() {
  try {
    config = await fetch('/api/config').then(response => response.json());
    picker.innerHTML = Array.from({length: config.max_selected}, (_, slot) => `<label class="model-select"><span>Model ${slot + 1}</span><select aria-label="Model ${slot + 1}"><option value="">No model</option>${config.models.map(model => `<option value="${esc(model)}" ${config.defaults[slot] === model ? 'selected' : ''}>${esc(config.model_labels?.[model] || model)}</option>`).join('')}</select></label>`).join('');
    document.querySelector('#cutoff-note').textContent = `Single-prompt comparisons allow ${config.timeout_seconds} seconds per model. Evaluations require exactly 3 models.`;
    picker.addEventListener('change', updateSelection);
    document.querySelectorAll('input[name="run-mode"]').forEach(input => input.addEventListener('change', updateMode));
    updateMode();
  } catch {
    errorBox.textContent = 'Could not load model configuration.';
  }
  renderHistory();
}

document.querySelector('#clear-history').onclick = () => { localStorage.removeItem(HISTORY_KEY); renderHistory(); };
start();
