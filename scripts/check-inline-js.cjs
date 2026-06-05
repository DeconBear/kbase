const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync('kb/index.html', 'utf8');
const scriptPattern = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
let match;
let count = 0;
const failures = [];

while ((match = scriptPattern.exec(html))) {
  count += 1;
  try {
    new vm.Script(match[1], { filename: `kb/index.html:inline-script-${count}.js` });
  } catch (error) {
    failures.push(`inline script ${count}: ${error.message}`);
  }
}

if (failures.length) {
  console.error(failures.join('\n'));
  process.exit(1);
}

console.log(`checked ${count} inline scripts`);
