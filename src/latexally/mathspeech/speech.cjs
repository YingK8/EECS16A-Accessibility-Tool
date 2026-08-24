// MathML -> speech, one long-lived process for the whole batch.
//
// The `sre` CLI accepts one <math> per invocation, so driving it from the shell
// would mean ~35k process spawns. The library API takes the same input and
// costs one startup, which is the difference between minutes and hours.
//
// Protocol is JSON Lines both ways -- {"hash","mathml"} in, {"hash","speech"}
// or {"hash","error"} out -- so one formula SRE chokes on cannot take down the
// batch. CommonJS on purpose: speech-rule-engine has no working ESM entry.

const sre = require('speech-rule-engine');

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => (data += chunk));
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

(async () => {
  const domain = process.argv[2] || 'clearspeak';
  const locale = process.argv[3] || 'en';
  await sre.setupEngine({ domain, style: 'default', locale, modality: 'speech' });

  const out = [];
  for (const line of (await readStdin()).split('\n')) {
    if (!line.trim()) continue;
    const { hash, mathml } = JSON.parse(line);
    try {
      out.push(JSON.stringify({ hash, speech: sre.toSpeech(mathml) }));
    } catch (error) {
      out.push(JSON.stringify({ hash, error: String((error && error.message) || error) }));
    }
  }
  process.stdout.write(out.join('\n') + '\n');
})();
