// Claude Code statusline. Reads the session JSON on stdin, prints one line.
// Surfaces context utilisation so the 40-60% working range is visible at a glance,
// plus the model and effort level, which are both prompt-cache keys and should not
// be changed mid-session.
let buf = '';
process.stdin.on('data', d => (buf += d));
process.stdin.on('end', () => {
  let j;
  try { j = JSON.parse(buf); } catch { process.stdout.write(''); return; }

  const model = (j.model && j.model.display_name) || 'model';
  const effort = (j.effort && j.effort.level) || '?';
  const cw = j.context_window || {};
  const pct = Math.round(cw.used_percentage || 0);

  // Ten-cell meter. The 40-60% band is where planning quality holds up best;
  // past ~60% recall starts degrading well before the window is actually full.
  const filled = Math.min(10, Math.round(pct / 10));
  const meter = '#'.repeat(filled) + '-'.repeat(10 - filled);

  const parts = [`[${model} ${effort}]`, `ctx ${meter} ${pct}%`];

  if (j.exceeds_200k_tokens) parts.push('>200k');

  const rl = j.rate_limits || {};
  if (rl.five_hour && typeof rl.five_hour.used_percentage === 'number') {
    parts.push(`5h ${Math.round(rl.five_hour.used_percentage)}%`);
  }
  if (rl.seven_day && typeof rl.seven_day.used_percentage === 'number') {
    parts.push(`7d ${Math.round(rl.seven_day.used_percentage)}%`);
  }

  process.stdout.write(parts.join('  '));
});
