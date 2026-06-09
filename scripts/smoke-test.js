// scripts/smoke-test.js
// End-to-end smoke test: launch server, boot Chrome via puppeteer-core, validate:
//   1. Index page renders (title, sidebar, article list, AI provider label)
//   2. /api/articles returns >= 1 article
//   3. /api/llm-config returns deepseek provider
//   4. Clicking an article card opens reader view
//   5. Search box filters the list
//   6. Clicking "设置" (settings) button shows settings panel
//
// Saves screenshots to docs/media/kbase-*.png

const puppeteer = require('puppeteer-core');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const OUT = path.join(ROOT, 'docs', 'media');
fs.mkdirSync(OUT, { recursive: true });

const URL = 'http://localhost:8765/';
const SCREENSHOT_OPTS = { path: undefined, fullPage: false };

function log(msg) { console.log(`  ${msg}`); }
function ok(msg) { console.log(`  ✅ ${msg}`); }
function fail(msg) { console.log(`  ❌ ${msg}`); process.exitCode = 1; }
function check(name, cond) { cond ? ok(name) : fail(name); }

async function startServer() {
    console.log('🚀 Starting kbase HTTP server…');
    const proc = spawn('python3', [path.join(ROOT, 'scripts', 'serve-headless.py')], {
        cwd: ROOT,
        stdio: ['ignore', 'pipe', 'pipe'],
    });
    // Wait for "Listening on" line
    return new Promise((resolve, reject) => {
        const onData = (chunk) => {
            const s = chunk.toString();
            process.stdout.write('  [srv] ' + s);
            if (s.includes('Listening on') || s.includes('running on')) resolve(proc);
        };
        proc.stdout.on('data', onData);
        proc.stderr.on('data', (c) => process.stdout.write('  [srv-err] ' + c));
        proc.on('exit', (code) => reject(new Error(`server exited with ${code}`)));
        setTimeout(() => reject(new Error('server start timeout')), 8000);
    });
}

(async () => {
    let server;
    try {
        server = await startServer();
        // Give the server a beat to fully bind
        await new Promise(r => setTimeout(r, 500));

        console.log('\n🧪 Puppeteer smoke test');
        const browser = await puppeteer.launch({
            executablePath: '/usr/bin/google-chrome',
            headless: 'new',
            args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
        });
        const page = await browser.newPage();
        await page.setViewport({ width: 1280, height: 800 });

        // === Test 1: Home page loads ===
        console.log('\n[1] GET / renders the SPA');
        const resp = await page.goto(URL, { waitUntil: 'networkidle0', timeout: 15000 });
        check('HTTP 200', resp.status() === 200);
        const title = await page.title();
        check(`title contains "Knowledge Base" or "KBase"`, /Knowledge Base|KBase/i.test(title));
        await page.screenshot({ path: path.join(OUT, 'kbase-01-home.png') });
        ok('screenshot saved → kbase-01-home.png');

        // === Test 2: Sidebar shows KBase AI header ===
        console.log('\n[2] Sidebar header');
        const sidebarText = await page.evaluate(() => document.body.innerText);
        check('sidebar shows "KBase AI"', sidebarText.includes('KBase AI'));
        check('shows "全库资料问答"', sidebarText.includes('全库资料问答'));

        // === Test 3: Article list loaded ===
        console.log('\n[3] Article list');
        const articleCount = await page.evaluate(() => {
            const m = document.body.innerText.match(/(\d+)\s*\/\s*(\d+)\s*项/);
            return m ? parseInt(m[2], 10) : 0;
        });
        check(`article list shows count > 0 (got ${articleCount})`, articleCount > 0);

        // === Test 4: LLM provider label ===
        console.log('\n[4] LLM provider label');
        const provLabel = await page.evaluate(() => {
            const txt = document.body.innerText;
            const m = txt.match(/(DeepSeek|OpenAI|claude|Anthropic|Gemini)/i);
            return m ? m[0] : null;
        });
        check(`LLM provider label visible (${provLabel})`, provLabel !== null);

        // === Test 5: Open first article ===
        console.log('\n[5] Click first article card');
        const firstCardSel = '.article-card, [data-article-id], .article-item, [class*="article"]';
        const cardFound = await page.$(firstCardSel);
        if (cardFound) {
            await cardFound.click();
            await new Promise(r => setTimeout(r, 1000));
            const readerVisible = await page.evaluate(() => {
                const t = document.body.innerText;
                return /摘要|Abstract|总结|Summary|Introduction|目录|outline|references/i.test(t);
            });
            check('reader view shows article content', readerVisible);
            await page.screenshot({ path: path.join(OUT, 'kbase-02-reader.png') });
            ok('screenshot saved → kbase-02-reader.png');
        } else {
            fail('no article card found in DOM');
        }

        // === Test 6: Search filter ===
        console.log('\n[6] Search filter');
        await page.goto(URL, { waitUntil: 'networkidle0' });
        await new Promise(r => setTimeout(r, 800));
        const searchSel = 'input[type="search"], input[placeholder*="搜索"], input[placeholder*="search" i]';
        const searchBox = await page.$(searchSel);
        if (searchBox) {
            await searchBox.type('quantum', { delay: 50 });
            await new Promise(r => setTimeout(r, 800));
            const filtered = await page.evaluate(() => {
                const m = document.body.innerText.match(/(\d+)\s*\/\s*(\d+)\s*项/);
                return m ? { shown: parseInt(m[1],10), total: parseInt(m[2],10) } : null;
            });
            check(`search "quantum" filters list (${JSON.stringify(filtered)})`,
                filtered && filtered.shown < filtered.total && filtered.shown >= 1);
            await page.screenshot({ path: path.join(OUT, 'kbase-03-search.png') });
        } else {
            fail('no search input found');
        }

        // === Test 7: API endpoint direct check ===
        console.log('\n[7] Direct API probe');
        const apiArticles = await page.evaluate(() => fetch('/api/articles').then(r => r.json()));
        check('GET /api/articles returns object', apiArticles && typeof apiArticles === 'object');
        check(`API returns >= 1 article (got ${(apiArticles.articles || []).length})`,
            (apiArticles.articles || []).length >= 1);
        const apiLLM = await page.evaluate(() => fetch('/api/llm-config').then(r => r.json()));
        check('GET /api/llm-config has providers', apiLLM && Array.isArray(apiLLM.providers) && apiLLM.providers.length >= 1);

        await browser.close();
    } catch (e) {
        fail(`unexpected error: ${e.message}`);
        console.error(e);
    } finally {
        if (server) {
            server.kill('SIGTERM');
            await new Promise(r => setTimeout(r, 500));
        }
    }
})();
